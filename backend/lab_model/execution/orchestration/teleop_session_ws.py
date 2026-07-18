"""WebSocket TeleOp session â€” server-push pose + client goto (Phase 4)."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, Optional, TYPE_CHECKING

from lab_model.language.domain.component import is_teleop_ready

if TYPE_CHECKING:
    from fastapi import WebSocket
    from mock_edge.host.base import LabCommunicator

_LOG = logging.getLogger(__name__)

_PUSH_HZ = 50.0
_PUSH_INTERVAL_S = 1.0 / _PUSH_HZ


def _session_phase(pose: Optional[Dict[str, Any]]) -> str:
    if not pose:
        return "idle"
    return "executing" if pose.get("executing") else "idle"


async def _pose_push_loop(
    websocket: "WebSocket",
    lab: "LabCommunicator",
    tag_id: str,
    *,
    stop: asyncio.Event,
) -> None:
    """Push pose @ ~50 Hz while the socket is open."""
    try:
        while not stop.is_set():
            pose = lab.get_teleop_live_pose(tag_id)
            if pose is not None:
                payload = {
                    "type": "pose",
                    "ts_ms": pose.get("ts_ms"),
                    "phase": _session_phase(pose),
                    "pose": {
                        k: pose[k]
                        for k in ("x", "y", "z", "rotation", "executing")
                        if k in pose
                    },
                }
                await websocket.send_json(payload)
            await asyncio.sleep(_PUSH_INTERVAL_S)
    except asyncio.CancelledError:
        raise
    except (ConnectionResetError, OSError):
        pass
    except Exception as exc:  # noqa: BLE001
        _LOG.debug("teleop ws push loop ended for %s: %s", tag_id, exc)


async def _handle_client_message(
    lab: "LabCommunicator",
    tag_id: str,
    msg: Dict[str, Any],
    websocket: "WebSocket",
) -> None:
    mtype = str(msg.get("type") or "").strip().lower()
    if mtype == "ping":
        await websocket.send_json({"type": "pong", "ts_ms": msg.get("ts_ms")})
        return
    if mtype == "goto":
        body: Dict[str, Any] = {
            k: msg[k]
            for k in (
                "target_pose",
                "nominal_pose",
                "target_motor_positions",
                "speed",
                "frame_id",
            )
            if k in msg
        }
        try:
            await lab._teleop.goto(tag_id, body)
        except RuntimeError as exc:
            await websocket.send_json({"type": "error", "message": str(exc)})
        except Exception as exc:  # noqa: BLE001
            await websocket.send_json(
                {"type": "error", "message": f"goto failed: {exc!r}"}
            )
        return
    await websocket.send_json(
        {"type": "error", "message": f"unknown message type {mtype!r}"}
    )


async def run_teleop_session_websocket(
    websocket: "WebSocket",
    lab: "LabCommunicator",
    tag_id: str,
) -> None:
    """Handle one TeleOp WebSocket through connect â†’ push/receive â†’ disconnect."""
    from fastapi import WebSocketDisconnect  # noqa: PLC0415

    state = lab.get_lab_state()
    entry = (state.get("components") or {}).get(tag_id)
    if not isinstance(entry, dict) or not is_teleop_ready(entry):
        await websocket.close(code=4409, reason="TELEOP session not ready")
        return

    await websocket.accept()
    await websocket.send_json({"type": "session", "phase": "ready", "tag_id": tag_id})

    stop = asyncio.Event()
    push_task = asyncio.create_task(
        _pose_push_loop(websocket, lab, tag_id, stop=stop),
        name=f"teleop-ws-push-{tag_id}",
    )
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json(
                    {"type": "error", "message": "invalid JSON"}
                )
                continue
            if not isinstance(msg, dict):
                await websocket.send_json(
                    {"type": "error", "message": "message must be a JSON object"}
                )
                continue
            await _handle_client_message(lab, tag_id, msg, websocket)
    except WebSocketDisconnect:
        pass
    finally:
        stop.set()
        push_task.cancel()
        try:
            await push_task
        except asyncio.CancelledError:
            pass
