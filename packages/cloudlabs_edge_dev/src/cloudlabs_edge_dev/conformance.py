"""Edge Contract v1 conformance runner (HTTP + WebSocket checks)."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx
import jsonschema
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from websockets.sync.client import connect as ws_connect

from cloudlabs_edge_dev import CONTRACT_VERSION
from cloudlabs_edge_dev.schemas_path import load_schema

PROFILES = ("stub", "skeleton", "hardware")

_SCHEMA_FILES = (
    "capabilities.schema.json",
    "bench.schema.json",
    "execute_request.schema.json",
    "execute_response.schema.json",
    "measurable_live_decl.schema.json",
    "teleop_ws_client.schema.json",
    "teleop_ws_server.schema.json",
    "epoch_packet.schema.json",
)


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class Report:
    base_url: str
    profile: str
    results: list[CheckResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(r.ok for r in self.results)

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.results.append(CheckResult(name=name, ok=ok, detail=detail))

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "base_url": self.base_url,
            "profile": self.profile,
            "checks": [
                {"name": r.name, "ok": r.ok, "detail": r.detail} for r in self.results
            ],
        }

    def print(self, file=sys.stdout) -> None:
        status = "PASS" if self.ok else "FAIL"
        print(f"[{status}] edge conformance profile={self.profile} url={self.base_url}", file=file)
        for r in self.results:
            mark = "ok" if r.ok else "FAIL"
            extra = f" - {r.detail}" if r.detail else ""
            line = f"  [{mark}] {r.name}{extra}"
            try:
                print(line, file=file)
            except UnicodeEncodeError:
                print(line.encode("ascii", "replace").decode("ascii"), file=file)


def _registry() -> Registry:
    registry: Registry = Registry()
    for name in _SCHEMA_FILES:
        try:
            schema = load_schema(name)
        except FileNotFoundError:
            continue
        resource = Resource.from_contents(schema)
        uri = schema.get("$id") or name
        registry = registry.with_resource(uri, resource)
        registry = registry.with_resource(name, resource)
    return registry


def _validate(instance: Any, schema_name: str) -> str | None:
    try:
        schema = load_schema(schema_name)
        Draft202012Validator(schema, registry=_registry()).validate(instance)
        return None
    except jsonschema.ValidationError as e:
        return e.message
    except Exception as e:  # noqa: BLE001 — surface tool errors cleanly
        return str(e)


def _ws_url(http_base: str, path: str) -> str:
    u = urlparse(http_base)
    scheme = "wss" if u.scheme == "https" else "ws"
    if not path.startswith("/"):
        path = "/" + path
    return f"{scheme}://{u.netloc}{path}"


def _execute(client: httpx.Client, primitive: str, args: dict | None = None) -> httpx.Response:
    return client.post("/execute", json={"primitive": primitive, "args": args or {}})


def run_conformance(base_url: str, profile: str = "stub") -> Report:
    if profile not in PROFILES:
        raise ValueError(f"unknown profile {profile!r}; choose from {PROFILES}")

    base = base_url.rstrip("/") + "/"
    report = Report(base_url=base_url.rstrip("/"), profile=profile)

    with httpx.Client(base_url=base, timeout=10.0) as client:
        # --- capabilities ---
        try:
            caps_resp = client.get("capabilities")
            caps_resp.raise_for_status()
            caps = caps_resp.json()
        except Exception as e:  # noqa: BLE001
            report.add("GET /capabilities reachable", False, str(e))
            return report

        err = _validate(caps, "capabilities.schema.json")
        report.add("GET /capabilities schema", err is None, err or "")

        cv = caps.get("contract_version")
        report.add(
            "contract_version pin",
            cv == CONTRACT_VERSION,
            f"got {cv!r}, want {CONTRACT_VERSION!r}",
        )

        primitives = set(caps.get("supported_primitives") or [])
        channels = caps.get("telemetry_channels") or {}

        # --- bench ---
        try:
            bench_resp = client.get("bench")
            bench_resp.raise_for_status()
            bench = bench_resp.json()
            err = _validate(bench, "bench.schema.json")
            report.add("GET /bench schema", err is None, err or "")
            if caps.get("backend_id") and bench.get("backend_id"):
                report.add(
                    "backend_id match",
                    caps["backend_id"] == bench["backend_id"],
                    f"caps={caps.get('backend_id')!r} bench={bench.get('backend_id')!r}",
                )
        except Exception as e:  # noqa: BLE001
            report.add("GET /bench reachable", False, str(e))

        # --- refuse unknown ---
        try:
            bad = _execute(client, "__NOT_A_PRIMITIVE__", {})
            body = bad.json()
            err = _validate(body, "execute_response.schema.json")
            status_ok = body.get("status") in ("refused", "failed")
            report.add(
                "unknown primitive -> structured refuse",
                status_ok and err is None,
                err or f"status={body.get('status')!r}",
            )
        except Exception as e:  # noqa: BLE001
            report.add("unknown primitive -> structured refuse", False, str(e))

        if profile == "skeleton":
            return report

        # --- live feed arming ---
        live_ch = None
        for name, ch in channels.items():
            if ch.get("requires_primitive") == "START_LIVE_FEED":
                live_ch = name
                break
        if live_ch is None and "START_LIVE_FEED" in primitives:
            live_ch = "tag_22.camera_image"

        if "START_LIVE_FEED" in primitives and live_ch:
            ch_meta = channels.get(live_ch) or {}
            path = ch_meta.get("path") or "/stream/tag_22/camera_image.jpg"
            try:
                # Before arming: expect failure or non-image.
                pre = client.get(path.lstrip("/"))
                armed_blocked = pre.status_code >= 400 or not (
                    pre.headers.get("content-type", "").startswith("image/jpeg")
                    or pre.content[:2] == b"\xff\xd8"
                )
                report.add(
                    "live stream blocked before START_LIVE_FEED",
                    armed_blocked,
                    f"status={pre.status_code} ct={pre.headers.get('content-type')}",
                )

                start = _execute(
                    client,
                    "START_LIVE_FEED",
                    {"channel": live_ch, "measurable_id": live_ch},
                )
                start_body = start.json()
                err = _validate(start_body, "execute_response.schema.json")
                report.add(
                    "START_LIVE_FEED completed",
                    start_body.get("status") == "completed" and err is None,
                    err or f"status={start_body.get('status')!r}",
                )

                frame = client.get(path.lstrip("/"))
                jpeg_ok = frame.status_code == 200 and (
                    frame.headers.get("content-type", "").startswith("image/jpeg")
                    or frame.content[:2] == b"\xff\xd8"
                )
                report.add(
                    "live JPEG after START_LIVE_FEED",
                    jpeg_ok,
                    f"status={frame.status_code} bytes={len(frame.content)}",
                )

                end = _execute(
                    client,
                    "END_LIVE_FEED",
                    {"channel": live_ch, "measurable_id": live_ch},
                )
                end_body = end.json()
                report.add(
                    "END_LIVE_FEED completed",
                    end_body.get("status") == "completed",
                    f"status={end_body.get('status')!r}",
                )

                post = client.get(path.lstrip("/"))
                stopped = post.status_code >= 400 or not (
                    post.headers.get("content-type", "").startswith("image/jpeg")
                    and post.content[:2] == b"\xff\xd8"
                )
                report.add(
                    "live stream stopped after END_LIVE_FEED",
                    stopped,
                    f"status={post.status_code}",
                )
            except Exception as e:  # noqa: BLE001
                report.add("live feed roundtrip", False, str(e))
        else:
            report.add("live feed primitives present", False, "START_LIVE_FEED missing")

        # --- RECORD / EVAL epoch ---
        for prim in ("RECORD_MEASURABLES", "EVAL_KERNEL"):
            if prim not in primitives:
                if profile == "stub":
                    report.add(f"{prim} declared", False, "missing from supported_primitives")
                continue
            try:
                resp = _execute(
                    client,
                    prim,
                    {"tag_id": "tag_22", "kernel_id": "stub"} if prim == "EVAL_KERNEL" else {"tag_id": "tag_22"},
                )
                body = resp.json()
                err = _validate(body, "execute_response.schema.json")
                has_epoch = isinstance(body.get("epoch_ms"), int)
                report.add(
                    f"{prim} returns epoch_ms",
                    body.get("status") == "completed" and has_epoch and err is None,
                    err or f"status={body.get('status')!r} epoch_ms={body.get('epoch_ms')!r}",
                )
            except Exception as e:  # noqa: BLE001
                report.add(f"{prim} returns epoch_ms", False, str(e))

        # --- teleop WS flat ---
        teleop_path = "/ws/teleop"
        for name, ch in channels.items():
            if ch.get("transport") == "websocket" and ch.get("requires_primitive") == "START_TELEOP":
                teleop_path = ch.get("path") or teleop_path
                break

        if "START_TELEOP" in primitives:
            try:
                start = _execute(client, "START_TELEOP", {"tag_id": "tag_22"})
                start_body = start.json()
                report.add(
                    "START_TELEOP completed",
                    start_body.get("status") == "completed",
                    f"status={start_body.get('status')!r}",
                )

                ws_url = _ws_url(report.base_url, teleop_path)
                with ws_connect(ws_url, open_timeout=5, close_timeout=2) as ws:
                    # Nested must fail
                    ws.send(
                        json.dumps(
                            {
                                "cmd": "JOG",
                                "tag_id": "tag_22",
                                "payload": {"axis": "x", "val": 1.0},
                            }
                        )
                    )
                    nested_raw = ws.recv(timeout=5)
                    nested = json.loads(nested_raw)
                    nested_err = nested.get("kind") == "error" or nested.get("error_code") == "NESTED_ENVELOPE"
                    report.add(
                        "Tier A WS rejects nested payload",
                        nested_err,
                        f"got {nested!r}",
                    )

                    # Flat PING
                    ws.send(json.dumps({"cmd": "PING", "tag_id": "tag_22"}))
                    pong_raw = ws.recv(timeout=5)
                    pong = json.loads(pong_raw)
                    err = _validate(pong, "teleop_ws_server.schema.json")
                    report.add(
                        "Tier A WS flat PING->pong",
                        pong.get("kind") == "pong" and err is None,
                        err or f"got {pong!r}",
                    )

                    # Flat JOG
                    jog = {"cmd": "JOG", "tag_id": "tag_22", "axis": "x", "val": 0.5}
                    err_c = _validate(jog, "teleop_ws_client.schema.json")
                    ws.send(json.dumps(jog))
                    sample_raw = ws.recv(timeout=5)
                    sample = json.loads(sample_raw)
                    err_s = _validate(sample, "teleop_ws_server.schema.json")
                    report.add(
                        "Tier A WS flat JOG->pose_sample",
                        err_c is None
                        and err_s is None
                        and sample.get("kind") in (None, "pose_sample")
                        and isinstance(sample.get("epoch_ms"), int),
                        err_c or err_s or f"got {sample!r}",
                    )

                end = _execute(client, "END_TELEOP", {"tag_id": "tag_22"})
                end_body = end.json()
                report.add(
                    "END_TELEOP completed",
                    end_body.get("status") == "completed",
                    f"status={end_body.get('status')!r}",
                )
            except Exception as e:  # noqa: BLE001
                report.add("teleop WS roundtrip", False, str(e))
        else:
            report.add("START_TELEOP declared", False, "missing from supported_primitives")

        if profile == "hardware":
            # Phase 6+: motion must not be pure no-op. Stub always fails this.
            report.add(
                "hardware motion non-noop (not enforced for stub)",
                False,
                "hardware profile reserved for Phase 6+",
            )

    return report


def main_check(base_url: str, profile: str = "stub", *, as_json: bool = False) -> int:
    report = run_conformance(base_url, profile=profile)
    if as_json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        report.print()
    return 0 if report.ok else 1
