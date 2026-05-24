"""Phase 10: TeleOp hardware mutex + gripper slip detection."""
from __future__ import annotations

import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from lab_communicator.real import teleop_bridge


class TestTeleopHardwareMutex(unittest.TestCase):
    def tearDown(self) -> None:
        with teleop_bridge._HW_SESSION_LOCK:
            teleop_bridge._ACTIVE_HW_TAG = None

    def test_mutex_refuses_second_session(self) -> None:
        teleop_bridge._ACTIVE_HW_TAG = "tag_1"
        comm = SimpleNamespace(
            experiment=SimpleNamespace(),
            get_manipulable=MagicMock(return_value=object()),
        )
        with self.assertRaises(RuntimeError) as ctx:
            teleop_bridge.start_hardware_session(comm, "tag_2")
        self.assertIn("mutex", str(ctx.exception).lower())


class TestGripperSlipCheck(unittest.TestCase):
    def test_pose3d_open_gripper_returns_error(self) -> None:
        comm = SimpleNamespace()
        with patch.object(
            teleop_bridge,
            "resolve_teleop_mode",
            return_value="pose3d",
        ), patch(
            "lab_communicator.real.gripper.get_gripper_status",
            return_value={"closed": False, "source": "test"},
        ):
            err = teleop_bridge.check_gripper_slip_during_teleop(comm, "tag_8")
        self.assertIsNotNone(err)
        assert err is not None
        self.assertIn("slip", err.lower())

    def test_rz_mode_skips_slip_check(self) -> None:
        comm = SimpleNamespace()
        with patch.object(
            teleop_bridge,
            "resolve_teleop_mode",
            return_value="rz",
        ):
            err = teleop_bridge.check_gripper_slip_during_teleop(comm, "tag_8")
        self.assertIsNone(err)

    def test_unavailable_probe_skips_slip_check(self) -> None:
        comm = SimpleNamespace()
        with patch.object(
            teleop_bridge,
            "resolve_teleop_mode",
            return_value="pose3d",
        ), patch(
            "lab_communicator.real.gripper.get_gripper_status",
            return_value={"closed": False, "source": "unavailable (no probe)"},
        ):
            err = teleop_bridge.check_gripper_slip_during_teleop(comm, "tag_8")
        self.assertIsNone(err)


class TestIsPlacedFromLabState(unittest.TestCase):
    def test_held_tag_not_placed(self) -> None:
        entry = {"statecontrol": {"tunables": {"presence": "breadboard"}}}
        comm = SimpleNamespace(
            current_state={
                "holding": {"tag_id": "tag_8"},
                "system_status": "HOLDING",
            },
            _state_lock=nullcontext(),
        )
        self.assertFalse(
            teleop_bridge._is_placed_from_lab_state(comm, "tag_8", entry)
        )


if __name__ == "__main__":
    unittest.main()
