"""Phase 0: primitive handler coverage audit (no hardware required)."""
from __future__ import annotations

import unittest

from mock_edge.host.base import LabCommunicator
from mock_edge.host.communicator import MockLabCommunicator
from lab_model.language import measurables as _measurables  # noqa: F401
from lab_model.language import tunables as _tunables  # noqa: F401
from lab_model.language.domain.component import new_component_entry
from lab_model.platform import (
    assert_primitive_handlers_wired,
    audit_primitive_handlers,
    validate_platform_integrity,
)


class TestPrimitiveHandlerAudit(unittest.TestCase):
    def test_lab_communicator_handlers_complete(self) -> None:
        assert_primitive_handlers_wired(LabCommunicator, label="LabCommunicator")

    def test_mock_subclass_handlers_complete(self) -> None:
        assert_primitive_handlers_wired(MockLabCommunicator, label="MockLabCommunicator")

    def test_platform_integrity(self) -> None:
        validate_platform_integrity()

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
        ):
            missing = [
                r["primitive"]
                for r in audit_primitive_handlers(cls, label=label)
                if r["status"] == "missing"
            ]
            self.assertEqual(missing, [], f"{label} missing: {missing}")


if __name__ == "__main__":
    unittest.main()
