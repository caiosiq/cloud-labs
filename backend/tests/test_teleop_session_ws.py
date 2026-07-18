"""Phase 4: WebSocket TeleOp session helpers."""
from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock

from lab_model.execution.orchestration.teleop_session_ws import (
    _handle_client_message,
    _session_phase,
)


class TestTeleopSessionWsHelpers(unittest.TestCase):
    def test_session_phase_idle_without_pose(self) -> None:
        self.assertEqual(_session_phase(None), "idle")

    def test_session_phase_executing(self) -> None:
        self.assertEqual(_session_phase({"executing": True}), "executing")
        self.assertEqual(_session_phase({"executing": False}), "idle")


class TestTeleopSessionWsMessages(unittest.IsolatedAsyncioTestCase):
    async def test_ping_replies_pong(self) -> None:
        ws = AsyncMock()
        lab = MagicMock()
        await _handle_client_message(
            lab, "tag_1", {"type": "ping", "ts_ms": 123}, ws
        )
        ws.send_json.assert_awaited_once_with({"type": "pong", "ts_ms": 123})

    async def test_goto_delegates_to_teleop_controller(self) -> None:
        ws = AsyncMock()
        lab = MagicMock()
        lab._teleop.goto = AsyncMock()
        msg = {
            "type": "goto",
            "target_pose": {"rotation": 45.0},
            "speed": {"angular_deg_s": 15.0},
        }
        await _handle_client_message(lab, "tag_1", msg, ws)
        lab._teleop.goto.assert_awaited_once_with(
            "tag_1",
            {
                "target_pose": {"rotation": 45.0},
                "speed": {"angular_deg_s": 15.0},
            },
        )

    async def test_unknown_type_returns_error(self) -> None:
        ws = AsyncMock()
        lab = MagicMock()
        await _handle_client_message(lab, "tag_1", {"type": "nope"}, ws)
        payload = ws.send_json.await_args.args[0]
        self.assertEqual(payload["type"], "error")


if __name__ == "__main__":
    unittest.main()
