"""Phase 6: Real TeleOp bridge unit tests (no hardware)."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from lab_communicator.real import teleop_bridge


class TestTeleopBridgeTransforms(unittest.TestCase):
    def test_lab_target_rz_rotation_only(self) -> None:
        comm = SimpleNamespace()
        body = teleop_bridge.lab_target_to_hardware(
            comm, "tag_1", {"rotation": 45.0, "x": 1.0}, mode="rz"
        )
        self.assertEqual(body, {"rotation": 45.0})

    def test_lab_target_pose3d_maps_xy_z(self) -> None:
        comm = SimpleNamespace()
        comm._z_lab_to_robot = MagicMock(return_value=500.0)
        with patch(
            "lab_communicator.real.coordinate_frames.lab_table_xy_to_robot_xy",
            return_value=(10.0, 20.0),
        ):
            body = teleop_bridge.lab_target_to_hardware(
                comm,
                "tag_1",
                {"x": 1.0, "y": 2.0, "z": 40.0, "rotation": 90.0},
                mode="pose3d",
            )
        self.assertEqual(body["x"], 10.0)
        self.assertEqual(body["y"], 20.0)
        self.assertEqual(body["z"], 500.0)
        self.assertEqual(body["rotation"], 90.0)

    def test_hardware_pose_to_lab(self) -> None:
        comm = SimpleNamespace()
        comm._z_robot_to_lab = MagicMock(return_value=42.0)
        with patch(
            "lab_communicator.real.coordinate_frames.robot_table_xy_to_lab_xy",
            return_value=(1.5, 2.5),
        ):
            out = teleop_bridge.hardware_pose_to_lab(
                comm,
                "tag_1",
                {"x": 100.0, "y": 200.0, "z": 400.0, "rotation": 30.0, "executing": True},
            )
        self.assertEqual(out["x"], 1.5)
        self.assertEqual(out["y"], 2.5)
        self.assertEqual(out["z"], 42.0)
        self.assertEqual(out["rotation"], 30.0)
        self.assertTrue(out["executing"])


class TestRealPrepareTeleopOverride(unittest.TestCase):
    def test_prepare_is_overridden_on_real(self) -> None:
        from lab_communicator.base import LabCommunicator
        from lab_communicator.real.communicator import RealLabCommunicator

        self.assertIsNot(
            RealLabCommunicator._primitive_prepare_teleop,
            LabCommunicator._primitive_prepare_teleop,
        )


if __name__ == "__main__":
    unittest.main()
