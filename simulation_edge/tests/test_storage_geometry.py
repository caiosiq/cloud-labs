import json
import unittest
from pathlib import Path

import mujoco
import numpy as np

from simulation_edge.bootstrap import _resolve_catalog_rows
from simulation_edge.host.scene import (
    ROBOT_MOUNTING_PLANE_Z_M,
    ROBOT_MOUNTING_PLATE_OBJECT_ID,
    ROBOT_MOUNTING_PLATE_THICKNESS_M,
    ROBOT_MOUNTING_PLATE_X_M,
    ROBOT_MOUNTING_PLATE_Y_M,
    STORAGE_TABLETOP_OVERLAY_GEOM_NAME,
    STORAGE_TABLETOP_OVERLAY_THICKNESS_M,
    TABLE_SURFACE_Z_M,
    build_scene_spec,
    load_simulation_profile,
)


LAB_VIEW = Path(__file__).resolve().parents[1] / "lab_view"


class StorageGeometryTests(unittest.TestCase):
    def test_storage_grid_matches_experimental_three_by_two_layout(self):
        layout = json.loads((LAB_VIEW / "layout.json").read_text(encoding="utf-8"))
        storage = layout["storage"]
        self.assertEqual((storage["grid_nx"], storage["grid_ny"]), (3, 2))
        self.assertEqual(
            storage["bounds_mm"],
            {"x_min": -150, "x_max": 150, "y_min": -400, "y_max": -160},
        )

    def test_optical_housing_profile_has_75_mm_base(self):
        profile = load_simulation_profile("optical_housings")
        self.assertEqual(profile.base_cylinder_diameter_m, 0.075)
        self.assertEqual(profile.base_cylinder_height_m, 0.019)
        self.assertEqual(profile.rgba, (0.55, 0.58, 0.64, 1.0))
        self.assertEqual(profile.visual_clearance_size_m, (0.05, 0.05, 0.1))
        expected_meshes = {
            "tag_9": "NE10A.stl",
            "tag_2": "LB1.stl",
            "tag_20": "BB1-E03.stl",
            "tag_99": "CS165MU.stl",
            "tag_19": "NLC02.stl",
            "tag_21": "CS165MU.stl",
            "tag_22": "CS165MU.stl",
            "tag_50": "CPS635R.stl",
            "tag_10": "PBS251.stl",
            "tag_11": "LA1509-A.stl",
            "tag_18": "CM254-050-E02.stl",
        }
        self.assertEqual(
            {visual.tag_id: visual.mesh_path.name for visual in profile.component_visuals},
            expected_meshes,
        )
        assert profile.visual_clearance_size_m is not None
        for visual in profile.component_visuals:
            self.assertAlmostEqual(visual.support_plane_from_bottom_m, 0.121412)
            lower, upper = visual.transformed_bounds_m()
            extent = tuple(high - low for low, high in zip(lower, upper))
            self.assertLessEqual(extent[0], profile.visual_clearance_size_m[0])
            self.assertLessEqual(extent[1], profile.visual_clearance_size_m[1])
            self.assertLessEqual(extent[2], profile.visual_clearance_size_m[2])
            rotation = np.empty(9, dtype=float)
            mujoco.mju_quat2Mat(rotation, np.asarray(visual.quat_wxyz))
            front_axis = rotation.reshape(3, 3) @ np.asarray(visual.front_axis_xyz)
            np.testing.assert_allclose(front_axis, (0.0, 1.0, 0.0), atol=1e-7)

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
        self.assertIn('diffuse="0.5 0.5 0.5" ambient="0.2 0.2 0.2"', scene.xml)
        self.assertIn('orthographic="false" fovy="45"', scene.xml)
        self.assertIn('offwidth="3840" offheight="2160"', scene.xml)
        self.assertIn('rgba="0.28 0.34 0.42 1" friction="1 0.01 0.001"', scene.xml)
        self.assertNotIn('name="table_grid"', scene.xml)
        self.assertNotIn('name="table_material"', scene.xml)
        self.assertFalse(
            {"tag_9", "tag_2", "tag_10"} & set(scene.spawn_adjustments_mm)
        )
        model = mujoco.MjModel.from_xml_string(scene.xml)
        self.assertGreater(model.ngeom, 0)

        storage_overlay = model.geom(STORAGE_TABLETOP_OVERLAY_GEOM_NAME)
        np.testing.assert_allclose(
            storage_overlay.pos,
            (
                0.0,
                -0.280,
                TABLE_SURFACE_Z_M
                + STORAGE_TABLETOP_OVERLAY_THICKNESS_M / 2.0,
            ),
            atol=1e-9,
        )
        np.testing.assert_allclose(
            storage_overlay.size,
            (0.150, 0.120, STORAGE_TABLETOP_OVERLAY_THICKNESS_M / 2.0),
            atol=1e-9,
        )
        np.testing.assert_allclose(
            storage_overlay.rgba,
            (0.12, 0.12, 0.12, 1.0),
        )
        self.assertEqual(int(storage_overlay.contype[0]), 0)
        self.assertEqual(int(storage_overlay.conaffinity[0]), 0)

        for tag_id, component in scene.components.items():
            mounted = component.mounted_visual
            self.assertIsNotNone(mounted, tag_id)
            assert mounted is not None
            mounted_geom = model.geom(mounted.geom_name)
            self.assertEqual(int(mounted_geom.contype[0]), 0)
            self.assertEqual(int(mounted_geom.conaffinity[0]), 0)
            mesh_id = int(mounted_geom.dataid[0])
            vertex_address = int(model.mesh_vertadr[mesh_id])
            vertex_count = int(model.mesh_vertnum[mesh_id])
            vertices = model.mesh_vert[
                vertex_address : vertex_address + vertex_count
            ]
            rotation = np.empty(9, dtype=float)
            mujoco.mju_quat2Mat(rotation, mounted_geom.quat)
            transformed = vertices @ rotation.reshape(3, 3).T + mounted_geom.pos
            lower = transformed.min(axis=0)
            upper = transformed.max(axis=0)
            self.assertAlmostEqual(
                float((lower[0] + upper[0]) / 2.0), 0.0, places=7, msg=tag_id
            )
            self.assertAlmostEqual(
                float((lower[1] + upper[1]) / 2.0), 0.0, places=7, msg=tag_id
            )
            support_local_z = -component.height_m / 2.0 + 0.121412
            self.assertAlmostEqual(
                float(lower[2]), support_local_z, places=7, msg=tag_id
            )

        mirror = scene.components["tag_20"].mounted_visual
        self.assertIsNotNone(mirror)
        assert mirror is not None
        expected_source_position = (0.034412041, -0.0108109858, 0.034217389)
        source_position = mirror.position_in_component_m(
            scene.components["tag_20"].height_m
        )
        for actual, expected in zip(source_position, expected_source_position):
            self.assertAlmostEqual(actual, expected, places=7)

        mirror_geom = model.geom(mirror.geom_name)
        self.assertEqual(int(mirror_geom.contype[0]), 0)
        self.assertEqual(int(mirror_geom.conaffinity[0]), 0)

        # MuJoCo principal-axis-aligns imported meshes at compile time. Verify the
        # resulting geometry rather than its rewritten geom pose: it is centered,
        # its bottom is on the requested plane, and its face normal points +lab-Y.
        mesh_id = int(mirror_geom.dataid[0])
        vertex_address = int(model.mesh_vertadr[mesh_id])
        vertex_count = int(model.mesh_vertnum[mesh_id])
        vertices = model.mesh_vert[
            vertex_address : vertex_address + vertex_count
        ]
        rotation = np.empty(9, dtype=float)
        mujoco.mju_quat2Mat(rotation, mirror_geom.quat)
        rotation = rotation.reshape(3, 3)
        transformed = vertices @ rotation.T + mirror_geom.pos
        lower = transformed.min(axis=0)
        upper = transformed.max(axis=0)
        self.assertAlmostEqual(float((lower[0] + upper[0]) / 2.0), 0.0, places=7)
        self.assertAlmostEqual(float((lower[1] + upper[1]) / 2.0), 0.0, places=7)
        support_local_z = -scene.components["tag_20"].height_m / 2.0 + 0.121412
        self.assertAlmostEqual(float(lower[2]), support_local_z, places=7)
        face_normal = rotation @ np.array([1.0, 0.0, 0.0])
        np.testing.assert_allclose(face_normal, (0.0, 1.0, 0.0), atol=1e-7)

        plate = next(
            obj
            for obj in scene.static_collision_objects
            if obj.object_id == ROBOT_MOUNTING_PLATE_OBJECT_ID
        )
        self.assertEqual(
            plate.dimensions_m,
            (
                ROBOT_MOUNTING_PLATE_X_M,
                ROBOT_MOUNTING_PLATE_Y_M,
                ROBOT_MOUNTING_PLATE_THICKNESS_M,
            ),
        )
        self.assertAlmostEqual(
            float(model.body("link_base").pos[2]),
            ROBOT_MOUNTING_PLANE_Z_M,
            places=9,
        )


if __name__ == "__main__":
    unittest.main()
