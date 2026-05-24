"""PICK must preserve table XY from nominal_pose when measurables.pose is null."""
from __future__ import annotations

import unittest

from lab_model.domain.component import resolve_pick_table_pose


class TestPickPoseResolution(unittest.TestCase):
    def test_falls_back_to_nominal_when_meas_null(self) -> None:
        entry = {
            "statecontrol": {
                "tunables": {
                    "nominal_pose": {"x": 120.5, "y": -45.0, "rotation": 15.0},
                },
                "measurables": {"pose": None},
            },
            "telemetry": {"teleop": {}, "live_feed": {}},
        }
        pose = resolve_pick_table_pose(entry)
        self.assertAlmostEqual(pose["x"], 120.5)
        self.assertAlmostEqual(pose["y"], -45.0)
        self.assertAlmostEqual(pose["rotation"], 15.0)

    def test_prefers_meas_when_present(self) -> None:
        entry = {
            "statecontrol": {
                "tunables": {
                    "nominal_pose": {"x": 1.0, "y": 2.0, "rotation": 0.0},
                },
                "measurables": {
                    "pose": {"x": 10.0, "y": 20.0, "rotation": 90.0, "z": 0.0},
                },
            },
            "telemetry": {"teleop": {}, "live_feed": {}},
        }
        pose = resolve_pick_table_pose(entry)
        self.assertAlmostEqual(pose["x"], 10.0)
        self.assertAlmostEqual(pose["y"], 20.0)
        self.assertAlmostEqual(pose["rotation"], 90.0)


if __name__ == "__main__":
    unittest.main()
