import asyncio
import os
import tempfile
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from lab_communicator.base import LabCommunicator
from lab_model import motor_rotation_store as mrs
from lab_primitives.dispatch import execute_validated_command, fetch_read_primitive, parse_command_payload
from lab_primitives.ids import MACRO_PRIMITIVE_IDS, PrimitiveId
from lab_primitives.protocol import LabPrimitiveSurface
from lab_primitives.registry import PRIMITIVE_REGISTRY


class _MotorOnlyLab(LabCommunicator):
    """Minimal lab for macro tests: only ``move_motor`` + rotation store side effects."""

    def __init__(self):
        self.moves: list[tuple[str, int, float]] = []

    def get_lab_state(self) -> dict:
        return {"components": {}}

    async def move_motor(self, target_id: str, motor_id: int, distance: float) -> None:
        self.moves.append((target_id, motor_id, distance))
        mrs.add_delta(target_id, motor_id, distance)


class LabPrimitivesTests(unittest.TestCase):
    def test_registry_has_every_primitive_id(self) -> None:
        for pid in PrimitiveId:
            self.assertIn(pid, PRIMITIVE_REGISTRY, msg=f"missing registry row for {pid!r}")

    def test_motor_send_home_is_macro(self) -> None:
        self.assertIn(PrimitiveId.MOTOR_SEND_HOME, MACRO_PRIMITIVE_IDS)

    def test_motor_send_home_macro_zeroes_angle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rot_path = os.path.join(tmp, "mock_motor_rotations.json")
            with patch.object(mrs, "_mode_path", return_value=rot_path):
                mrs.add_delta("t_macro", 1, 12.5)
                self.assertAlmostEqual(mrs.get_angle("t_macro", 1), 12.5)
                lab = _MotorOnlyLab()
                cmd = parse_command_payload(
                    {
                        "action": "MOTOR_SEND_HOME",
                        "target_id": "t_macro",
                        "parameters": {"motor_id": 1},
                    }
                )
                asyncio.run(execute_validated_command(lab, cmd))
                self.assertAlmostEqual(mrs.get_angle("t_macro", 1), 0.0)
                self.assertEqual(lab.moves, [("t_macro", 1, -12.5)])

    def test_move_motor_validation_error_on_bad_payload(self) -> None:
        with self.assertRaises(ValidationError):
            parse_command_payload(
                {"action": "MOVE_MOTOR", "target_id": "x", "parameters": {}}
            )

    def test_lab_primitive_surface_runtime_check(self) -> None:
        lab = _MotorOnlyLab()
        self.assertIsInstance(lab, LabPrimitiveSurface)

    def test_fetch_read_uses_return_helpers(self) -> None:
        class _SliceLab(LabCommunicator):
            def get_lab_state(self) -> dict:
                return {
                    "components": {
                        "tag_a": {
                            "tunables": {"presence": "breadboard"},
                            "measurables": {"pose": {"x": 1.0}},
                        }
                    }
                }

        lab = _SliceLab()
        self.assertEqual(
            fetch_read_primitive(lab, PrimitiveId.GET_TUNABLES, "tag_a")["presence"],
            "breadboard",
        )
        self.assertEqual(
            fetch_read_primitive(lab, PrimitiveId.GET_MEASURABLES, "tag_a")["pose"]["x"],
            1.0,
        )


if __name__ == "__main__":
    unittest.main()
