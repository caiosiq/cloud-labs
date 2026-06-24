from __future__ import annotations

import copy
import math
import unittest

import mujoco
import numpy as np

from lab_communicator.mujoco.client import MuJoCoProcessClient
from lab_communicator.mujoco.runtime import (
    CollisionPlanError,
    MuJoCoRobotRuntime,
)
from lab_communicator.mujoco.scene import (
    SceneValidationError,
    build_scene_spec,
)
from lab_model.domain.storage_region import configure_from_layout_document


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
configure_from_layout_document(LAYOUT)


def catalog_row(tag_id: str, component_type: str = "OPTICAL_FILTER"):
    return {
        "tag_id": tag_id,
        "type": component_type,
        "size": {"width": 62, "height": 62},
        "height_mm": 60,
        "capabilities": {
            "statecontrol": {
                "tunables": {"nominal_pose": {"widget": "TablePose"}},
                "measurables": {},
            },
            "telemetry": {},
            "primitives": ["MOVE_COMPONENT"],
        },
    }


def component(x: float, y: float, rotation: float = 0.0):
    return {
        "type": "OPTICAL_FILTER",
        "statecontrol": {
            "tunables": {
                "presence": "breadboard",
                "nominal_pose": {"x": x, "y": y, "rotation": rotation},
                "storage": {"in_storage": False, "slot": None},
                "placement": {"mode": "MANUAL"},
            },
            "measurables": {
                "pose": {"x": x, "y": y, "rotation": rotation},
            },
        },
        "telemetry": {
            "teleop": {"active": False, "ready": False},
            "live_feed": {},
        },
    }


def valid_scene(profile_id: str = "demo_boxes"):
    state = {
        "system_status": "IDLE",
        "components": {
            "tag_pick": component(300, -180),
            "tag_other": component(300, 100),
        },
        "holding": {},
    }
    rows = [
        catalog_row("tag_pick"),
        catalog_row("tag_other", "OPTICAL_MIRROR"),
    ]
    return build_scene_spec(LAYOUT, rows, state, profile_id=profile_id)


class MujocoSceneTests(unittest.TestCase):
    def test_scene_uses_millimeters_to_meters_and_catalog_dimensions(self):
        scene = valid_scene()
        spec = scene.components["tag_pick"]
        self.assertAlmostEqual(spec.x_m, 0.3)
        self.assertAlmostEqual(spec.y_m, -0.18)
        self.assertAlmostEqual(spec.width_m, 0.062)
        self.assertAlmostEqual(spec.depth_m, 0.062)
        self.assertAlmostEqual(spec.height_m, 0.06)
        model = mujoco.MjModel.from_xml_string(scene.xml)
        self.assertEqual(model.nu, 8)
        self.assertGreaterEqual(model.body(spec.body_name).id, 0)

    def test_optical_housing_profile_uses_shared_mesh_and_collision_box(self):
        scene = valid_scene("optical_housings")
        self.assertEqual(scene.profile_id, "optical_housings")
        model = mujoco.MjModel.from_xml_string(scene.xml)
        for spec in scene.components.values():
            self.assertAlmostEqual(spec.width_m, 0.072)
            self.assertAlmostEqual(spec.depth_m, 0.0642883)
            self.assertAlmostEqual(spec.height_m, 0.221412)
            self.assertAlmostEqual(spec.mass_kg, 0.25)
            self.assertAlmostEqual(spec.grasp_height_m, 0.18)
            visual_id = model.geom(f"visual_{spec.body_name}").id
            collision_id = model.geom(f"geom_{spec.body_name}").id
            self.assertEqual(model.geom_type[visual_id], mujoco.mjtGeom.mjGEOM_MESH)
            self.assertEqual(model.geom_type[collision_id], mujoco.mjtGeom.mjGEOM_BOX)
            self.assertEqual(model.geom_contype[visual_id], 0)
            self.assertEqual(model.geom_conaffinity[visual_id], 0)

    def test_scene_rejects_box_whose_footprint_crosses_table_edge(self):
        state = {
            "components": {"tag_pick": component(490, 0)},
        }
        with self.assertRaises(SceneValidationError):
            build_scene_spec(LAYOUT, [catalog_row("tag_pick")], state)


