"""WebSocket optimize session — server-push pose + loss during autonomous OPTIMIZE."""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Dict, Optional

from lab_model.domain.holding import SYSTEM_STATUS_BUSY, SYSTEM_STATUS_OPTIMIZING

if TYPE_CHECKING:
    from fastapi import WebSocket
    from lab_communicator.base import LabCommunicator

_LOG = logging.getLogger(__name__)

_PUSH_HZ = 25.0
_PUSH_INTERVAL_S = 1.0 / _PUSH_HZ


def _tick_payload(pose: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not pose:
        return None
    body: Dict[str, Any] = {
        k: pose[k]
        for k in ("x", "y", "z", "rotation", "executing")
        if k in pose
    }
    mp = pose.get("motor_positions")
    if isinstance(mp, dict) and mp:
        body["motor_positions"] = dict(mp)
    if "iteration" in pose:
        body["iteration"] = pose["iteration"]
    if "loss" in pose:
        body["loss"] = pose["loss"]
    return body


def _message_type(pose: Dict[str, Any]) -> str:
    evt = pose.get("optimize_event")
    if evt == "step":
        return "step"
    if evt == "motion":
        return "motion"
    if pose.get("iteration") is not None or pose.get("loss") is not None:
        return "step"
    return "motion"


async def _optimize_push_loop(
    websocket: "WebSocket",
    lab: "LabCommunicator",
    tag_id: str,
    *,
    stop: asyncio.Event,
) -> None:
    last_type: Optional[str] = None
    try:
        while not stop.is_set():
            pose = lab.get_teleop_live_pose(tag_id)
            tick = _tick_payload(pose)
            if tick is not None:
                msg_type = _message_type(pose)
                payload: Dict[str, Any] = {
                    "type": msg_type,
                    "ts_ms": pose.get("ts_ms") if pose else None,
                    "pose": tick,
                }
                if msg_type == "step":
                    if "iteration" in tick:
                        payload["iteration"] = tick["iteration"]
                    if "loss" in tick:
                        payload["loss"] = tick["loss"]
                last_type = msg_type
                await websocket.send_json(payload)
            await asyncio.sleep(_PUSH_INTERVAL_S)
    except asyncio.CancelledError:
        raise
    except (ConnectionResetError, OSError):
        pass
    except Exception as exc:  # noqa: BLE001
        _LOG.debug("optimize ws push loop ended for %s: %s", tag_id, exc)


async def run_optimize_session_websocket(
    websocket: "WebSocket",
    lab: "LabCommunicator",
    tag_id: str,
) -> None:
    """Handle one optimize WebSocket (read-only telemetry stream)."""
    from fastapi import WebSocketDisconnect  # noqa: PLC0415

    state = lab.get_lab_state()
    active = getattr(lab, "_optimize_active_tag", None)
    status = state.get("system_status")
    if active != tag_id or status not in (SYSTEM_STATUS_BUSY, SYSTEM_STATUS_OPTIMIZING):
        await websocket.close(code=4409, reason="OPTIMIZE session not active")
        return

    await websocket.accept()
    await websocket.send_json(
        {"type": "session", "phase": "optimizing", "tag_id": tag_id}
    )

    stop = asyncio.Event()
    push_task = asyncio.create_task(
        _optimize_push_loop(websocket, lab, tag_id, stop=stop),
        name=f"optimize-ws-push-{tag_id}",
    )
    try:
        while True:
            raw = await websocket.receive_text()
            if raw.strip().lower() in ("ping", '{"type":"ping"}'):
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    finally:
        stop.set()
        push_task.cancel()
        try:
            await push_task
        except asyncio.CancelledError:
            pass
