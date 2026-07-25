import json
import unittest
from pathlib import Path

import mujoco

from simulation_edge.bootstrap import _resolve_catalog_rows
from simulation_edge.host.scene import build_scene_spec, load_simulation_profile


LAB_VIEW = Path(__file__).resolve().parents[1] / "lab_view"


class StorageGeometryTests(unittest.TestCase):
    def test_storage_grid_top_right_corner_is_negative_one_mm(self):
        layout = json.loads((LAB_VIEW / "layout.json").read_text(encoding="utf-8"))
        bounds = layout["storage"]["bounds_mm"]
        self.assertEqual(bounds["x_max"], -1)
        self.assertEqual(bounds["y_max"], -1)
        self.assertEqual(bounds["x_min"], -361)
        self.assertEqual(bounds["y_min"], -361)

    def test_optical_housing_profile_has_75_mm_base(self):
        profile = load_simulation_profile("optical_housings")
        self.assertEqual(profile.base_cylinder_diameter_m, 0.075)
        self.assertEqual(profile.base_cylinder_height_m, 0.019)

    def test_generated_scene_contains_compound_collision_geometry(self):
        layout = json.loads((LAB_VIEW / "layout.json").read_text(encoding="utf-8"))
        state = json.loads((LAB_VIEW / "lab_state.json").read_text(encoding="utf-8"))
        scene = build_scene_spec(
            layout,
            _resolve_catalog_rows(LAB_VIEW),
            state,
            profile_id="optical_housings",
        )

        self.assertGreater(len(scene.components), 0)
        self.assertEqual(scene.xml.count('type="cylinder"'), len(scene.components))
        self.assertIn('size="0.03750000 0.00950000"', scene.xml)
        self.assertFalse(
            {"tag_9", "tag_2", "tag_10"} & set(scene.spawn_adjustments_mm)
        )
        model = mujoco.MjModel.from_xml_string(scene.xml)
        self.assertGreater(model.ngeom, 0)


if __name__ == "__main__":
    unittest.main()
