"""TeleOp WebSocket proxy (coordinator -> HTTP edge /ws/teleop).

Exercises the real proxy (``run_teleop_session_proxy``) end-to-end against a
fake edge WebSocket server that speaks the edge's flat Tier-A contract, plus
unit tests for the frame-translation helpers and the ``teleop_ws_url`` resolver.
No hardware, no coordinator app: a duck-typed browser socket drives the proxy.
"""
from __future__ import annotations

import asyncio
import json
import unittest
from typing import Any, Dict, Optional

import websockets

from lab_model.execution.edge.client import HttpEdgeClient
from lab_model.execution.orchestration import teleop_session_ws
from lab_model.execution.orchestration.teleop_session_ws import (
    _edge_to_browser,
    _flat_pose_fields,
    run_teleop_session_proxy,
)


# --------------------------------------------------------------------------- #
# Unit tests: pure translation helpers                                         #
# --------------------------------------------------------------------------- #
class FlatPoseFieldsTests(unittest.TestCase):
    def test_nested_target_pose_is_flattened(self) -> None:
        out = _flat_pose_fields({"target_pose": {"x": 1, "y": 2.5, "rotation": 10}})
        self.assertEqual(out, {"x": 1.0, "y": 2.5, "rotation": 10.0})

    def test_flat_fields_pass_through(self) -> None:
        out = _flat_pose_fields({"x": 3, "z": -1})
        self.assertEqual(out, {"x": 3.0, "z": -1.0})

    def test_non_numeric_and_nested_axes_dropped(self) -> None:
        out = _flat_pose_fields({"x": "nope", "y": {"bad": 1}, "z": 4})
        self.assertEqual(out, {"z": 4.0})


class EdgeToBrowserTests(unittest.TestCase):
    def test_pose_sample_becomes_pose(self) -> None:
        out = _edge_to_browser(
            {"kind": "pose_sample", "tag_id": "t", "x": 1.0, "y": 2.0, "epoch_ms": 42}
        )
        self.assertIsNotNone(out)
        assert out is not None
        self.assertEqual(out["type"], "pose")
        self.assertEqual(out["ts_ms"], 42)
        self.assertEqual(out["pose"], {"x": 1.0, "y": 2.0})
        self.assertEqual(out["phase"], "idle")

    def test_executing_sets_phase(self) -> None:
        out = _edge_to_browser({"kind": "pose_sample", "x": 0.0, "executing": True})
        assert out is not None
        self.assertEqual(out["phase"], "executing")

    def test_error_frame(self) -> None:
        out = _edge_to_browser({"kind": "error", "error_code": "TELEOP_ERROR"})
        assert out is not None
        self.assertEqual(out["type"], "error")
        self.assertIn("TELEOP_ERROR", out["message"])

    def test_pong_is_dropped(self) -> None:
        self.assertIsNone(_edge_to_browser({"kind": "pong"}))


class TeleopWsUrlTests(unittest.TestCase):
    def test_resolves_and_maps_scheme(self) -> None:
        client = HttpEdgeClient(base_url="http://edge.local:8200")
        client._caps_cache = {
            "telemetry_channels": {
                "teleop": {"transport": "websocket", "path": "/ws/teleop"}
            }
        }
        self.assertEqual(client.teleop_ws_url(), "ws://edge.local:8200/ws/teleop")

    def test_https_maps_to_wss(self) -> None:
        client = HttpEdgeClient(base_url="https://edge.local")
        client._caps_cache = {
            "telemetry_channels": {"ws0": {"transport": "websocket", "path": "/ws/teleop"}}
        }
        self.assertEqual(client.teleop_ws_url(), "wss://edge.local/ws/teleop")

    def test_no_teleop_channel_returns_none(self) -> None:
        client = HttpEdgeClient(base_url="http://edge.local")
        client._caps_cache = {"telemetry_channels": {"cam": {"transport": "mjpeg_http"}}}
        self.assertIsNone(client.teleop_ws_url())


# --------------------------------------------------------------------------- #
# Integration: real proxy against a fake edge /ws/teleop server                #
# --------------------------------------------------------------------------- #
from fastapi import WebSocketDisconnect  # noqa: E402


_DISCONNECT = object()


