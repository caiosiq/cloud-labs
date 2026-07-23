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
    from lab_model.execution.edge.client import HttpEdgeClient

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


# ---------------------------------------------------------------------------
# HTTP edge proxy
# ---------------------------------------------------------------------------
#
# For an HTTP Edge Contract backend there is no in-process ``LabCommunicator``:
# the arm lives behind the edge's own ``/ws/teleop`` socket. This proxy bridges
# the browser-facing typed protocol (``session`` / ``pose`` / ``pong`` /
# ``error`` out; ``ping`` / ``goto`` / ``jog`` in) to the edge's flat Tier-A
# frames (``cmd`` in ``PING`` / ``POSE`` / ``JOG`` / ``GOTO``), keeping the edge
# the single owner of the arm and of the table<->robot transform. The edge WS is
# request/response, so we drive the ~50 Hz pose push by ticking a motion-free
# ``POSE`` read over the same socket.


def _flat_pose_fields(msg: Dict[str, Any]) -> Dict[str, float]:
    """Extract flat x/y/z/rotation from a browser ``goto``/``jog`` message.

    Accepts either a nested ``target_pose``/``nominal_pose`` object or flat
    top-level fields, and returns only the present numeric axes so the edge (which
    rejects nested envelopes) receives a flat frame.
    """
    src: Dict[str, Any] = {}
    for key in ("target_pose", "nominal_pose"):
        nested = msg.get(key)
        if isinstance(nested, dict):
            src.update(nested)
    for axis in ("x", "y", "z", "rotation"):
        if axis in msg and not isinstance(msg[axis], (dict, list)):
            src[axis] = msg[axis]
    out: Dict[str, float] = {}
    for axis in ("x", "y", "z", "rotation"):
        val = src.get(axis)
        if val is None:
            continue
        try:
            out[axis] = float(val)
        except (TypeError, ValueError):
            continue
    return out


def _edge_to_browser(msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Translate one edge Tier-A frame into a browser-facing message (or None)."""
    kind = str(msg.get("kind") or "").lower()
    if kind == "pong":
        return None  # browser pings are answered locally
    if kind == "error":
        detail = msg.get("message") or msg.get("error_code") or "teleop error"
        return {"type": "error", "message": str(detail)}
    # Any other frame is a pose sample (kind == "pose_sample" or bare axes).
    pose = {
        k: msg[k]
        for k in ("x", "y", "z", "rotation", "executing")
        if k in msg
    }
    if not pose:
        return None
    return {
        "type": "pose",
        "ts_ms": msg.get("epoch_ms"),
        "phase": "executing" if msg.get("executing") else "idle",
        "pose": pose,
    }


async def run_teleop_session_proxy(
    websocket: "WebSocket",
    edge_client: "HttpEdgeClient",
    tag_id: str,
) -> None:
    """Proxy a browser TeleOp WebSocket to an HTTP edge's ``/ws/teleop``.

    Opens one outbound socket to the edge, ticks a motion-free ``POSE`` read at
    ~50 Hz for the server-push pose stream, and forwards ``goto`` / ``jog`` /
    ``ping`` from the browser. Edge frames are translated back to the typed
    browser protocol so the frontend is identical to the in-process path.
    """
    from fastapi import WebSocketDisconnect  # noqa: PLC0415
    import websockets  # noqa: PLC0415
    from websockets.exceptions import ConnectionClosed  # noqa: PLC0415

    ws_url = edge_client.teleop_ws_url()
    if not ws_url:
        await websocket.close(code=4409, reason="Edge advertises no teleop socket")
        return

    try:
        edge_ws = await websockets.connect(ws_url, open_timeout=5.0)
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("teleop proxy could not reach edge %s: %s", ws_url, exc)
        await websocket.close(code=1011, reason="Edge teleop unreachable")
        return

    await websocket.accept()
    await websocket.send_json({"type": "session", "phase": "ready", "tag_id": tag_id})

    stop = asyncio.Event()
    send_lock = asyncio.Lock()

    async def _edge_send(frame: Dict[str, Any]) -> None:
        async with send_lock:
            await edge_ws.send(json.dumps(frame))

    async def _pose_push() -> None:
        """Tick a read-only POSE over the edge socket for the browser push."""
        try:
            while not stop.is_set():
                await _edge_send({"tag_id": tag_id, "cmd": "POSE"})
                await asyncio.sleep(_PUSH_INTERVAL_S)
        except asyncio.CancelledError:
            raise
        except (ConnectionClosed, OSError):
            stop.set()
        except Exception as exc:  # noqa: BLE001
            _LOG.debug("teleop proxy pose tick ended for %s: %s", tag_id, exc)
            stop.set()

    async def _edge_reader() -> None:
        """Relay edge frames to the browser as typed pose/error messages."""
        try:
            async for raw in edge_ws:
                try:
                    msg = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    continue
                if not isinstance(msg, dict):
                    continue
                out = _edge_to_browser(msg)
                if out is not None:
                    await websocket.send_json(out)
        except asyncio.CancelledError:
            raise
        except (ConnectionClosed, OSError):
            pass
        except Exception as exc:  # noqa: BLE001
            _LOG.debug("teleop proxy edge reader ended for %s: %s", tag_id, exc)
        finally:
            stop.set()

    push_task = asyncio.create_task(_pose_push(), name=f"teleop-proxy-push-{tag_id}")
    reader_task = asyncio.create_task(_edge_reader(), name=f"teleop-proxy-read-{tag_id}")

    try:
        while not stop.is_set():
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "message": "invalid JSON"})
                continue
            if not isinstance(msg, dict):
                await websocket.send_json(
                    {"type": "error", "message": "message must be a JSON object"}
                )
                continue
            mtype = str(msg.get("type") or "").strip().lower()
            if mtype == "ping":
                await websocket.send_json({"type": "pong", "ts_ms": msg.get("ts_ms")})
                continue
            if mtype == "goto":
                frame: Dict[str, Any] = {"tag_id": tag_id, "cmd": "GOTO"}
                frame.update(_flat_pose_fields(msg))
                if msg.get("speed") is not None:
                    frame["speed"] = msg["speed"]
                await _edge_send(frame)
                continue
            if mtype == "jog":
                frame = {"tag_id": tag_id, "cmd": "JOG"}
                if msg.get("axis") is not None and msg.get("val") is not None:
                    frame["axis"] = msg["axis"]
                    frame["val"] = msg["val"]
                else:
                    frame.update(_flat_pose_fields(msg))
                await _edge_send(frame)
                continue
            await websocket.send_json(
                {"type": "error", "message": f"unknown message type {mtype!r}"}
            )
    except WebSocketDisconnect:
        pass
    except (ConnectionClosed, OSError):
        pass
    finally:
        stop.set()
        push_task.cancel()
        reader_task.cancel()
        for task in (push_task, reader_task):
            try:
                await task
            except asyncio.CancelledError:
                pass
        try:
            await edge_ws.close()
        except Exception:  # noqa: BLE001
            pass
