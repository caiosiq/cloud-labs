#!/usr/bin/env python3
"""Preflight/status panel for Cloud Labs + MuJoCo + WSL MoveIt debugging.

This script is intentionally external: it checks ports, HTTP endpoints, WSL
visibility, repo layout, and log freshness without importing the backend app.
Use --deep when you want to verify the MuJoCo debug/observer path too.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


STATUS_ORDER = {"fail": 0, "warn": 1, "info": 2, "ok": 3}
STATUS_LABEL = {
    "ok": "OK",
    "warn": "WARN",
    "fail": "FAIL",
    "info": "INFO",
}
REQUIRED_MOVEIT_SIDECAR_PROTOCOL_VERSION = 3


@dataclass
class Check:
    group: str
    name: str
    status: str
    message: str
    detail: dict[str, Any] = field(default_factory=dict)
    action: str | None = None

    def as_dict(self) -> dict[str, Any]:
        out = {
            "group": self.group,
            "name": self.name,
            "status": self.status,
            "message": self.message,
            "detail": self.detail,
        }
        if self.action:
            out["action"] = self.action
        return out


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
            Check(
                group=group,
                name=name,
                status=status,
                message=message,
                detail=detail or {},
                action=action,
            )
        )

    def overall(self) -> str:
        if any(check.status == "fail" for check in self.checks):
            return "fail"
        if any(check.status == "warn" for check in self.checks):
            return "warn"
        return "ok"


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def workspace_root(root: Path) -> Path:
    return root.parent


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


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


def compact(text: str, *, limit: int = 260) -> str:
    out = " ".join(str(text or "").split())
    if len(out) <= limit:
        return out
    return out[: limit - 3] + "..."


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def latest_path(root: Path, pattern: str) -> Path | None:
    if not root.is_dir():
        return None
    matches = list(root.glob(pattern))
    if not matches:
        return None
    return max(matches, key=lambda p: p.stat().st_mtime)


def port_open(host: str, port: int, *, timeout_s: float) -> tuple[bool, str | None]:
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True, None
    except OSError as exc:
        return False, str(exc)


def http_get(
    url: str,
    *,
    timeout_s: float,
    accept: str = "application/json",
) -> tuple[bool, int | None, Any, str]:
    request = Request(url, headers={"Accept": accept})
    try:
        with urlopen(request, timeout=timeout_s) as response:
            body = response.read()
            content_type = response.headers.get("content-type", "")
            text = body.decode("utf-8", errors="replace")
            if "json" in content_type or text.strip().startswith(("{", "[")):
                try:
                    return True, int(response.status), json.loads(text or "{}"), text
                except json.JSONDecodeError:
                    return True, int(response.status), None, text
            return True, int(response.status), {"bytes": len(body)}, text
    except HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        return False, int(exc.code), parsed, text
    except (URLError, TimeoutError, OSError) as exc:
        return False, None, None, str(exc)


def run_command(args: list[str], *, timeout_s: float) -> tuple[int | None, str]:
    try:
        completed = subprocess.run(
            args,
            check=False,
            capture_output=True,
            timeout=timeout_s,
        )
        raw = completed.stdout + completed.stderr
        if b"\x00" in raw:
            text = raw.decode("utf-16le", errors="replace")
        else:
            text = raw.decode("utf-8", errors="replace")
        return completed.returncode, text.replace("\r\n", "\n").replace("\r", "\n").strip()
    except FileNotFoundError as exc:
        return None, str(exc)
    except subprocess.TimeoutExpired as exc:
        return None, f"timed out after {timeout_s:.1f}s: {exc}"


def parse_iso_utc(text: str) -> dt.datetime | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        value = dt.datetime.fromisoformat(raw)
    except ValueError:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc)


def process_start_time_utc(pid: int) -> dt.datetime | None:
    scripts = [
        (
            f"$p=Get-CimInstance Win32_Process -Filter \"ProcessId = {int(pid)}\"; "
            "if ($p) { $p.CreationDate.ToUniversalTime().ToString('o') }"
        ),
        (
            f"$p=Get-Process -Id {int(pid)} -ErrorAction SilentlyContinue; "
            "if ($p) { $p.StartTime.ToUniversalTime().ToString('o') }"
        ),
    ]
    for script in scripts:
        rc, out = run_command(
            ["powershell", "-NoProfile", "-Command", script],
            timeout_s=5.0,
        )
        if rc != 0:
            continue
        for line in reversed(out.splitlines()):
            parsed = parse_iso_utc(line)
            if parsed is not None:
                return parsed
    return None


def latest_simulation_edge_source(root: Path) -> tuple[Path | None, dt.datetime | None]:
    source_root = root / "simulation_edge" / "src" / "simulation_edge"
    if not source_root.is_dir():
        return None, None
    newest: Path | None = None
    newest_mtime = -1.0
    for path in source_root.rglob("*.py"):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if mtime > newest_mtime:
            newest = path
            newest_mtime = mtime
    if newest is None:
        return None, None
    return (
        newest,
        dt.datetime.fromtimestamp(newest_mtime, tz=dt.timezone.utc),
    )


def parse_edge_url(root: Path) -> str:
    backends = load_json(root / "schemas" / "backends.json")
    for row in backends.get("backends") or []:
        if row.get("backend_id") == "sim.default":
            edge = row.get("edge") or {}
            return str(edge.get("base_url") or "http://127.0.0.1:8120").rstrip("/")
    return "http://127.0.0.1:8120"


def python_import_check(python: Path) -> tuple[bool, str]:
    if not python.is_file():
        return False, f"missing {python}"
    code = (
        "import importlib.util, json; "
        "mods=['mujoco','fastapi','uvicorn','numpy']; "
        "print(json.dumps({m: bool(importlib.util.find_spec(m)) for m in mods}))"
    )
    rc, out = run_command([str(python), "-c", code], timeout_s=10.0)
    if rc != 0:
        return False, out or "python import probe failed"
    try:
        found = json.loads(out)
    except json.JSONDecodeError:
        return False, out
    missing = [name for name, ok in found.items() if not ok]
    if missing:
        return False, "missing Python modules: " + ", ".join(missing)
    return True, "required Python modules import"


def add_repo_checks(col: StatusCollector, root: Path) -> str:
    ws = workspace_root(root)
    lab_auto = ws / "lab_automation"
    col.add("Repo", "cloud-labs path", "ok" if root.is_dir() else "fail", str(root))
    col.add(
        "Repo",
        "lab_automation sibling",
        "ok" if lab_auto.is_dir() else "fail",
        str(lab_auto),
        action="Clone or restore lab_automation beside cloud-labs-caio-edge-contract.",
    )
    schema = root / "schemas" / "backends.json"
    try:
        edge_url = parse_edge_url(root)
        col.add("Repo", "sim.default edge URL", "ok", edge_url)
    except Exception as exc:  # noqa: BLE001
        edge_url = "http://127.0.0.1:8120"
        col.add(
            "Repo",
            "sim.default edge URL",
            "fail",
            f"could not parse {schema}: {exc}",
            action="Fix schemas/backends.json.",
        )
    venv_python = ws / ".venv" / "Scripts" / "python.exe"
    ok, message = python_import_check(venv_python)
    col.add(
        "Repo",
        "Python runtime",
        "ok" if ok else "fail",
        message,
        detail={"python": str(venv_python)},
        action="Use the repo .venv or reinstall requirements." if not ok else None,
    )
    return edge_url


def add_wsl_checks(col: StatusCollector) -> None:
    rc, out = run_command(["wsl.exe", "--list", "--verbose"], timeout_s=8.0)
    if rc == 0 and "no installed distributions" not in out.lower():
        lines = [line.strip() for line in out.splitlines() if line.strip()]
        col.add("WSL", "Codex WSL visibility", "ok", "; ".join(lines[:3]))
    else:
        col.add(
            "WSL",
            "Codex WSL visibility",
            "info",
            compact(out or "wsl.exe unavailable from this process"),
            action=(
                "If MoveIt is not reachable, start the WSL MoveIt stack from "
                "your WSL terminal."
            ),
        )


def add_http_service_checks(
    col: StatusCollector,
    *,
    name: str,
    group: str,
    base_url: str,
    health_path: str,
    port: int,
    timeout_s: float,
) -> tuple[bool, Any]:
    open_ok, port_error = port_open("127.0.0.1", port, timeout_s=1.0)
    col.add(
        group,
        f"{name} port {port}",
        "ok" if open_ok else "fail",
        "listening" if open_ok else port_error or "not listening",
    )
    url = f"{base_url.rstrip('/')}{health_path}"
    ok, status, payload, text = http_get(url, timeout_s=timeout_s)
    col.add(
        group,
        f"{name} HTTP {health_path}",
        "ok" if ok and status and status < 400 else "fail",
        f"HTTP {status}" if status else text,
        detail=payload if isinstance(payload, dict) else {},
    )
    return bool(ok and status and status < 400), payload


def add_moveit_checks(col: StatusCollector, *, moveit_url: str) -> None:
    ok, payload = add_http_service_checks(
        col,
        name="MoveIt sidecar",
        group="MoveIt",
        base_url=moveit_url,
        health_path="/health",
        port=8765,
        timeout_s=4.0,
    )
    if not ok:
        col.add(
            "MoveIt",
            "Move group action",
            "fail",
            "sidecar health is not reachable",
            action=(
                "In WSL: cd /mnt/c/Users/joshua/Documents/robotic_twin_simulation; "
                "source /opt/ros/jazzy/setup.bash; source ~/dev_ws/install/setup.bash; "
                "export RMW_FASTRTPS_USE_SHM=0; "
                "bash lab_automation/tools/moveit_sidecar/run_moveit_stack.sh restart"
            ),
        )
        return
    if isinstance(payload, dict):
        raw_version = payload.get("sidecar_protocol_version")
        try:
            protocol_version = int(raw_version)
        except (TypeError, ValueError):
            protocol_version = 0
        protocol_ok = protocol_version >= REQUIRED_MOVEIT_SIDECAR_PROTOCOL_VERSION
        col.add(
            "MoveIt",
            "MoveIt sidecar protocol",
            "ok" if protocol_ok else "fail",
            (
                f"protocol {protocol_version}"
                if protocol_ok
                else (
                    f"protocol {raw_version!r}; required "
                    f"{REQUIRED_MOVEIT_SIDECAR_PROTOCOL_VERSION}"
                )
            ),
            detail={
                "required_sidecar_protocol_version": (
                    REQUIRED_MOVEIT_SIDECAR_PROTOCOL_VERSION
                ),
                "sidecar_protocol_version": raw_version,
            },
            action=(
                "In WSL: cd /mnt/c/Users/joshua/Documents/robotic_twin_simulation; "
                "source /opt/ros/jazzy/setup.bash; source ~/dev_ws/install/setup.bash; "
                "export RMW_FASTRTPS_USE_SHM=0; "
                "bash lab_automation/tools/moveit_sidecar/run_moveit_stack.sh restart"
            )
            if not protocol_ok
            else None,
        )
        ready = bool(payload.get("move_group_action_ready"))
        status = "ok" if ready else "fail"
        col.add(
            "MoveIt",
            "Move group action",
            status,
            "ready" if ready else "health reachable but /move_action is not ready",
            detail=payload,
        )


def add_edge_checks(
    col: StatusCollector,
    *,
    root: Path,
    edge_url: str,
    deep: bool,
    require_observer: bool,
) -> dict[str, Any] | None:
    ok, _ = add_http_service_checks(
        col,
        name="simulation_edge",
        group="Simulation Edge",
        base_url=edge_url,
        health_path="/health",
        port=8120,
        timeout_s=4.0,
    )
    if not ok or not deep:
        if ok:
            for path, label in (
                ("/capabilities", "simulation_edge capabilities"),
                ("/bench", "simulation_edge bench"),
                ("/lab-state", "simulation_edge lab state"),
            ):
                endpoint_ok, status, payload, text = http_get(
                    f"{edge_url}{path}",
                    timeout_s=35.0,
                )
                col.add(
                    "Simulation Edge",
                    label,
                    "ok" if endpoint_ok and status and status < 400 else "fail",
                    f"HTTP {status}" if status else compact(text),
                    detail=payload if isinstance(payload, dict) else {},
                    action=(
                        "Restart the simulation edge PowerShell terminal so the UI "
                        "uses the latest code and MuJoCo can bootstrap cleanly."
                    )
                    if not endpoint_ok
                    else None,
                )
        return None
    for path, label in (
        ("/capabilities", "simulation_edge capabilities"),
        ("/bench", "simulation_edge bench"),
        ("/lab-state", "simulation_edge lab state"),
    ):
        endpoint_ok, status, payload, text = http_get(
            f"{edge_url}{path}",
            timeout_s=35.0,
        )
        col.add(
            "Simulation Edge",
            label,
            "ok" if endpoint_ok and status and status < 400 else "fail",
            f"HTTP {status}" if status else compact(text),
            detail=payload if isinstance(payload, dict) else {},
            action=(
                "Restart the simulation edge PowerShell terminal so the UI "
                "uses the latest code and MuJoCo can bootstrap cleanly."
            )
            if not endpoint_ok
            else None,
        )
    ok, status, payload, text = http_get(f"{edge_url}/debug/health", timeout_s=35.0)
    col.add(
        "Simulation Edge",
        "MuJoCo debug health",
        "ok" if ok and status and status < 400 else "fail",
        f"HTTP {status}" if status else text,
        detail=payload if isinstance(payload, dict) else {},
        action=(
            "Start simulation edge with scripts/ops/run_simulation_edge_moveit.ps1 "
            "-Viewer -Observe"
        )
        if not ok
        else None,
    )
    if not isinstance(payload, dict):
        return None
    simulator = payload.get("simulator") or {}
    running = bool(simulator.get("running"))
    col.add(
        "Simulation Edge",
        "MuJoCo process",
        "ok" if running else "fail",
        f"pid={simulator.get('pid')}" if running else str(simulator.get("last_error") or "not running"),
    )
    if running and simulator.get("pid") is not None:
        start_time = process_start_time_utc(int(simulator["pid"]))
        source_path, source_mtime = latest_simulation_edge_source(root)
        if start_time is None or source_mtime is None or source_path is None:
            col.add(
                "Simulation Edge",
                "source freshness",
                "info",
                "could not compare process start time to source mtime",
            )
        elif source_mtime > start_time + dt.timedelta(seconds=2):
            col.add(
                "Simulation Edge",
                "source freshness",
                "warn",
                (
                    "running process predates source edits; latest source "
                    f"{source_path.name} mtime={source_mtime.isoformat(timespec='seconds')}"
                ),
                detail={
                    "pid": simulator.get("pid"),
                    "process_start_utc": start_time.isoformat(timespec="seconds"),
                    "latest_source": str(source_path),
                    "latest_source_mtime_utc": source_mtime.isoformat(timespec="seconds"),
                },
                action=(
                    "Restart the simulation edge PowerShell terminal so the UI uses "
                    "the latest code: powershell -ExecutionPolicy Bypass -File "
                    ".\\scripts\\ops\\run_simulation_edge_moveit.ps1 -Viewer -Observe"
                ),
            )
        else:
            col.add(
                "Simulation Edge",
                "source freshness",
                "ok",
                "running process is newer than simulation_edge source edits",
                detail={
                    "pid": simulator.get("pid"),
                    "process_start_utc": start_time.isoformat(timespec="seconds"),
                    "latest_source": str(source_path),
                    "latest_source_mtime_utc": source_mtime.isoformat(timespec="seconds"),
                },
            )
    observer = ((payload.get("debug") or {}).get("observer")) or simulator.get("observer")
    if isinstance(observer, dict):
        enabled = bool(observer.get("enabled"))
        status_name = "ok" if enabled else ("fail" if require_observer else "warn")
        col.add(
            "Observation",
            "MuJoCo observer",
            status_name,
            (
                f"enabled, frames={observer.get('frame_count')}, "
                f"fps={observer.get('command_fps')}"
                if enabled
                else "observer is disabled"
            ),
            detail=observer,
            action=(
                "Restart simulation edge with -Observe, or POST "
                "/debug/record/start if the edge is already running."
            )
            if not enabled
            else None,
        )
        last_frame = observer.get("last_frame") or {}
        frame_path = Path(str(last_frame.get("path") or ""))
        col.add(
            "Observation",
            "Latest MuJoCo frame",
            "ok" if frame_path.is_file() else ("warn" if enabled else "info"),
            str(frame_path) if frame_path else "no frame captured yet",
        )
    else:
        col.add(
            "Observation",
            "MuJoCo observer",
            "fail" if require_observer else "warn",
            "observer status missing from debug health",
        )
    return payload


def add_backend_checks(
    col: StatusCollector,
    *,
    backend_url: str,
    deep: bool,
) -> None:
    open_ok, port_error = port_open("127.0.0.1", 8000, timeout_s=1.0)
    col.add(
        "Cloud Labs Backend",
        "backend port 8000",
        "ok" if open_ok else "fail",
        "listening" if open_ok else port_error or "not listening",
    )
    ok, status, payload, text = http_get(f"{backend_url}/api/backends", timeout_s=5.0)
    col.add(
        "Cloud Labs Backend",
        "/api/backends",
        "ok" if ok and status and status < 400 else "fail",
        f"HTTP {status}" if status else text,
        detail=payload if isinstance(payload, dict) else {},
    )
    ok, status, payload, text = http_get(f"{backend_url}/api/catalog", timeout_s=35.0)
    col.add(
        "Cloud Labs Backend",
        "/api/catalog",
        "ok" if ok and status and status < 400 else "fail",
        f"HTTP {status}" if status else compact(text),
        detail=payload if isinstance(payload, dict) else {},
        action=(
            "Restart the Cloud Labs backend PowerShell terminal if the UI catalog "
            "does not load."
        )
        if not ok
        else None,
    )
    ok, status, payload, text = http_get(
        f"{backend_url}/api/runtime-mode?backend_id=sim.default",
        timeout_s=8.0,
    )
    col.add(
        "Cloud Labs Backend",
        "sim.default runtime mode",
        "ok" if ok and status and status < 400 else "fail",
        f"HTTP {status}" if status else text,
        detail=payload if isinstance(payload, dict) else {},
    )
    if deep and ok:
        ok2, status2, payload2, text2 = http_get(
            f"{backend_url}/api/debug/mujoco?backend_id=sim.default",
            timeout_s=35.0,
        )
        col.add(
            "Cloud Labs Backend",
            "MuJoCo debug proxy",
            "ok" if ok2 and status2 and status2 < 400 else "fail",
            f"HTTP {status2}" if status2 else text2,
            detail=payload2 if isinstance(payload2, dict) else {},
        )


def add_log_checks(col: StatusCollector, root: Path) -> None:
    ws = workspace_root(root)
    latest_mujoco = latest_path(root / "simulation_edge" / "logs" / "mujoco_sessions", "*.jsonl")
    col.add(
        "Logs",
        "latest MuJoCo log",
        "ok" if latest_mujoco else "warn",
        f"{latest_mujoco} age={fmt_age(file_age_s(latest_mujoco))}" if latest_mujoco else "none found",
    )
    latest_observer = latest_path(root / "simulation_edge" / "logs" / "observer_runs", "mujoco_observe_*")
    col.add(
        "Logs",
        "latest observer run",
        "ok" if latest_observer else "warn",
        f"{latest_observer} age={fmt_age(file_age_s(latest_observer))}" if latest_observer else "none found",
    )
    sidecar_log = ws / "lab_automation" / "logs" / "moveit_sidecar" / "sidecar.log"
    ros_log = ws / "lab_automation" / "logs" / "moveit_sidecar" / "ros_launch.log"
    for label, path in (("MoveIt sidecar log", sidecar_log), ("ROS launch log", ros_log)):
        col.add(
            "Logs",
            label,
            "ok" if path.is_file() else "warn",
            f"{path} age={fmt_age(file_age_s(path))}" if path.is_file() else f"missing {path}",
        )


def render_console(checks: list[Check], overall: str) -> str:
    lines = []
    lines.append("Cloud Labs Debug Status")
    lines.append(f"Overall: {STATUS_LABEL[overall]}")
    lines.append("")
    current_group = None
    for check in checks:
        if check.group != current_group:
            current_group = check.group
            lines.append(f"{current_group}:")
        label = STATUS_LABEL.get(check.status, check.status.upper())
        lines.append(f"  [{label:<4}] {check.name}: {check.message}")
        if check.action and check.status in {"fail", "warn"}:
            lines.append(f"         action: {check.action}")
    return "\n".join(lines)


def render_html(checks: list[Check], overall: str) -> str:
    rows = []
    for check in checks:
        rows.append(
            "<tr>"
            f"<td>{html.escape(check.group)}</td>"
            f"<td><span class='led {check.status}'></span>{html.escape(STATUS_LABEL[check.status])}</td>"
            f"<td>{html.escape(check.name)}</td>"
            f"<td>{html.escape(check.message)}</td>"
            f"<td>{html.escape(check.action or '')}</td>"
            "</tr>"
        )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Cloud Labs Debug Status</title>
<style>
body {{ font-family: Segoe UI, Arial, sans-serif; background:#101318; color:#e5e7eb; margin:24px; }}
h1 {{ margin-bottom:6px; }}
.overall {{ display:inline-flex; align-items:center; gap:8px; padding:8px 10px; border:1px solid #2f3744; border-radius:6px; background:#171b22; }}
table {{ border-collapse:collapse; width:100%; margin-top:18px; }}
th, td {{ text-align:left; border-bottom:1px solid #29313d; padding:9px 10px; vertical-align:top; }}
th {{ color:#a7b0bd; font-weight:600; }}
.led {{ display:inline-block; width:12px; height:12px; border-radius:50%; margin-right:8px; box-shadow:0 0 10px currentColor; }}
.ok {{ color:#22c55e; background:#22c55e; }}
.warn {{ color:#f59e0b; background:#f59e0b; }}
.fail {{ color:#ef4444; background:#ef4444; }}
.info {{ color:#60a5fa; background:#60a5fa; }}
</style>
</head>
<body>
<h1>Cloud Labs Debug Status</h1>
<div class="overall"><span class="led {overall}"></span>Overall: {html.escape(STATUS_LABEL[overall])}</div>
<table>
<thead><tr><th>Group</th><th>Status</th><th>Check</th><th>Message</th><th>Action</th></tr></thead>
<tbody>
{''.join(rows)}
</tbody>
</table>
</body>
</html>
"""