class MujocoPickPlaceTests(unittest.TestCase):
    def test_optical_housing_profile_moves_all_original_components(self):
        state = {
            "system_status": "IDLE",
            "components": {
                "tag_filter": component(138.4, -90.1),
                "tag_block": component(0.0, 259.9, 45.0),
                "tag_mirror": component(300.0, 200.0),
                "tag_crystal": component(234.5, 47.9),
            },
            "holding": {},
        }
        rows = [
            catalog_row("tag_filter", "OPTICAL_FILTER"),
            catalog_row("tag_block", "OPTICAL_BEAM_BLOCK"),
            catalog_row("tag_mirror", "OPTICAL_MIRROR"),
            catalog_row("tag_crystal", "OPTICAL_CRYSTAL"),
        ]
        scene = build_scene_spec(
            LAYOUT,
            rows,
            state,
            profile_id="optical_housings",
        )
        targets = {
            "tag_filter": (163.4, -105.1),
            "tag_block": (25.0, 244.9),
            "tag_mirror": (325.0, 185.0),
            "tag_crystal": (-200.0, 200.0),
        }
        for tag_id, (target_x, target_y) in targets.items():
            runtime = MuJoCoRobotRuntime(
                scene,
                show_viewer=False,
                realtime=False,
            )
            try:
                result = runtime.pick_and_place(
                    tag_id,
                    target_x_mm=target_x,
                    target_y_mm=target_y,
                    target_rotation_deg=0.0,
                )
            finally:
                runtime.close()
            self.assertLessEqual(abs(result.x_mm - target_x), 5)
            self.assertLessEqual(abs(result.y_mm - target_y), 5)

    def test_original_layout_quadrants_are_reachable(self):
        state = {
            "system_status": "IDLE",
            "components": {
                "tag_filter": component(138.4, -90.1),
                "tag_block": component(0.0, 259.9, 45.0),
                "tag_mirror": component(300.0, 200.0),
                "tag_crystal": component(234.5, 47.9),
            },
            "holding": {},
        }
        rows = [
            catalog_row("tag_filter", "OPTICAL_FILTER"),
            catalog_row("tag_block", "OPTICAL_BEAM_BLOCK"),
            catalog_row("tag_mirror", "OPTICAL_MIRROR"),
            catalog_row("tag_crystal", "OPTICAL_CRYSTAL"),
        ]
        scene = build_scene_spec(LAYOUT, rows, state)
        targets = {
            "tag_filter": (163.4, -105.1),
            "tag_block": (25.0, 244.9),
            "tag_mirror": (325.0, 185.0),
            "tag_crystal": (-200.0, 200.0),
        }
        for tag_id, (target_x, target_y) in targets.items():
            runtime = MuJoCoRobotRuntime(
                scene,
                show_viewer=False,
                realtime=False,
            )
            try:
                result = runtime.pick_and_place(
                    tag_id,
                    target_x_mm=target_x,
                    target_y_mm=target_y,
                    target_rotation_deg=0.0,
                )
            finally:
                runtime.close()
            self.assertLessEqual(abs(result.x_mm - target_x), 5)
            self.assertLessEqual(abs(result.y_mm - target_y), 5)

    def test_pick_place_order_and_final_tolerance(self):
        runtime = MuJoCoRobotRuntime(
            valid_scene(),
            show_viewer=False,
            realtime=False,
        )
        try:
            result = runtime.pick_and_place(
                "tag_pick",
                target_x_mm=300,
                target_y_mm=-40,
                target_rotation_deg=20,
            )
        finally:
            runtime.close()

        self.assertLessEqual(abs(result.x_mm - 300), 5)
        self.assertLessEqual(abs(result.y_mm + 40), 5)
        yaw_error = abs((result.rotation_deg - 20 + 180) % 360 - 180)
        self.assertLessEqual(yaw_error, 2)
        trace = list(result.stage_trace)
        self.assertLess(
            trace.index("lower around box"),
            trace.index("close while stationary"),
        )
        self.assertLess(
            trace.index("close while stationary"),
            trace.index("activate assisted weld"),
        )
        self.assertLess(
            trace.index("open and release"),
            trace.index("deactivate assisted weld"),
        )

    def test_assisted_grasp_stays_upright_and_releases_continuously(self):
        runtime = MuJoCoRobotRuntime(
            valid_scene(),
            show_viewer=False,
            realtime=False,
        )
        spec = runtime.scene.components["tag_pick"]
        sampled_quaternions = []
        lift_up_axis_z = None
        original_step = runtime._step
        original_move_stage = runtime._move_stage

        def measured_step():
            original_step()
            sampled_quaternions.append(
                runtime.data.joint(spec.joint_name).qpos[3:7].copy()
            )

        def measured_move_stage(*args, **kwargs):
            nonlocal lift_up_axis_z
            original_move_stage(*args, **kwargs)
            if args[0] == "lift":
                lift_up_axis_z = float(
                    runtime.data.body(spec.body_name).xmat.reshape(3, 3)[2, 2]
                )

        runtime._step = measured_step
        runtime._move_stage = measured_move_stage
        try:
            runtime.pick_and_place(
                "tag_pick",
                target_x_mm=300,
                target_y_mm=-40,
                target_rotation_deg=20,
            )
        finally:
            runtime.close()

        def quaternion_step_deg(first, second):
            dot = float(np.clip(abs(np.dot(first, second)), -1.0, 1.0))
            return math.degrees(2.0 * math.acos(dot))

        largest_step = max(
            quaternion_step_deg(first, second)
            for first, second in zip(
                sampled_quaternions,
                sampled_quaternions[1:],
            )
        )
        self.assertIsNotNone(lift_up_axis_z)
        self.assertGreater(lift_up_axis_z, 0.98)
        self.assertLess(largest_step, 5.0)

    def test_overlapping_destination_is_rejected_before_motion(self):
        runtime = MuJoCoRobotRuntime(
            valid_scene(),
            show_viewer=False,
            realtime=False,
        )
        try:
            with self.assertRaises(CollisionPlanError):
                runtime.pick_and_place(
                    "tag_pick",
                    target_x_mm=300,
                    target_y_mm=100,
                    target_rotation_deg=0,
                )
            self.assertEqual(runtime.stage_trace, [])
        finally:
            runtime.close()


class MujocoProcessTests(unittest.TestCase):
    def test_spawned_process_starts_and_stops_cleanly(self):
        client = MuJoCoProcessClient(
            valid_scene(),
            show_viewer=False,
            realtime=False,
        )
        try:
            client.start(timeout_s=20)
            self.assertTrue(client.status()["running"])
        finally:
            client.stop()
        self.assertFalse(client.running)


if __name__ == "__main__":
    unittest.main()
