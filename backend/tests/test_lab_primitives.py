import asyncio
import os
import pathlib
import re
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


class StageCInvariantsTests(unittest.TestCase):
    """Guard rails for ``fixing.md`` §9 Stage C.

    Stage C sealed the state-ownership boundary between cloud-labs and
    ``lab_automation``:
      * ``.inventory_location`` attribute access is forbidden in
        ``real.py`` -- cloud-labs ignores the field entirely and lets
        ``lab_automation`` own it (see ``fixing.md`` §6.1 and
        ``labautomation_new_primitives.md`` §5).
      * Writes to ``.current_location`` happen only inside ``set_lab_state``
        (snapshot load is the single cross-wall sync point).
      * Writes to ``.is_placed`` happen only inside ``set_lab_state`` and
        ``affirm_placed_at_current`` (the manual-place flow -- see
        ``fixing.md`` §9 Stage C6 option (a)).

    Any future PR that tries to re-open one of those leaks will fail this
    test and route the author back to the fixing.md discussion.
    """

    _CURRENT_LOCATION_WRITE_ALLOWED = frozenset({"set_lab_state"})
    _IS_PLACED_WRITE_ALLOWED = frozenset({"set_lab_state", "affirm_placed_at_current"})

    # Matches ``def <name>(`` / ``async def <name>(`` at one level of
    # indentation (4 spaces) -- i.e. RealLabCommunicator's method bodies.
    _METHOD_HEADER_RE = re.compile(r"^    (?:async\s+)?def\s+(\w+)\s*\(", re.MULTILINE)

    @classmethod
    def setUpClass(cls) -> None:
        # backend/tests/test_lab_primitives.py -> backend/lab_communicator/real.py
        here = pathlib.Path(__file__).resolve()
        cls.real_py_path = here.parents[1] / "lab_communicator" / "real.py"
        cls.real_py_src = cls.real_py_path.read_text(encoding="utf-8")

    def _iter_methods(self):
        """Yield ``(method_name, method_body)`` pairs from ``real.py``.

        Uses a simple indentation-based heuristic (not the ``ast`` module)
        so the test stays cheap and does not depend on ``lab_automation``
        being importable. Good enough for the CI lint we need.
        """
        src = self.real_py_src
        matches = list(self._METHOD_HEADER_RE.finditer(src))
        for i, m in enumerate(matches):
            start = m.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(src)
            yield m.group(1), src[start:end]

    @staticmethod
    def _strip_line_comments(body: str) -> str:
        """Drop ``# ...`` trailing comments so prose mentions of the names
        we enforce don't trip the lint. Intentionally does not strip
        docstrings -- we don't currently assign to these attributes inside
        docstrings, and handling that properly needs the ``tokenize``
        module."""
        return "\n".join(line.split("#", 1)[0] for line in body.splitlines())

    def test_no_inventory_location_attribute_access_in_real_py(self) -> None:
        pattern = re.compile(r"\.inventory_location\b")
        for name, body in self._iter_methods():
            code = self._strip_line_comments(body)
            if pattern.search(code):
                self.fail(
                    f"real.py::{name} contains ``.inventory_location`` "
                    f"attribute access, forbidden by fixing.md §9 Stage C. "
                    f"Use ``.current_location`` instead."
                )

    def test_current_location_writes_only_in_set_lab_state(self) -> None:
        # ``=`` without a following ``=`` = real assignment, not ``==``.
        pattern = re.compile(r"\.current_location\s*=(?!=)")
        for name, body in self._iter_methods():
            code = self._strip_line_comments(body)
            if pattern.search(code) and name not in self._CURRENT_LOCATION_WRITE_ALLOWED:
                self.fail(
                    f"real.py::{name} writes ``.current_location``; that is "
                    f"only allowed inside "
                    f"{sorted(self._CURRENT_LOCATION_WRITE_ALLOWED)} "
                    f"(fixing.md §9 Stage C6). Treat lab_automation state "
                    f"as owned by lab_automation -- our snapshot loader is "
                    f"the single cross-wall sync point."
                )

    def test_is_placed_writes_only_in_allowed_sites(self) -> None:
        pattern = re.compile(r"\.is_placed\s*=(?!=)")
        for name, body in self._iter_methods():
            code = self._strip_line_comments(body)
            if pattern.search(code) and name not in self._IS_PLACED_WRITE_ALLOWED:
                self.fail(
                    f"real.py::{name} writes ``.is_placed``; that is only "
                    f"allowed inside "
                    f"{sorted(self._IS_PLACED_WRITE_ALLOWED)} "
                    f"(fixing.md §9 Stage C6, option (a) keeps the manual-"
                    f"place flow)."
                )


if __name__ == "__main__":
    unittest.main()