def write_html(root: Path, checks: list[Check], overall: str) -> Path:
    out_dir = root / "simulation_edge" / "logs" / "debug_status"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "latest.html"
    path.write_text(render_html(checks, overall), encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend-url", default="http://127.0.0.1:8000")
    parser.add_argument("--moveit-url", default="http://127.0.0.1:8765")
    parser.add_argument("--edge-url", default=None)
    parser.add_argument("--deep", action="store_true")
    parser.add_argument("--require-observer", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--html", action="store_true")
    args = parser.parse_args()

    root = repo_root()
    col = StatusCollector()
    edge_url = args.edge_url.rstrip("/") if args.edge_url else add_repo_checks(col, root)
    if args.edge_url:
        add_repo_checks(col, root)
    add_wsl_checks(col)
    add_moveit_checks(col, moveit_url=args.moveit_url.rstrip("/"))
    add_edge_checks(
        col,
        root=root,
        edge_url=edge_url,
        deep=bool(args.deep),
        require_observer=bool(args.require_observer),
    )
    add_backend_checks(col, backend_url=args.backend_url.rstrip("/"), deep=bool(args.deep))
    add_log_checks(col, root)

    overall = col.overall()
    payload = {
        "timestamp": now_utc().isoformat(timespec="seconds"),
        "overall": overall,
        "checks": [check.as_dict() for check in col.checks],
    }
    if args.html:
        path = write_html(root, col.checks, overall)
        payload["html_path"] = str(path)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(render_console(col.checks, overall))
        if args.html:
            print("")
            print(f"HTML status: {payload['html_path']}")
    return 0 if overall != "fail" else 1


if __name__ == "__main__":
    raise SystemExit(main())