class _FakeBrowserWS:
    """Duck-typed FastAPI WebSocket backed by asyncio queues."""

    def __init__(self) -> None:
        self.outgoing: "asyncio.Queue[Dict[str, Any]]" = asyncio.Queue()
        self._incoming: "asyncio.Queue[Any]" = asyncio.Queue()
        self.closed: Optional[tuple] = None
        self.accepted = False

    # -- server -> browser -------------------------------------------------- #
    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, data: Dict[str, Any]) -> None:
        await self.outgoing.put(data)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = (code, reason)

    # -- browser -> server -------------------------------------------------- #
    async def receive_text(self) -> str:
        item = await self._incoming.get()
        if item is _DISCONNECT:
            raise WebSocketDisconnect()
        return item

    def client_send(self, msg: Dict[str, Any]) -> None:
        self._incoming.put_nowait(json.dumps(msg))

    def client_disconnect(self) -> None:
        self._incoming.put_nowait(_DISCONNECT)


class _FakeEdgeClient:
    """Only teleop_ws_url is used by the proxy."""

    def __init__(self, url: str) -> None:
        self._url = url

    def teleop_ws_url(self) -> str:
        return self._url


async def _edge_handler(ws: Any) -> None:
    """Minimal edge /ws/teleop: flat frames, request/response, no push."""
    pose = {"x": 0.0, "y": 0.0, "z": 0.0, "rotation": 0.0}

    def sample(tag: str) -> str:
        return json.dumps(
            {"kind": "pose_sample", "tag_id": tag, **pose, "epoch_ms": 1}
        )

    async for raw in ws:
        msg = json.loads(raw)
        tag = str(msg.get("tag_id") or "")
        cmd = msg.get("cmd")
        if cmd == "PING":
            await ws.send(json.dumps({"epoch_ms": 1, "tag_id": tag, "kind": "pong"}))
        elif cmd == "POSE":
            await ws.send(sample(tag))
        elif cmd in ("JOG", "GOTO"):
            for axis in ("x", "y", "z", "rotation"):
                if msg.get(axis) is not None:
                    pose[axis] = float(msg[axis])
            await ws.send(sample(tag))
        else:
            await ws.send(
                json.dumps(
                    {"epoch_ms": 1, "tag_id": tag, "kind": "error", "error_code": "UNKNOWN_CMD"}
                )
            )


async def _drain_until(browser: _FakeBrowserWS, pred, timeout: float = 3.0):
    """Return the first outgoing message matching ``pred`` within ``timeout``."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        remaining = deadline - loop.time()
        msg = await asyncio.wait_for(browser.outgoing.get(), timeout=remaining)
        if pred(msg):
            return msg
    raise AssertionError("expected message not seen before timeout")


class TeleopProxyIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        # Keep the pose tick lively but not a 50 Hz flood in tests.
        self._orig_interval = teleop_session_ws._PUSH_INTERVAL_S
        teleop_session_ws._PUSH_INTERVAL_S = 0.02
        self._server = await websockets.serve(_edge_handler, "127.0.0.1", 0)
        port = self._server.sockets[0].getsockname()[1]
        self._url = f"ws://127.0.0.1:{port}/ws/teleop"

    async def asyncTearDown(self) -> None:
        teleop_session_ws._PUSH_INTERVAL_S = self._orig_interval
        self._server.close()
        await self._server.wait_closed()

    async def test_session_pose_goto_ping_and_disconnect(self) -> None:
        browser = _FakeBrowserWS()
        client = _FakeEdgeClient(self._url)
        task = asyncio.create_task(
            run_teleop_session_proxy(browser, client, "tag_22")  # type: ignore[arg-type]
        )

        # 1) Session handshake first.
        first = await asyncio.wait_for(browser.outgoing.get(), timeout=3.0)
        self.assertEqual(first["type"], "session")
        self.assertEqual(first["phase"], "ready")
        self.assertEqual(first["tag_id"], "tag_22")

        # 2) Server-push pose stream (driven by POSE ticks).
        pose_msg = await _drain_until(browser, lambda m: m.get("type") == "pose")
        self.assertIn("x", pose_msg["pose"])

        # 3) goto is flattened, forwarded, and reflected back in subsequent poses.
        browser.client_send({"type": "goto", "target_pose": {"x": 12.5, "y": -3.0}})
        reflected = await _drain_until(
            browser,
            lambda m: m.get("type") == "pose" and m["pose"].get("x") == 12.5,
        )
        self.assertEqual(reflected["pose"]["y"], -3.0)

        # 4) ping is answered locally.
        browser.client_send({"type": "ping", "ts_ms": 99})
        pong = await _drain_until(browser, lambda m: m.get("type") == "pong")
        self.assertEqual(pong["ts_ms"], 99)

        # 5) clean disconnect ends the proxy.
        browser.client_disconnect()
        await asyncio.wait_for(task, timeout=3.0)


if __name__ == "__main__":
    unittest.main()
