#!/usr/bin/env python3
"""Green-light preflight for the radial Cloud Labs simulation stack."""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import socket
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


STATUS_LABEL = {"ok": "OK", "warn": "WARN", "fail": "FAIL", "info": "INFO"}


@dataclass
class Check:
    group: str
    name: str
    status: str
    message: str
    detail: dict[str, Any] = field(default_factory=dict)
    action: str | None = None

    def as_dict(self) -> dict[str, Any]:
        result = {
            "group": self.group,
            "name": self.name,
            "status": self.status,
            "message": self.message,
            "detail": self.detail,
        }
        if self.action:
            result["action"] = self.action
        return result


class StatusCollector:
    def __init__(self) -> None:
        self.checks: list[Check] = []

    def add(
        self,
        group: str,
        name: str,
        status: str,
        message: str,
        *,
        detail: dict[str, Any] | None = None,
        action: str | None = None,
    ) -> None:
        self.checks.append(
            Check(group, name, status, message, detail or {}, action)
        )

    def overall(self) -> str:
        if any(check.status == "fail" for check in self.checks):
            return "fail"
        if any(check.status == "warn" for check in self.checks):
            return "warn"
        return "ok"


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def compact(value: object, *, limit: int = 260) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def file_age_s(path: Path) -> float | None:
    try:
        return max(0.0, time.time() - path.stat().st_mtime)
    except OSError:
        return None


def fmt_age(seconds: float | None) -> str:
    if seconds is None:
        return "missing"
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds / 60:.1f}m"
    return f"{seconds / 3600:.1f}h"


