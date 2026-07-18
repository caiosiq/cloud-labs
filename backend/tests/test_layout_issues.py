"""Layout conflict detection uses committed intent pose, not stale measurables."""
from __future__ import annotations

import unittest

from lab_model.language.domain.component import new_component_entry, PRESENCE_BREADBOARD, PRESENCE_STORAGE
from lab_model.language.domain.storage_region import analyze_layout_issues, configure_from_layout_document


LAYOUT = {
    "lab_bounds_mm": {
        "x_min": -500,
        "x_max": 500,
        "y_min": -500,
        "y_max": 500,
    },
    "danger_zone": {"radius_mm": 90, "padding_mm": 5},
    "storage": {
        "rule": "negative_xy",
        "grid_nx": 4,
        "grid_ny": 4,
        "extent_from_origin_mm": {"width_mm": 300, "height_mm": 380},
    },
}


class TestLayoutIssues(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        configure_from_layout_document(LAYOUT)

    def test_stored_nominal_in_q3_stale_meas_on_breadboard_no_conflict(self) -> None:
        entry = new_component_entry(
            "tag_19",
            "OPTICAL_CRYSTAL",
            presence=PRESENCE_STORAGE,
            nominal_pose={"x": -37.5, "y": -332.5, "rotation": 0.0},
            meas_pose={"x": 157.1, "y": 189.3, "rotation": 0.0},
            placement_mode="STORAGE",
            in_storage=True,
            slot={"i": 3, "j": 0},
        )
        issues = analyze_layout_issues({"tag_19": entry}, lambda _tid: (62.0, 62.0))
        kinds = {i["kind"] for i in issues}
        self.assertNotIn("STORED_OUTSIDE_Q3", kinds)
        self.assertNotIn("STORED_OFF_SLOT", kinds)

    def test_stored_nominal_outside_q3_reports_conflict(self) -> None:
        entry = new_component_entry(
            "tag_19",
            "OPTICAL_CRYSTAL",
            presence=PRESENCE_STORAGE,
            nominal_pose={"x": 150.0, "y": 150.0, "rotation": 0.0},
            meas_pose={"x": 150.0, "y": 150.0, "rotation": 0.0},
            placement_mode="STORAGE",
            in_storage=True,
            slot={"i": 0, "j": 0},
        )
        issues = analyze_layout_issues({"tag_19": entry}, lambda _tid: (62.0, 62.0))
        self.assertTrue(any(i["kind"] == "STORED_OUTSIDE_Q3" for i in issues))

    def test_placed_nominal_on_breadboard_stale_meas_in_q3_no_conflict(self) -> None:
        entry = new_component_entry(
            "tag_9",
            "OPTICAL_MIRROR",
            presence=PRESENCE_BREADBOARD,
            nominal_pose={"x": 200.0, "y": 200.0, "rotation": 0.0},
            meas_pose={"x": -37.5, "y": -332.5, "rotation": 0.0},
            placement_mode="MANUAL",
            in_storage=False,
            slot=None,
        )
        issues = analyze_layout_issues({"tag_9": entry}, lambda _tid: (62.0, 62.0))
        self.assertFalse(any(i["kind"] == "PLACED_IN_Q3" for i in issues))


if __name__ == "__main__":
    unittest.main()
