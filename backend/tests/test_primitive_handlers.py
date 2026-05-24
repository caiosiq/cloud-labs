"""Phase 0: primitive handler coverage audit (no hardware required)."""
from __future__ import annotations

import unittest

from lab_communicator.base import LabCommunicator
from lab_communicator.mock.communicator import MockLabCommunicator
from lab_communicator.real.communicator import RealLabCommunicator
from lab_model import measurables as _measurables  # noqa: F401
from lab_model import tunables as _tunables  # noqa: F401
from lab_model.domain.component import new_component_entry
from lab_model.platform import (
    assert_primitive_handlers_wired,
    audit_primitive_handlers,
    audit_real_hardware_hooks,
    validate_platform_integrity,
)


class TestPrimitiveHandlerAudit(unittest.TestCase):
    def test_lab_communicator_handlers_complete(self) -> None:
        assert_primitive_handlers_wired(LabCommunicator, label="LabCommunicator")

    def test_mock_subclass_handlers_complete(self) -> None:
        assert_primitive_handlers_wired(MockLabCommunicator, label="MockLabCommunicator")

    def test_real_subclass_handlers_complete(self) -> None:
        assert_primitive_handlers_wired(RealLabCommunicator, label="RealLabCommunicator")

    def test_platform_integrity(self) -> None:
        validate_platform_integrity()

    def test_real_hardware_hooks_present(self) -> None:
        rows = audit_real_hardware_hooks(RealLabCommunicator)
        missing = [r["hook"] for r in rows if r["status"] == "missing"]
        self.assertEqual(missing, [], f"missing real hooks: {missing}")

    def test_teleop_prepare_overridden_on_real(self) -> None:
        """Phase 6 — real prepare enters lab_automation LiveControlSession."""
        from lab_communicator.base import LabCommunicator
        from lab_communicator.real.communicator import RealLabCommunicator

        self.assertIsNot(
            RealLabCommunicator._primitive_prepare_teleop,
            LabCommunicator._primitive_prepare_teleop,
        )

    def test_scan_emits_v1_component_shape(self) -> None:
        entry = new_component_entry(
            "tag_1",
            "OPTICAL_MIRROR",
            presence="breadboard",
            nominal_pose={"x": 1.0, "y": 2.0, "rotation": 0.0},
            meas_pose={"x": 1.0, "y": 2.0, "rotation": 0.0},
        )
        self.assertIn("statecontrol", entry)
        self.assertIn("telemetry", entry)
        self.assertIn("tunables", entry["statecontrol"])
        self.assertIn("measurables", entry["statecontrol"])

    def test_audit_report_has_no_missing_handlers(self) -> None:
        for cls, label in (
            (LabCommunicator, "LabCommunicator"),
            (MockLabCommunicator, "MockLabCommunicator"),
            (RealLabCommunicator, "RealLabCommunicator"),
        ):
            missing = [
                r["primitive"]
                for r in audit_primitive_handlers(cls, label=label)
                if r["status"] == "missing"
            ]
            self.assertEqual(missing, [], f"{label} missing: {missing}")


if __name__ == "__main__":
    unittest.main()
