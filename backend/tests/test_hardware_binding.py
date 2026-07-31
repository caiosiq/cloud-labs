"""hardware_binding resolver (mock_backend catalog ``parameters``)."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from lab_model.coordinator.catalog.schema import (
    catalog_is_fixed_instrument,
    catalog_is_placeable_on_table,
    resolve_cam_id_for_tag,
    resolve_hardware_binding,
    resolve_telemetry_stream_backend,
)
from lab_model.language.domain.component import get_tunables, new_component_entry
from lab_model.coordinator.state.fixture_seed import enrich_runtime_entry_from_catalog

_MOCK_LIB = (
    Path(__file__).resolve().parents[2]
    / "mock_backend"
    / "lab_view"
    / "component_library.json"
)


def _load_mock_catalog_row(tag_id: str) -> dict:
    with open(_MOCK_LIB, encoding="utf-8") as f:
        data = json.load(f)
    row = (data.get("components") or {}).get(tag_id)
    if not isinstance(row, dict):
        raise KeyError(tag_id)
    return row


class TestHardwareBinding(unittest.TestCase):
    def test_tag_99_binding_opencv_overhead(self) -> None:
        row = _load_mock_catalog_row("tag_99")
        binding = resolve_hardware_binding(row)
        self.assertIsNotNone(binding)
        assert binding is not None
        self.assertEqual(binding.backend, "opencv_usb")
        self.assertEqual(resolve_telemetry_stream_backend(row), "overhead")
        self.assertIsNone(resolve_cam_id_for_tag(row))
        self.assertTrue(catalog_is_fixed_instrument(row))

    def test_tag_22_binding_recorder_tcp(self) -> None:
        row = _load_mock_catalog_row("tag_22")
        binding = resolve_hardware_binding(row)
        self.assertIsNotNone(binding)
        assert binding is not None
        self.assertEqual(binding.backend, "recorder_tcp")
        self.assertEqual(binding.recorder_cam_id, 1)
        self.assertEqual(resolve_cam_id_for_tag(row), 1)
        self.assertEqual(resolve_telemetry_stream_backend(row), "table_cam")

    def test_mock_gripper_cameras_placeable_on_table(self) -> None:
        for tag_id in ("tag_21", "tag_22"):
            row = _load_mock_catalog_row(tag_id)
            self.assertTrue(catalog_is_placeable_on_table(row))
            self.assertFalse(catalog_is_fixed_instrument(row))

    def test_enrich_runtime_entry_from_catalog(self) -> None:
        row = _load_mock_catalog_row("tag_22")
        entry = new_component_entry(
            "tag_22",
            "OPTICAL_CAMERA",
            presence="breadboard",
            nominal_pose={"x": 0.0, "y": 0.0, "rotation": 0.0},
            meas_pose={"x": 0.0, "y": 0.0, "rotation": 0.0},
        )
        enrich_runtime_entry_from_catalog(entry, row)
        self.assertIn("presence", get_tunables(entry))


if __name__ == "__main__":
    unittest.main()
