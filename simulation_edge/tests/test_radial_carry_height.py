import json
import unittest
from pathlib import Path

from simulation_edge.host.runtime import RADIAL_DEFAULT_CARRY_Z_M
from simulation_edge.host.scene import TABLE_SURFACE_Z_M


SIMULATION_EDGE = Path(__file__).resolve().parents[1]
LIBRARY_PATH = (
    SIMULATION_EDGE / "radial_motion_libraries" / "optical_housings.json"
)
PROFILE_PATH = (
    SIMULATION_EDGE / "simulation_profiles" / "optical_housings" / "profile.json"
)


class RadialCarryHeightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.library = json.loads(LIBRARY_PATH.read_text(encoding="utf-8"))
        cls.profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))

    def test_runtime_and_library_use_550_mm_world_tcp_height(self):
        self.assertEqual(RADIAL_DEFAULT_CARRY_Z_M, 0.550)
        self.assertEqual(self.library["carry_z_m"], 0.550)

    def test_world_tcp_carry_is_430_mm_above_table(self):
        self.assertEqual(TABLE_SURFACE_Z_M, 0.120)
        self.assertAlmostEqual(
            RADIAL_DEFAULT_CARRY_Z_M - TABLE_SURFACE_Z_M,
            0.430,
            places=9,
        )

    def test_library_samples_promote_550_mm_pose_to_canonical(self):
        for sample in self.library["samples"]:
            carry_pose = next(
                pose
                for pose in sample["vertical_poses"]
                if pose["z_m"] == 0.550
            )
            self.assertEqual(sample["joints"], carry_pose["joints"])

    def test_each_sample_has_one_canonical_carry_pose(self):
        carry_z_m = self.library["carry_z_m"]
        for sample in self.library["samples"]:
            carry_poses = [
                pose
                for pose in sample["vertical_poses"]
                if pose["z_m"] == carry_z_m
            ]
            self.assertEqual(len(carry_poses), 1)

    def test_safe_radial_range_is_unchanged(self):
        radii_mm = [
            float(sample["radius_m"]) * 1000.0
            for sample in self.library["samples"]
        ]
        self.assertEqual(min(radii_mm), 134.0)
        self.assertEqual(max(radii_mm), 473.0)

    def test_component_bottom_clears_component_top_by_more_than_one_inch(self):
        geometry = self.profile["geometry"]
        grasp_height_m = float(geometry["grasp_height_mm"]) / 1000.0
        component_height_m = (
            float(geometry["collision_size_mm"][2]) / 1000.0
        )
        carried_bottom_z_m = RADIAL_DEFAULT_CARRY_Z_M - grasp_height_m
        resting_top_z_m = TABLE_SURFACE_Z_M + component_height_m

        self.assertAlmostEqual(
            carried_bottom_z_m - resting_top_z_m,
            0.028588,
            places=6,
        )
        self.assertGreater(
            carried_bottom_z_m - resting_top_z_m,
            0.0254,
        )


if __name__ == "__main__":
    unittest.main()
