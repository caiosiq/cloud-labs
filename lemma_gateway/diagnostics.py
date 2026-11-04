"""Read bounded simulator diagnostics referenced by a gateway job's own error."""
from __future__ import annotations

import json
import os
import re
from collections import deque
from pathlib import Path
from typing import Any

MAX_SCAN_BYTES = 4 * 1024 * 1024
MAX_EVENTS = 40
MAX_TEXT_CHARS = 16000
MAX_OUTPUT_BYTES = 96 * 1024
_REFERENCE = re.compile(r"sim_request_id=([0-9a-f]{32}),\s*log=([^\r\n]+\.jsonl)\)")
_FIELDS = (
    "timestamp", "event", "request_id", "tag_id", "stage", "message",
    "command", "error", "traceback", "stage_trace",
)


def simulator_log_root() -> Path:
    override = os.getenv("CLOUDLAB_MUJOCO_LOG_DIR")
    return (Path(override).expanduser() if override else
            Path(__file__).resolve().parents[1] / "simulation_edge/logs/mujoco_sessions").resolve()


def job_diagnostics(job: dict[str, Any]) -> dict[str, Any]:
    result = job.get("result")
    result = result if isinstance(result, dict) else {}
    command_status = result.get("command_status")
    command_status = command_status if isinstance(command_status, dict) else {}
    report = {
        "job_id": job.get("job_id"), "command": job.get("command"),
        "status": job["status"], "error": job.get("error"),
        "command_id": result.get("command_id"), "command_status": command_status,
        "simulator": {"available": False, "events": []},
    }
    simulator = report["simulator"]
    text = str(command_status.get("error") or job.get("error") or "")
    match = _REFERENCE.search(text)
    if not match:
        simulator["reason"] = "This job has no simulator log reference; its error above is the available diagnostic."
        return report
    request_id, filename = match.groups()
    simulator["request_id"] = request_id
    try:
        path = Path(filename).resolve()
        if path.parent != simulator_log_root() or not re.fullmatch(r"mujoco_\d{8}_\d{6}_\d+\.jsonl", path.name):
            simulator["reason"] = "The referenced file is outside the configured simulator session logs."
            return report
        with path.open("rb") as handle:
            size = handle.seek(0, os.SEEK_END)
            offset = max(0, size - MAX_SCAN_BYTES)
            handle.seek(offset)
            lines = handle.read(MAX_SCAN_BYTES).decode("utf-8", errors="replace").splitlines()
    except (OSError, ValueError):
        simulator["reason"] = "The referenced simulator log is unavailable on the gateway host."
        return report
    if offset:
        lines = lines[1:]  # The first line may start in the middle of a JSON record.
    events: deque[dict[str, Any]] = deque(maxlen=MAX_EVENTS)
    matched = 0
    truncated = bool(offset)
    for line in lines:
        try:
            row = json.loads(line)
        except (ValueError, RecursionError):
            continue  # A log may end with a partially written record.
        if not isinstance(row, dict) or row.get("request_id") != request_id:
            continue
        matched += 1
        event = {key: row[key] for key in _FIELDS if key in row}
        for key, value in list(event.items()):
            if isinstance(value, str) and len(value) > MAX_TEXT_CHARS:
                event[key] = value[:MAX_TEXT_CHARS] + "\n[truncated]"
                truncated = True
        events.append(event)
    selected = list(events)
    while selected and len(json.dumps(selected).encode("utf-8")) > MAX_OUTPUT_BYTES:
        selected.pop(0)
        truncated = True
    simulator.update(available=bool(selected), events=selected,
                     truncated=truncated or matched > MAX_EVENTS)
    if not selected:
        simulator["reason"] = "No entries for this simulator request were found in the bounded log excerpt."
    return report


def capture_job_diagnostics(job: dict[str, Any]) -> dict[str, Any]:
    """Diagnostics must never replace or hide the original command failure."""
    try:
        return job_diagnostics(job)
    except Exception as exc:
        return {
            "job_id": job.get("job_id"), "command": job.get("command"),
            "status": job.get("status"), "error": job.get("error"),
            "simulator": {
                "available": False, "events": [],
                "reason": f"Diagnostics could not be collected ({type(exc).__name__}); the original error is retained.",
            },
        }
