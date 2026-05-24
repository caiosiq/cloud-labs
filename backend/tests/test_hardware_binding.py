"""Phase 2: hardware_binding resolver and fixture seeding."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from lab_model.catalog.schema import (
    catalog_is_fixed_instrument,
    resolve_cam_id_for_tag,
    resolve_hardware_binding,
    resolve_telemetry_stream_backend,
)
from lab_model.domain.component import get_tunables
from lab_model.state.fixture_seed import build_fixture_component_entry, merge_fixture_components


def _load_real_catalog_row(tag_id: str) -> dict:
    lib = (
        Path(__file__).resolve().parents[1]
        / "lab_communicator"
        / "real"
        / "lab_view"
        / "default"
        / "component_library.json"
    )
    with open(lib, encoding="utf-8") as f:
        data = json.load(f)
    row = (data.get("components") or {}).get(tag_id)
    if not isinstance(row, dict):
        raise KeyError(tag_id)
    return row


class TestHardwareBinding(unittest.TestCase):
    def test_tag_99_binding_opencv_overhead(self) -> None:
        row = _load_real_catalog_row("tag_99")
        binding = resolve_hardware_binding(row)
        self.assertIsNotNone(binding)
        assert binding is not None
        self.assertEqual(binding.backend, "opencv_usb")
        self.assertEqual(binding.device_index, 0)
        self.assertEqual(resolve_telemetry_stream_backend(row), "overhead")
        self.assertIsNone(resolve_cam_id_for_tag(row))

    def test_tag_22_binding_recorder_tcp(self) -> None:
        row = _load_real_catalog_row("tag_22")
        binding = resolve_hardware_binding(row)
        self.assertIsNotNone(binding)
        assert binding is not None
        self.assertEqual(binding.backend, "recorder_tcp")
        self.assertEqual(binding.recorder_cam_id, 1)
        self.assertEqual(resolve_cam_id_for_tag(row), 1)
        self.assertEqual(resolve_telemetry_stream_backend(row), "table_cam")

    def test_fixed_instruments_detected(self) -> None:
        self.assertTrue(catalog_is_fixed_instrument(_load_real_catalog_row("tag_99")))
        self.assertTrue(catalog_is_fixed_instrument(_load_real_catalog_row("tag_22")))

    def test_fixture_entry_v1_shape(self) -> None:
        row = _load_real_catalog_row("tag_99")
        entry = build_fixture_component_entry(row)
        self.assertIn("statecontrol", entry)
        self.assertIn("telemetry", entry)
        self.assertIsNone(entry["statecontrol"]["measurables"]["pose"])
        self.assertEqual(
            entry["telemetry"]["live_feed"]["stream"]["backend"],
            "overhead",
        )

    def test_merge_fixture_adds_missing_tag(self) -> None:
        row = _load_real_catalog_row("tag_99")
        merged = merge_fixture_components({}, [row])
        self.assertIn("tag_99", merged)
        tun = get_tunables(merged["tag_99"])
        self.assertEqual(tun.get("exposure_time_ms"), 50.0)


if __name__ == "__main__":
    unittest.main()
