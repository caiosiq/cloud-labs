"""Tests for pose refresh offers (tolerance-based dry-run)."""

from __future__ import annotations

import unittest

from mock_edge.shared.mock_scan_preview import build_mock_scan_proposed_poses
from mock_edge.shared.session_checkpoint import ReconciliationThresholds
from lab_model.language.domain.component import new_component_entry
from lab_model.coordinator.state.pose_refresh_offers import build_pose_refresh_offers


class PoseRefreshOffersTests(unittest.TestCase):
    def _table_component(self, tag_id: str, x: float, y: float, rot: float = 0.0):
        return new_component_entry(
            tag_id,
            "TEST_PART",
            presence="breadboard",
            nominal_pose={"x": x, "y": y, "rotation": rot},
            meas_pose={"x": x, "y": y, "rotation": rot},
        )

    def test_build_offers_marks_within_tolerance(self) -> None:
        comp = self._table_component("tag_a", 10.0, 20.0, 5.0)
        components = {"tag_a": comp}
        proposed = build_mock_scan_proposed_poses(components)
        self.assertIn("tag_a", proposed)

        offers = build_pose_refresh_offers(
            components,
            proposed,
            ReconciliationThresholds(position_mm=50.0, yaw_deg=50.0),
        )
        self.assertEqual(len(offers), 1)
        self.assertTrue(offers[0]["within_tolerance"])
        self.assertFalse(offers[0]["default_apply"])

    def test_build_offers_marks_significant_delta(self) -> None:
        comp = self._table_component("tag_a", 10.0, 20.0, 5.0)
        components = {"tag_a": comp}
        proposed = {"tag_a": {"x": 100.0, "y": 200.0, "rotation": 90.0}}
        offers = build_pose_refresh_offers(
            components,
            proposed,
            ReconciliationThresholds(position_mm=2.0, yaw_deg=5.0),
        )
        self.assertEqual(len(offers), 1)
        self.assertFalse(offers[0]["within_tolerance"])
        self.assertTrue(offers[0]["default_apply"])
        self.assertGreater(offers[0]["delta_mm"], 2.0)

    def test_mock_preview_is_deterministic(self) -> None:
        comp = self._table_component("tag_b", 1.0, 2.0, 3.0)
        components = {"tag_b": comp}
        first = build_mock_scan_proposed_poses(components)
        second = build_mock_scan_proposed_poses(components)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