def run_command(args: list[str], *, timeout_s: float) -> tuple[int | None, str]:
    try:
        completed = subprocess.run(
            args,
            check=False,
            capture_output=True,
            timeout=timeout_s,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return None, str(exc)
    raw = completed.stdout + completed.stderr
    text = raw.decode("utf-8", errors="replace")
    return completed.returncode, text.replace("\r\n", "\n").strip()


def port_open(host: str, port: int) -> tuple[bool, str]:
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True, "listening"
    except OSError as exc:
        return False, str(exc)


def http_get(url: str, *, timeout_s: float = 8.0) -> tuple[bool, int | None, Any, str]:
    request = Request(url, headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=timeout_s) as response:
            body = response.read()
            text = body.decode("utf-8", errors="replace")
            try:
                payload = json.loads(text) if text else None
            except json.JSONDecodeError:
                payload = {"bytes": len(body)}
            return True, int(response.status), payload, text
    except HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = None
        return False, int(exc.code), payload, text
    except (URLError, TimeoutError, OSError) as exc:
        return False, None, None, str(exc)


def parse_edge_url(root: Path) -> str:
    path = root / "schemas" / "backends.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    for row in payload.get("backends") or []:
        if row.get("backend_id") == "sim.default":
            return str((row.get("edge") or {}).get("base_url") or "http://127.0.0.1:8120").rstrip("/")
    raise RuntimeError("sim.default is missing from schemas/backends.json")


def python_import_check(python: Path) -> tuple[bool, str]:
    if not python.is_file():
        return False, f"missing {python}"
    code = (
        "import importlib.util,json; "
        "mods=['mujoco','fastapi','uvicorn','numpy']; "
        "print(json.dumps({m:bool(importlib.util.find_spec(m)) for m in mods}))"
    )
    rc, output = run_command([str(python), "-c", code], timeout_s=10.0)
    if rc != 0:
        return False, output or "Python import probe failed"
    try:
        found = json.loads(output)
    except json.JSONDecodeError:
        return False, compact(output)
    missing = [name for name, present in found.items() if not present]
    return (
        (False, "missing Python modules: " + ", ".join(missing))
        if missing
        else (True, "required Python modules import")
    )


def process_start_time_utc(pid: int) -> dt.datetime | None:
    command = (
        f"$p=Get-Process -Id {int(pid)} -ErrorAction SilentlyContinue; "
        "if ($p) { $p.StartTime.ToUniversalTime().ToString('o') }"
    )
    rc, output = run_command(
        ["powershell", "-NoProfile", "-Command", command],
        timeout_s=5.0,
    )
    if rc != 0:
        return None
    try:
        value = dt.datetime.fromisoformat(output.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc)


def latest_runtime_source(root: Path) -> tuple[Path | None, dt.datetime | None]:
    candidates: list[Path] = []
    for source_root in (
        root / "simulation_edge" / "src" / "simulation_edge",
        root / "simulation_edge" / "cloudlabs_edge",
    ):
        if source_root.is_dir():
            candidates.extend(source_root.rglob("*.py"))
    candidates.append(root / "scripts" / "ops" / "run_simulation_edge_radial.ps1")
    existing = [path for path in candidates if path.is_file()]
    if not existing:
        return None, None
    newest = max(existing, key=lambda path: path.stat().st_mtime)
    return newest, dt.datetime.fromtimestamp(
        newest.stat().st_mtime,
        tz=dt.timezone.utc,
    )


def add_repo_checks(col: StatusCollector, root: Path) -> str:
    col.add("Repo", "cloud-labs path", "ok" if root.is_dir() else "fail", str(root))
    try:
        edge_url = parse_edge_url(root)
        col.add("Repo", "sim.default edge URL", "ok", edge_url)
    except Exception as exc:  # noqa: BLE001
        edge_url = "http://127.0.0.1:8120"
        col.add(
            "Repo",
            "sim.default edge URL",
            "fail",
            str(exc),
            action="Fix schemas/backends.json.",
        )
    python = root.parent / ".venv" / "Scripts" / "python.exe"
    ok, message = python_import_check(python)
    col.add(
        "Repo",
        "Python runtime",
        "ok" if ok else "fail",
        message,
        detail={"python": str(python)},
        action="Restore the workspace .venv and its requirements." if not ok else None,
    )
    return edge_url


def add_endpoint_check(
    col: StatusCollector,
    *,
    group: str,
    name: str,
    url: str,
    timeout_s: float = 8.0,
) -> Any:
    ok, status, payload, response_text = http_get(url, timeout_s=timeout_s)
    passed = bool(ok and status is not None and status < 400)
    col.add(
        group,
        name,
        "ok" if passed else "fail",
        f"HTTP {status}" if status is not None else compact(response_text),
        detail=payload if isinstance(payload, dict) else {},
    )
    return payload if passed else None


def add_edge_checks(col: StatusCollector, root: Path, edge_url: str) -> dict[str, Any]:
    open_ok, message = port_open("127.0.0.1", 8120)
    col.add(
        "Simulation Edge",
        "port 8120",
        "ok" if open_ok else "fail",
        message,
        action="Start scripts/ops/run_simulation_edge_radial.ps1 -Viewer." if not open_ok else None,
    )
    if not open_ok:
        return {}
    add_endpoint_check(col, group="Simulation Edge", name="/health", url=f"{edge_url}/health")
    add_endpoint_check(col, group="Simulation Edge", name="/capabilities", url=f"{edge_url}/capabilities")
    add_endpoint_check(col, group="Simulation Edge", name="/bench", url=f"{edge_url}/bench", timeout_s=35.0)
    state = add_endpoint_check(
        col,
        group="Simulation Edge",
        name="/lab-state",
        url=f"{edge_url}/lab-state",
        timeout_s=35.0,
    )
    simulator = (state or {}).get("simulator") if isinstance(state, dict) else {}
    simulator = simulator if isinstance(simulator, dict) else {}
    running = bool(simulator.get("running"))
    col.add(
        "MuJoCo",
        "process",
        "ok" if running else "fail",
        f"pid={simulator.get('pid')}" if running else str(simulator.get("last_error") or "not running"),
        detail=simulator,
        action="Restart the radial simulation edge terminal." if not running else None,
    )
    planner = str(simulator.get("planner") or "")
    col.add(
        "MuJoCo",
        "radial planner",
        "ok" if planner == "radial" else "fail",
        planner or "planner missing",
        action="Start simulation edge with run_simulation_edge_radial.ps1." if planner != "radial" else None,
    )
    viewer = bool(simulator.get("viewer"))
    col.add(
        "MuJoCo",
        "viewer",
        "ok" if viewer else "fail",
        "enabled" if viewer else "disabled",
        action="Restart simulation edge with the -Viewer switch." if not viewer else None,
    )
    last_error = simulator.get("last_error")
    col.add(
        "MuJoCo",
        "runtime errors",
        "ok" if not last_error else "fail",
        "none" if not last_error else compact(last_error),
    )
    log_path = Path(str(simulator.get("log_path") or ""))
    log_ok = bool(log_path.is_file())
    col.add(
        "MuJoCo",
        "current diagnostic log",
        "ok" if log_ok else "fail",
        f"{log_path} age={fmt_age(file_age_s(log_path))}" if log_ok else "missing",
    )
    pid = simulator.get("pid")
    if running and pid:
        started = process_start_time_utc(int(pid))
        source, modified = latest_runtime_source(root)
        fresh = bool(started and modified and started >= modified - dt.timedelta(seconds=2))
        col.add(
            "MuJoCo",
            "source freshness",
            "ok" if fresh else "fail",
            "running process uses current source" if fresh else "running process predates source edits",
            detail={
                "process_start_utc": started.isoformat() if started else None,
                "latest_source": str(source) if source else None,
                "latest_source_mtime_utc": modified.isoformat() if modified else None,
            },
            action="Restart the radial simulation edge terminal." if not fresh else None,
        )
    return simulator


def add_backend_checks(col: StatusCollector, backend_url: str) -> None:
    open_ok, message = port_open("127.0.0.1", 8000)
    col.add(
        "Cloud Labs",
        "backend port 8000",
        "ok" if open_ok else "fail",
        message,
        action="Start scripts/ops/run_cloud_labs_backend.ps1." if not open_ok else None,
    )
    if not open_ok:
        return
    add_endpoint_check(col, group="Cloud Labs", name="/api/backends", url=f"{backend_url}/api/backends")
    add_endpoint_check(
        col,
        group="Cloud Labs",
        name="sim.default catalog",
        url=f"{backend_url}/api/catalog?backend_id=sim.default",
        timeout_s=35.0,
    )
    mode = add_endpoint_check(
        col,
        group="Cloud Labs",
        name="sim.default runtime mode",
        url=f"{backend_url}/api/runtime-mode?backend_id=sim.default",
        timeout_s=35.0,
    )
    simulator = (mode or {}).get("simulator") if isinstance(mode, dict) else {}
    mirrored = bool(
        isinstance(simulator, dict)
        and simulator.get("running")
        and simulator.get("planner") == "radial"
    )
    col.add(
        "Cloud Labs",
        "radial runtime connected",
        "ok" if mirrored else "fail",
        "sim.default reports running radial MuJoCo" if mirrored else "sim.default is not connected to radial MuJoCo",
    )
    add_endpoint_check(
        col,
        group="Cloud Labs",
        name="Twin UI",
        url=f"{backend_url}/twin",
    )


def render_console(checks: list[Check], overall: str) -> str:
    lines = ["Cloud Labs Radial Debug Status", f"Overall: {STATUS_LABEL[overall]}", ""]
    current_group = None
    for check in checks:
        if check.group != current_group:
            current_group = check.group
            lines.append(f"{current_group}:")
        lines.append(f"  [{STATUS_LABEL[check.status]:<4}] {check.name}: {check.message}")
        if check.action and check.status in {"fail", "warn"}:
            lines.append(f"         action: {check.action}")
    return "\n".join(lines)


def render_html(checks: list[Check], overall: str) -> str:
    rows = "".join(
        "<tr>"
        f"<td>{html.escape(check.group)}</td>"
        f"<td class='{check.status}'>{html.escape(STATUS_LABEL[check.status])}</td>"
        f"<td>{html.escape(check.name)}</td>"
        f"<td>{html.escape(check.message)}</td>"
        f"<td>{html.escape(check.action or '')}</td>"
        "</tr>"
        for check in checks
    )
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Cloud Labs Radial Debug Status</title>
<style>
body{{font:14px system-ui;margin:24px;background:#111827;color:#e5e7eb}}
table{{border-collapse:collapse;width:100%}}th,td{{padding:8px;border-bottom:1px solid #374151;text-align:left}}
.ok{{color:#4ade80}}.warn{{color:#facc15}}.fail{{color:#f87171}}.info{{color:#60a5fa}}
</style></head><body><h1>Cloud Labs Radial Debug Status: {STATUS_LABEL[overall]}</h1>
<table><thead><tr><th>Group</th><th>Status</th><th>Check</th><th>Message</th><th>Action</th></tr></thead>
<tbody>{rows}</tbody></table></body></html>"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend-url", default="http://127.0.0.1:8000")
    parser.add_argument("--edge-url")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--html", action="store_true")
    args = parser.parse_args()

    root = repo_root()
    collector = StatusCollector()
    configured_edge_url = add_repo_checks(collector, root)
    edge_url = (args.edge_url or configured_edge_url).rstrip("/")
    add_edge_checks(collector, root, edge_url)
    add_backend_checks(collector, args.backend_url.rstrip("/"))

    overall = collector.overall()
    payload = {
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "overall": overall,
        "checks": [check.as_dict() for check in collector.checks],
    }
    if args.html:
        output = root / "simulation_edge" / "logs" / "debug_status" / "latest.html"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(render_html(collector.checks, overall), encoding="utf-8")
        payload["html_path"] = str(output)
    print(json.dumps(payload, indent=2) if args.json else render_console(collector.checks, overall))
    if args.html and not args.json:
        print(f"\nHTML status: {payload['html_path']}")
    return 1 if overall == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
