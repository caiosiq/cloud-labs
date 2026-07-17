from __future__ import annotations

import copy
import math
import os
import unittest
from unittest.mock import patch

import mujoco
import numpy as np

from lab_communicator.mujoco import runtime as runtime_module
from lab_communicator.mujoco.client import MuJoCoProcessClient
from lab_communicator.mujoco.runtime import (
    CollisionPlanError,
    MoveResult,
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

    def test_optical_housing_profile_adds_lab_frame_visual_and_clearance_shell(self):
        scene = valid_scene("optical_housings")
        frame_ids = {
            obj.object_id: obj
            for obj in scene.static_collision_objects
        }
        self.assertEqual(
            set(frame_ids),
            {
                "lab_frame_left_clearance",
                "lab_frame_right_clearance",
                "lab_frame_front_clearance",
                "lab_frame_back_clearance",
                "lab_frame_top_camera_clearance",
            },
        )
        self.assertAlmostEqual(scene.table_bounds_mm["x_min"], -609.6)
        self.assertAlmostEqual(scene.table_bounds_mm["x_max"], 609.6)
        self.assertAlmostEqual(scene.table_bounds_mm["y_min"], -628.65)
        self.assertAlmostEqual(scene.table_bounds_mm["y_max"], 628.65)

        wall_thickness_m = (0.75 + 3.0) * 0.0254
        top_clearance_m = (6.0 + 3.0) * 0.0254
        self.assertAlmostEqual(
            frame_ids["lab_frame_left_clearance"].dimensions_m[0],
            wall_thickness_m,
        )
        self.assertAlmostEqual(
            frame_ids["lab_frame_top_camera_clearance"].dimensions_m[2],
            top_clearance_m,
        )

        model = mujoco.MjModel.from_xml_string(scene.xml)
        visual_id = model.geom("visual_lab_frame").id
        left_id = model.geom("lab_frame_left_clearance").id
        top_id = model.geom("lab_frame_top_camera_clearance").id
        self.assertEqual(model.geom_type[visual_id], mujoco.mjtGeom.mjGEOM_MESH)
        self.assertEqual(model.geom_contype[visual_id], 0)
        self.assertEqual(model.geom_conaffinity[visual_id], 0)
        self.assertEqual(model.geom_type[left_id], mujoco.mjtGeom.mjGEOM_BOX)
        self.assertEqual(model.geom_type[top_id], mujoco.mjtGeom.mjGEOM_BOX)
        self.assertAlmostEqual(model.geom_size[left_id][0], wall_thickness_m / 2.0)
        self.assertAlmostEqual(model.geom_size[top_id][2], top_clearance_m / 2.0)

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

    def test_moveit_backend_uses_manual_contact_slices(self):
        runtime = MuJoCoRobotRuntime(
            valid_scene(),
            show_viewer=False,
            realtime=False,
        )
        moveit_calls = []
        controller_calls = []

        def fake_moveit_stage(
            name,
            position_m,
            rotation,
            *,
            speed_mm_s,
            omit_tags=None,
            attached_tag=None,
        ):
            del rotation, speed_mm_s
            runtime.stage_trace.append(name)
            moveit_calls.append(
                {
                    "name": name,
                    "z_m": float(position_m[2]),
                    "omit_tags": set(omit_tags or set()),
                    "attached_tag": attached_tag,
                }
            )

        def fake_carry_stage(
            name,
            *,
            source_xy,
            target_xy,
            transfer_z,
            rotation,
            speed_mm_s,
            omit_tags,
            attached_tag,
            source_rotation=None,
            place_position_m=None,
            place_speed_mm_s=80.0,
        ):
            del source_xy, source_rotation, place_position_m, place_speed_mm_s
            fake_moveit_stage(
                name,
                np.array((*target_xy, transfer_z)),
                rotation,
                speed_mm_s=speed_mm_s,
                omit_tags=omit_tags,
                attached_tag=attached_tag,
            )

        def fake_controller_stage(name, arm, position_m, rotation, *, speed_mm_s):
            del arm, rotation, speed_mm_s
            runtime.stage_trace.append(name)
            controller_calls.append({"name": name, "z_m": float(position_m[2])})

        def fake_result(tag_id, **kwargs):
            del kwargs
            return MoveResult(
                tag_id=tag_id,
                x_mm=300.0,
                y_mm=-40.0,
                rotation_deg=20.0,
                stage_trace=tuple(runtime.stage_trace),
            )

        runtime._moveit_move_stage = fake_moveit_stage
        runtime._moveit_carry_stage = fake_carry_stage
        runtime._move_stage = fake_controller_stage
        runtime._validate_grasp_envelope = lambda spec: None
        runtime._settle = lambda duration_s: None
        runtime._placement_result = fake_result

        try:
            result = runtime._pick_and_place_moveit(
                "tag_pick",
                target_x_mm=300,
                target_y_mm=-40,
                target_rotation_deg=20,
            )
        finally:
            runtime.close()

        self.assertEqual(
            [call["name"] for call in moveit_calls],
            ["moveit move above source", "moveit translate"],
        )
        self.assertEqual(
            [call["name"] for call in controller_calls],
            [
                "descend open",
                "lower around box",
                "pickup clearance lift",
                "contact descend destination",
                "contact retreat",
            ],
        )
        self.assertEqual(moveit_calls[0]["omit_tags"], {"tag_pick"})
        self.assertIsNone(moveit_calls[0]["attached_tag"])
        self.assertEqual(moveit_calls[1]["omit_tags"], {"tag_pick"})
        self.assertEqual(moveit_calls[1]["attached_tag"], "tag_pick")
        self.assertAlmostEqual(moveit_calls[0]["z_m"], 0.28, places=6)
        self.assertAlmostEqual(moveit_calls[1]["z_m"], 0.28, places=6)
        self.assertIn("activate assisted weld", result.stage_trace)
        self.assertIn("deactivate assisted weld", result.stage_trace)
        self.assertNotIn("moveit retreat", result.stage_trace)

    def test_moveit_backend_runs_prepick_feasibility_before_pickup(self):
        runtime = MuJoCoRobotRuntime(
            valid_scene(profile_id="optical_housings"),
            show_viewer=False,
            realtime=False,
        )
        runtime.moveit = object()
        order = []

        def make_stage(name):
            return runtime_module.MoveItStagePlan(
                name=name,
                stage_request_id=name,
                position_m=np.zeros(3, dtype=float),
                rotation=np.eye(3),
                points=[],
                joint_names=runtime_module.MOVEIT_JOINT_NAMES,
                fallback_duration_s=0.1,
                allowed_tag=None,
                final_joints=runtime.current_joint_target.copy(),
            )

        def fake_prepick(*args, **kwargs):
            del args, kwargs
            order.append("prepick")
            return runtime_module.MoveItPrePickFeasibilityPlan(
                seed_index=1,
                seed_total=4,
                above_source=make_stage("moveit move above source"),
                carry=runtime_module.MoveItCarryPlan(
                    stage_name="moveit translate",
                    route_index=0,
                    stages=(make_stage("moveit translate"),),
                ),
            )

        def fake_execute_stage(planned):
            order.append(planned.name)
            runtime.stage_trace.append(planned.name)

        def fake_execute_carry(carry_plan):
            order.append(carry_plan.stage_name)
            for planned in carry_plan.stages:
                runtime.stage_trace.append(planned.name)

        def fake_controller_stage(name, arm, position_m, rotation, *, speed_mm_s):
            del arm, position_m, rotation, speed_mm_s
            order.append(name)
            runtime.stage_trace.append(name)

        def fake_gripper(position, *, speed):
            del speed
            order.append(f"gripper {position:.0f}")
            runtime._gripper_position = float(position)

        def fake_result(tag_id, **kwargs):
            del kwargs
            return MoveResult(
                tag_id=tag_id,
                x_mm=300.0,
                y_mm=-40.0,
                rotation_deg=20.0,
                stage_trace=tuple(runtime.stage_trace),
            )

        runtime._plan_moveit_pre_pick_feasibility = fake_prepick
        runtime._execute_moveit_stage_plan = fake_execute_stage
        runtime._execute_moveit_carry_plan = fake_execute_carry
        runtime._move_stage = fake_controller_stage
        runtime.set_gripper_position = fake_gripper
        runtime._validate_grasp_envelope = lambda spec: None
        runtime._settle = lambda duration_s: None
        runtime._placement_result = fake_result

        try:
            runtime._pick_and_place_moveit(
                "tag_pick",
                target_x_mm=300,
                target_y_mm=-40,
                target_rotation_deg=20,
            )
        finally:
            runtime.close()

        self.assertLess(order.index("prepick"), order.index("gripper 0"))
        self.assertLess(order.index("gripper 0"), order.index("moveit move above source"))
        self.assertLess(order.index("pickup clearance lift"), order.index("moveit translate"))

    def test_moveit_backend_uses_cached_manual_plans_from_prepick(self):
        runtime = MuJoCoRobotRuntime(
            valid_scene(profile_id="optical_housings"),
            show_viewer=False,
            realtime=False,
        )
        runtime.moveit = object()
        cached_calls = []
        fallback_calls = []

        def make_stage(name):
            return runtime_module.MoveItStagePlan(
                name=name,
                stage_request_id=name,
                position_m=np.zeros(3, dtype=float),
                rotation=np.eye(3),
                points=[],
                joint_names=runtime_module.MOVEIT_JOINT_NAMES,
                fallback_duration_s=0.1,
                allowed_tag=None,
                final_joints=runtime.current_joint_target.copy(),
            )

        def make_manual(name):
            return runtime_module.ManualCartesianStagePlan(
                name=name,
                position_m=np.zeros(3, dtype=float),
                rotation=np.eye(3),
                joint_waypoints=(
                    runtime.current_joint_target.copy(),
                    runtime.current_joint_target.copy(),
                ),
                durations_s=(0.01,),
                speed_mm_s=100.0,
                allowed_tag="tag_pick" if "open" not in name and "retreat" not in name else None,
                gripper_position=runtime_module.GRIPPER_OPEN,
            )

        pickup_names = (
            "descend open",
            "lower around box",
            "pickup clearance lift",
        )
        place_names = (
            "contact descend destination",
            "contact retreat",
        )

        def fake_prepick(*args, **kwargs):
            del args, kwargs
            return runtime_module.MoveItPrePickFeasibilityPlan(
                seed_index=1,
                seed_total=4,
                above_source=make_stage("moveit move above source"),
                manual_pickup_stages=tuple(make_manual(name) for name in pickup_names),
                carry=runtime_module.MoveItCarryPlan(
                    stage_name="moveit translate",
                    route_index=0,
                    stages=(make_stage("moveit translate"),),
                    post_carry_manual_stages=tuple(
                        make_manual(name) for name in place_names
                    ),
                ),
            )

        def fake_execute_stage(planned):
            runtime.stage_trace.append(planned.name)

        def fake_execute_carry(carry_plan):
            for planned in carry_plan.stages:
                runtime.stage_trace.append(planned.name)

        def fake_execute_cached(planned, arm=None):
            del arm
            cached_calls.append(planned.name)
            runtime.stage_trace.append(planned.name)

        def fake_controller_stage(name, arm, position_m, rotation, *, speed_mm_s):
            del arm, position_m, rotation, speed_mm_s
            fallback_calls.append(name)
            runtime.stage_trace.append(name)

        def fake_result(tag_id, **kwargs):
            del kwargs
            return MoveResult(
                tag_id=tag_id,
                x_mm=300.0,
                y_mm=-40.0,
                rotation_deg=20.0,
                stage_trace=tuple(runtime.stage_trace),
            )

        runtime._plan_moveit_pre_pick_feasibility = fake_prepick
        runtime._execute_moveit_stage_plan = fake_execute_stage
        runtime._execute_moveit_carry_plan = fake_execute_carry
        runtime._execute_cartesian_stage_plan = fake_execute_cached
        runtime._move_stage = fake_controller_stage
        runtime._validate_grasp_envelope = lambda spec: None
        runtime._settle = lambda duration_s: None
        runtime._placement_result = fake_result

        try:
            runtime._pick_and_place_moveit(
                "tag_pick",
                target_x_mm=300,
                target_y_mm=-40,
                target_rotation_deg=20,
            )
        finally:
            runtime.close()

        self.assertEqual(cached_calls, [*pickup_names, *place_names])
        self.assertEqual(fallback_calls, [])

    def test_moveit_backend_uses_collision_aware_rest_seed(self):
        class FakeMoveItClient:
            def health(self):
                return {"ok": True}

        baseline = MuJoCoRobotRuntime(
            valid_scene(profile_id="optical_housings"),
            show_viewer=False,
            realtime=False,
        )
        try:
            keyframe_home = baseline.home.copy()
            keyframe_tcp_rotation = baseline.home_tcp_rotation.copy()
        finally:
            baseline.close()

        with patch.object(
            runtime_module,
            "MoveItPlannerClient",
            return_value=FakeMoveItClient(),
        ):
            moveit_runtime = MuJoCoRobotRuntime(
                valid_scene(profile_id="optical_housings"),
                show_viewer=False,
                realtime=False,
                planner_backend=runtime_module.MUJOCO_PLANNER_MOVEIT,
            )
        try:
            expected = np.asarray(
                runtime_module.MOVEIT_COLLISION_AWARE_REST_JOINTS,
                dtype=float,
            )
            self.assertTrue(np.allclose(moveit_runtime.home, keyframe_home))
            self.assertTrue(
                np.allclose(moveit_runtime.home_tcp_rotation, keyframe_tcp_rotation)
            )
            self.assertTrue(
                np.allclose(moveit_runtime.current_joint_target, expected)
            )
            self.assertTrue(np.allclose(moveit_runtime.data.ctrl[:7], expected))
            self.assertGreater(float(np.linalg.norm(expected - keyframe_home)), 0.5)
        finally:
            moveit_runtime.close()

    def test_moveit_return_home_uses_observation_rest_pose(self):
        class FakeMoveItClient:
            def health(self):
                return {"ok": True}

        with patch.object(
            runtime_module,
            "MoveItPlannerClient",
            return_value=FakeMoveItClient(),
        ):
            runtime = MuJoCoRobotRuntime(
                valid_scene(profile_id="optical_housings"),
                show_viewer=False,
                realtime=False,
                planner_backend=runtime_module.MUJOCO_PLANNER_MOVEIT,
            )

        expected_home = np.asarray(
            runtime_module.MOVEIT_COLLISION_AWARE_REST_JOINTS,
            dtype=float,
        )
        start_joints = expected_home + np.asarray(
            (0.12, 0.05, -0.08, 0.03, 0.09, -0.04, 0.11),
            dtype=float,
        )
        preflight_calls = []
        drive_calls = []

        def fake_preflight(start, end, *, allowed_tag):
            preflight_calls.append((start.copy(), end.copy(), allowed_tag))

        def fake_drive(start, end, duration_s, *, minimum_s=0.001):
            drive_calls.append((start.copy(), end.copy(), duration_s, minimum_s))
            runtime.data.qpos[:7] = end
            runtime.data.ctrl[:7] = end

        runtime.current_joint_target = start_joints.copy()
        runtime.data.qpos[:7] = start_joints
        runtime.data.ctrl[:7] = start_joints
        runtime.allowed_collision_tag = "tag_pick"
        runtime._preflight_joint_path = fake_preflight
        runtime._drive_joint_segment = fake_drive
        runtime._settle = lambda duration_s: None

        try:
            runtime._return_to_observation_home()
        finally:
            runtime.close()

        self.assertIn("return home", runtime.stage_trace)
        self.assertTrue(np.allclose(runtime.current_joint_target, expected_home))
        self.assertTrue(np.allclose(runtime.data.ctrl[:7], expected_home))
        self.assertEqual(len(preflight_calls), 1)
        self.assertTrue(np.allclose(preflight_calls[0][0], start_joints))
        self.assertTrue(np.allclose(preflight_calls[0][1], expected_home))
        self.assertIsNone(preflight_calls[0][2])
        self.assertEqual(len(drive_calls), 1)
        self.assertGreaterEqual(
            drive_calls[0][2],
            runtime_module.RETURN_HOME_MIN_DURATION_S,
        )
        self.assertEqual(
            drive_calls[0][3],
            runtime_module.RETURN_HOME_MIN_PLAYBACK_DURATION_S,
        )

    def test_moveit_return_home_uses_moveit_when_joint_sweep_is_blocked(self):
        class FakeMoveItClient:
            def health(self):
                return {"ok": True}

        with patch.object(
            runtime_module,
            "MoveItPlannerClient",
            return_value=FakeMoveItClient(),
        ):
            runtime = MuJoCoRobotRuntime(
                valid_scene(profile_id="optical_housings"),
                show_viewer=False,
                realtime=False,
                planner_backend=runtime_module.MUJOCO_PLANNER_MOVEIT,
            )

        expected_home = np.asarray(
            runtime_module.MOVEIT_COLLISION_AWARE_REST_JOINTS,
            dtype=float,
        )
        start_joints = expected_home + np.asarray(
            (0.12, 0.05, -0.08, 0.03, 0.09, -0.04, 0.11),
            dtype=float,
        )
        planned_calls = []

        def blocked_preflight(start, end, *, allowed_tag):
            del start, end, allowed_tag
            raise CollisionPlanError("joint sweep blocked")

        def fake_plan(
            name,
            position_m,
            rotation,
            *,
            speed_mm_s,
            omit_tags=None,
            attached_tag=None,
            orientation_tolerance_rad=0.05,
            **kwargs,
        ):
            del rotation, speed_mm_s, kwargs
            planned_calls.append(
                {
                    "name": name,
                    "position_m": np.asarray(position_m, dtype=float).copy(),
                    "omit_tags": set(omit_tags or set()),
                    "attached_tag": attached_tag,
                    "orientation_tolerance_rad": orientation_tolerance_rad,
                }
            )
            return runtime_module.MoveItStagePlan(
                name=name,
                stage_request_id=name,
                position_m=np.asarray(position_m, dtype=float).copy(),
                rotation=np.eye(3),
                points=[],
                joint_names=runtime_module.MOVEIT_JOINT_NAMES,
                fallback_duration_s=0.1,
                allowed_tag=None,
                final_joints=expected_home.copy(),
            )

        def fake_execute(planned, *, minimum_s=0.02):
            self.assertEqual(
                minimum_s,
                runtime_module.RETURN_HOME_MIN_PLAYBACK_DURATION_S,
            )
            runtime.stage_trace.append(planned.name)
            runtime.current_joint_target = planned.final_joints.copy()
            runtime.data.qpos[:7] = planned.final_joints
            runtime.data.ctrl[:7] = planned.final_joints

        runtime.current_joint_target = start_joints.copy()
        runtime.data.qpos[:7] = start_joints
        runtime.data.ctrl[:7] = start_joints
        runtime._preflight_joint_path = blocked_preflight
        runtime._tcp_pose_for_joint_target = lambda joints: (
            np.array((0.1, 0.2, 0.3), dtype=float),
            np.eye(3),
        )
        runtime._plan_moveit_stage = fake_plan
        runtime._execute_moveit_stage_plan = fake_execute
        runtime._settle = lambda duration_s: None

        try:
            runtime._return_to_observation_home()
        finally:
            runtime.close()

        self.assertEqual([call["name"] for call in planned_calls], ["moveit return home"])
        self.assertEqual(planned_calls[0]["omit_tags"], set())
        self.assertIsNone(planned_calls[0]["attached_tag"])
        self.assertAlmostEqual(planned_calls[0]["orientation_tolerance_rad"], 0.08)
        self.assertIn("return home", runtime.stage_trace)
        self.assertIn("moveit return home", runtime.stage_trace)
        self.assertTrue(np.allclose(runtime.current_joint_target, expected_home))

    def test_moveit_stage_passes_runtime_speed_scaling_to_sidecar(self):
        runtime = MuJoCoRobotRuntime(
            valid_scene(profile_id="optical_housings"),
            show_viewer=False,
            realtime=False,
        )
        captured = {}

        class FakeMoveItClient:
            def plan_pose(self, **kwargs):
                captured.update(kwargs)
                return {
                    "success": True,
                    "joint_trajectory": {
                        "joint_names": runtime_module.MOVEIT_JOINT_NAMES,
                        "points": [
                            {
                                "positions": runtime.current_joint_target.tolist(),
                                "time_from_start_s": 0.1,
                            }
                        ],
                    },
                }

        runtime.moveit = FakeMoveItClient()
        try:
            with patch.dict(
                os.environ,
                {
                    runtime_module.MOVEIT_MAX_VELOCITY_SCALE_ENV_VAR: "0.66",
                    runtime_module.MOVEIT_MAX_ACCELERATION_SCALE_ENV_VAR: "0.77",
                    runtime_module.MOVEIT_ALLOWED_PLANNING_TIME_ENV_VAR: "2.5",
                    runtime_module.MOVEIT_PLANNING_ATTEMPTS_ENV_VAR: "3",
                },
            ):
                runtime._plan_moveit_stage(
                    "moveit speed scaling test",
                    runtime.data.site("link_tcp").xpos.copy(),
                    runtime.data.site("link_tcp").xmat.reshape(3, 3).copy(),
                    speed_mm_s=170.0,
                )
        finally:
            runtime.close()

        self.assertEqual(captured["max_velocity_scaling_factor"], 0.66)
        self.assertEqual(captured["max_acceleration_scaling_factor"], 0.77)
        self.assertEqual(captured["allowed_planning_time_s"], 2.5)
        self.assertEqual(captured["planning_attempts"], 3)

    def test_moveit_stage_passes_carry_path_constraints_to_sidecar(self):
        runtime = MuJoCoRobotRuntime(
            valid_scene(profile_id="optical_housings"),
            show_viewer=False,
            realtime=False,
        )
        captured = {}

        class FakeMoveItClient:
            def plan_pose(self, **kwargs):
                captured.update(kwargs)
                return {
                    "success": True,
                    "joint_trajectory": {
                        "joint_names": runtime_module.MOVEIT_JOINT_NAMES,
                        "points": [
                            {
                                "positions": runtime.current_joint_target.tolist(),
                                "time_from_start_s": 0.1,
                            }
                        ],
                    },
                }

        runtime.moveit = FakeMoveItClient()
        try:
            rotation = runtime.data.site("link_tcp").xmat.reshape(3, 3).copy()
            path_constraint = runtime._moveit_carry_upright_path_constraint(rotation)
            runtime._plan_moveit_stage(
                "moveit constrained carry test",
                runtime.data.site("link_tcp").xpos.copy(),
                rotation,
                speed_mm_s=170.0,
                attached_tag="tag_pick",
                joint7_continuity_tolerance_rad=1.25,
                path_orientation_constraint=path_constraint,
                path_joint7_continuity=True,
            )
        finally:
            runtime.close()

        self.assertEqual(captured["path_orientation_constraint"], path_constraint)
        self.assertEqual(
            captured["path_joint_constraints"][0]["joint_name"],
            "joint7",
        )
        self.assertAlmostEqual(
            captured["path_joint_constraints"][0]["position"],
            captured["start_joints"][6],
        )
        self.assertEqual(captured["joint7_continuity_tolerance_rad"], 1.25)

    def test_moveit_carry_uses_direct_goal_with_upright_constraint(self):
        runtime = MuJoCoRobotRuntime(
            valid_scene(profile_id="optical_housings"),
            show_viewer=False,
            realtime=False,
        )
        planned = []
        source_rotation = runtime._target_rotation(0.0)

        def fake_plan(
            name,
            position_m,
            rotation,
            *,
            speed_mm_s,
            omit_tags=None,
            attached_tag=None,
            orientation_tolerance_rad=0.05,
            joint7_continuity_tolerance_rad=0.0,
            path_orientation_constraint=None,
            path_joint7_continuity=False,
        ):
            del rotation, speed_mm_s, omit_tags, attached_tag, orientation_tolerance_rad
            planned.append(
                {
                    "name": name,
                    "position_m": np.asarray(position_m, dtype=float).copy(),
                    "joint7_tolerance": joint7_continuity_tolerance_rad,
                    "path_orientation_constraint": path_orientation_constraint,
                    "path_joint7": path_joint7_continuity,
                }
            )
            return runtime_module.MoveItStagePlan(
                name=name,
                stage_request_id=name,
                position_m=np.asarray(position_m, dtype=float).copy(),
                rotation=np.eye(3),
                points=[],
                joint_names=runtime_module.MOVEIT_JOINT_NAMES,
                fallback_duration_s=0.1,
                allowed_tag="tag_pick",
                final_joints=np.zeros(7, dtype=float),
            )

        runtime._plan_moveit_stage = fake_plan
        runtime._preview_moveit_stage_plan = lambda planned_stage, attached_pose: None

        try:
            plan = runtime._plan_moveit_carry_route(
                "moveit translate",
                source_xy=np.array((0.1, -0.2), dtype=float),
                target_xy=np.array((-0.3, 0.4), dtype=float),
                transfer_z=0.41,
                rotation=source_rotation,
                speed_mm_s=170.0,
                omit_tags={"tag_pick"},
                attached_tag="tag_pick",
                source_rotation=source_rotation,
            )
        finally:
            runtime.close()

        self.assertEqual(plan.route_index, 0)
        self.assertEqual([item["name"] for item in planned], ["moveit translate"])
        self.assertTrue(
            np.allclose(planned[0]["position_m"], np.array((-0.3, 0.4, 0.41)))
        )
        self.assertIsNotNone(planned[0]["path_orientation_constraint"])
        self.assertGreater(planned[0]["joint7_tolerance"], 0.0)
        self.assertTrue(planned[0]["path_joint7"])

    def test_moveit_carry_splits_translation_from_requested_yaw(self):
        runtime = MuJoCoRobotRuntime(
            valid_scene(profile_id="optical_housings"),
            show_viewer=False,
            realtime=False,
        )
        planned = []
        source_rotation = runtime._target_rotation(0.0)
        target_rotation = runtime._target_rotation(60.0)

        def fake_plan(
            name,
            position_m,
            rotation,
            *,
            speed_mm_s,
            omit_tags=None,
            attached_tag=None,
            orientation_tolerance_rad=0.05,
            joint7_continuity_tolerance_rad=0.0,
            path_orientation_constraint=None,
            path_joint7_continuity=False,
        ):
            del (
                position_m,
                speed_mm_s,
                omit_tags,
                attached_tag,
                orientation_tolerance_rad,
            )
            planned.append(
                {
                    "name": name,
                    "yaw": runtime._target_yaw_from_rotation(rotation),
                    "path_orientation_constraint": path_orientation_constraint,
                    "joint7_tolerance": joint7_continuity_tolerance_rad,
                    "path_joint7": path_joint7_continuity,
                }
            )
            return runtime_module.MoveItStagePlan(
                name=name,
                stage_request_id=name,
                position_m=np.zeros(3, dtype=float),
                rotation=np.asarray(rotation, dtype=float).copy(),
                points=[],
                joint_names=runtime_module.MOVEIT_JOINT_NAMES,
                fallback_duration_s=0.1,
                allowed_tag="tag_pick",
                final_joints=np.zeros(7, dtype=float),
            )

        runtime._plan_moveit_stage = fake_plan
        runtime._preview_moveit_stage_plan = lambda planned_stage, attached_pose: None

        try:
            with patch.dict(
                os.environ,
                {
                    runtime_module.MOVEIT_CARRY_YAW_STEP_DEG_ENV_VAR: "30",
                },
            ):
                plan = runtime._plan_moveit_carry_route(
                    "moveit translate",
                    source_xy=np.array((0.0, 0.0), dtype=float),
                    target_xy=np.array((0.1, 0.0), dtype=float),
                    transfer_z=0.41,
                    rotation=target_rotation,
                    speed_mm_s=170.0,
                    omit_tags={"tag_pick"},
                    attached_tag="tag_pick",
                    source_rotation=source_rotation,
                )
        finally:
            runtime.close()

        self.assertEqual(plan.route_index, 0)
        self.assertEqual([item["name"] for item in planned], [
            "moveit translate",
            "moveit translate yaw 1/2",
            "moveit translate yaw 2/2",
        ])
        self.assertAlmostEqual(planned[0]["yaw"], 0.0)
        self.assertAlmostEqual(planned[-1]["yaw"], 60.0)
        self.assertIsNotNone(planned[0]["path_orientation_constraint"])
        for item in planned[1:]:
            self.assertIsNone(item["path_orientation_constraint"])
        for item in planned:
            self.assertGreater(item["joint7_tolerance"], 0.0)
            self.assertTrue(item["path_joint7"])

    def test_moveit_rotation_only_carry_does_not_try_translation_route(self):
        runtime = MuJoCoRobotRuntime(
            valid_scene(profile_id="optical_housings"),
            show_viewer=False,
            realtime=False,
        )
        planned = []
        source_rotation = runtime._target_rotation(0.0)
        target_rotation = runtime._target_rotation(90.0)

        def fake_plan(
            name,
            position_m,
            rotation,
            *,
            speed_mm_s,
            omit_tags=None,
            attached_tag=None,
            orientation_tolerance_rad=0.05,
            joint7_continuity_tolerance_rad=0.0,
            path_orientation_constraint=None,
            path_joint7_continuity=False,
        ):
            del (
                position_m,
                speed_mm_s,
                omit_tags,
                attached_tag,
                orientation_tolerance_rad,
                joint7_continuity_tolerance_rad,
                path_joint7_continuity,
            )
            planned.append(
                {
                    "name": name,
                    "yaw": runtime._target_yaw_from_rotation(rotation),
                    "path_orientation_constraint": path_orientation_constraint,
                }
            )
            return runtime_module.MoveItStagePlan(
                name=name,
                stage_request_id=name,
                position_m=np.zeros(3, dtype=float),
                rotation=np.asarray(rotation, dtype=float).copy(),
                points=[],
                joint_names=runtime_module.MOVEIT_JOINT_NAMES,
                fallback_duration_s=0.1,
                allowed_tag="tag_pick",
                final_joints=np.zeros(7, dtype=float),
            )

        runtime._plan_moveit_stage = fake_plan
        runtime._preview_moveit_stage_plan = lambda planned_stage, attached_pose: None

        try:
            with patch.dict(
                os.environ,
                {runtime_module.MOVEIT_CARRY_YAW_STEP_DEG_ENV_VAR: "30"},
            ):
                plan = runtime._plan_moveit_carry_route(
                    "moveit translate",
                    source_xy=np.array((0.234, 0.048), dtype=float),
                    target_xy=np.array((0.234, 0.048), dtype=float),
                    transfer_z=0.41,
                    rotation=target_rotation,
                    speed_mm_s=170.0,
                    omit_tags={"tag_pick"},
                    attached_tag="tag_pick",
                    source_rotation=source_rotation,
                )
        finally:
            runtime.close()

        self.assertEqual(plan.route_index, 0)
        self.assertEqual([item["name"] for item in planned], [
            "moveit translate yaw 1/3",
            "moveit translate yaw 2/3",
            "moveit translate yaw 3/3",
        ])
        self.assertTrue(all("route" not in item["name"] for item in planned))
        self.assertTrue(all(item["path_orientation_constraint"] is None for item in planned))
        self.assertAlmostEqual(planned[-1]["yaw"], 90.0)

    def test_moveit_trajectory_time_scale_reads_runtime_env(self):
        runtime = MuJoCoRobotRuntime(
            valid_scene(profile_id="optical_housings"),
            show_viewer=False,
            realtime=False,
        )
        try:
            with patch.dict(
                os.environ,
                {runtime_module.MOVEIT_TRAJECTORY_TIME_SCALE_ENV_VAR: "0.5"},
            ):
                self.assertEqual(runtime._moveit_trajectory_time_scale(), 0.5)
        finally:
            runtime.close()

    def test_trajectory_time_scale_also_scales_manual_motion_durations(self):
        runtime = MuJoCoRobotRuntime(
            valid_scene(profile_id="optical_housings"),
            show_viewer=False,
            realtime=False,
        )
        try:
            with patch.dict(
                os.environ,
                {runtime_module.MOVEIT_TRAJECTORY_TIME_SCALE_ENV_VAR: "0.5"},
            ):
                self.assertAlmostEqual(
                    runtime._scaled_manual_duration_s(0.8, minimum_s=0.01),
                    0.4,
                )
            with patch.dict(
                os.environ,
                {runtime_module.MOVEIT_TRAJECTORY_TIME_SCALE_ENV_VAR: "2.0"},
            ):
                self.assertAlmostEqual(
                    runtime._scaled_manual_duration_s(0.8, minimum_s=0.01),
                    1.6,
                )
            with patch.dict(
                os.environ,
                {runtime_module.MOVEIT_TRAJECTORY_TIME_SCALE_ENV_VAR: "0.25"},
            ):
                self.assertAlmostEqual(
                    runtime._scaled_manual_duration_s(0.02, minimum_s=0.01),
                    0.01,
                )
        finally:
            runtime.close()

    def test_moveit_stage_clamps_start_joints_to_moveit_urdf_limits(self):
        runtime = MuJoCoRobotRuntime(
            valid_scene(profile_id="optical_housings"),
            show_viewer=False,
            realtime=False,
        )
        captured = {}
        unsafe = runtime.current_joint_target.copy()
        unsafe[6] = math.pi * 0.999
        runtime.planner_backend = runtime_module.MUJOCO_PLANNER_MOVEIT
        runtime.current_joint_target = unsafe.copy()
        runtime.data.qpos[:7] = unsafe
        runtime.data.ctrl[:7] = unsafe
        mujoco.mj_forward(runtime.model, runtime.data)

        class FakeMoveItClient:
            def plan_pose(self, **kwargs):
                captured.update(kwargs)
                return {
                    "success": True,
                    "joint_trajectory": {
                        "joint_names": runtime_module.MOVEIT_JOINT_NAMES,
                        "points": [
                            {
                                "positions": kwargs["start_joints"],
                                "time_from_start_s": 0.1,
                            }
                        ],
                    },
                }

        runtime.moveit = FakeMoveItClient()
        try:
            runtime._plan_moveit_stage(
                "moveit limit clamp test",
                runtime.data.site("link_tcp").xpos.copy(),
                runtime.data.site("link_tcp").xmat.reshape(3, 3).copy(),
                speed_mm_s=170.0,
            )
        finally:
            runtime.close()

        expected_upper = (
            runtime_module.MOVEIT_JOINT_LIMITS_RAD[6][1]
            - runtime_module.MOVEIT_JOINT_LIMIT_MARGIN_RAD
        )
        self.assertAlmostEqual(captured["start_joints"][6], expected_upper)
        self.assertAlmostEqual(runtime.current_joint_target[6], expected_upper)

    def test_moveit_attached_component_does_not_touch_arm_wrist(self):
        runtime = MuJoCoRobotRuntime(
            valid_scene(),
            show_viewer=False,
            realtime=False,
        )
        try:
            attached = runtime._moveit_attached_object("tag_pick")
        finally:
            runtime.close()

        self.assertEqual(attached["link_name"], "link_tcp")
        self.assertIn("left_finger", attached["touch_links"])
        self.assertIn("right_finger", attached["touch_links"])
        self.assertNotIn("link7", attached["touch_links"])

    def test_moveit_world_objects_include_lab_frame_clearance_shell(self):
        scene = valid_scene(profile_id="optical_housings")
        with patch.dict(
            os.environ,
            {runtime_module.MOVEIT_INCLUDE_TABLE_ENV_VAR: "1"},
        ):
            runtime = MuJoCoRobotRuntime(
                scene,
                show_viewer=False,
                realtime=False,
            )
            try:
                world_objects = runtime._moveit_world_objects()
                known_ids = runtime._moveit_known_collision_object_ids()
            finally:
                runtime.close()

        objects_by_id = {obj["id"]: obj for obj in world_objects}
        self.assertIn("cloudlab_tabletop", objects_by_id)
        self.assertAlmostEqual(
            objects_by_id["cloudlab_tabletop"]["dimensions"][0],
            1.2192,
        )
        self.assertAlmostEqual(
            objects_by_id["cloudlab_tabletop"]["dimensions"][1],
            1.2573,
        )
        self.assertIn("lab_frame_left_clearance", objects_by_id)
        self.assertIn("lab_frame_top_camera_clearance", objects_by_id)
        self.assertEqual(
            objects_by_id["lab_frame_left_clearance"]["type"],
            "box",
        )
        self.assertAlmostEqual(
            objects_by_id["lab_frame_left_clearance"]["dimensions"][0],
            (0.75 + 3.0) * 0.0254,
        )
        self.assertAlmostEqual(
            objects_by_id["lab_frame_top_camera_clearance"]["dimensions"][2],
            (6.0 + 3.0) * 0.0254,
        )
        self.assertIn("lab_frame_left_clearance", known_ids)
        self.assertIn("lab_frame_top_camera_clearance", known_ids)

    def test_preflight_rejects_component_intruding_into_lab_frame_shell(self):
        runtime = MuJoCoRobotRuntime(
            valid_scene(profile_id="optical_housings"),
            show_viewer=False,
            realtime=False,
        )
        try:
            spec = runtime.scene.components["tag_pick"]
            joint_id = runtime.model.joint(spec.joint_name).id
            qpos_addr = int(runtime.model.jnt_qposadr[joint_id])
            runtime.data.qpos[qpos_addr : qpos_addr + 3] = (
                0.56,
                0.0,
                spec.center_z_m,
            )
            runtime.data.qpos[qpos_addr + 3 : qpos_addr + 7] = (1.0, 0.0, 0.0, 0.0)
            mujoco.mj_forward(runtime.model, runtime.data)

            with self.assertRaisesRegex(CollisionPlanError, "lab_frame_right"):
                runtime._preflight_joint_path(
                    runtime.current_joint_target,
                    runtime.current_joint_target,
                    allowed_tag=None,
                )
        finally:
            runtime.close()

    def test_moveit_tcp_rotation_accounts_for_frame_offset(self):
        runtime = MuJoCoRobotRuntime(
            valid_scene(profile_id="optical_housings"),
            show_viewer=False,
            realtime=False,
        )
        try:
            desired_mujoco_rotation = runtime._target_rotation(0.0)
            moveit_rotation = runtime._moveit_pose_link_rotation(
                desired_mujoco_rotation
            )

            self.assertLess(
                runtime_module._rotation_distance_deg(
                    desired_mujoco_rotation,
                    moveit_rotation
                    @ runtime_module.MOVEIT_TCP_TO_MUJOCO_TCP_ROTATION,
                ),
                1.0e-4,
            )
            self.assertGreater(
                runtime_module._rotation_distance_deg(
                    desired_mujoco_rotation,
                    moveit_rotation,
                ),
                170.0,
            )
        finally:
            runtime.close()

    def test_moveit_start_joint_normalization_wraps_equivalent_wrist_angle(self):
        runtime = MuJoCoRobotRuntime(
            valid_scene(profile_id="optical_housings"),
            show_viewer=False,
            realtime=False,
        )
        try:
            wrapped = np.array(
                [
                    1.9690113146044899,
                    -0.5988520843883883,
                    -0.21095587036344576,
                    1.321539598591923,
                    -0.12505288511768514,
                    1.9077379974988093,
                    -5.315996833743899,
                ],
                dtype=float,
            )
            runtime.current_joint_target = wrapped.copy()
            runtime.data.qpos[:7] = wrapped
            runtime.data.ctrl[:7] = wrapped
            mujoco.mj_forward(runtime.model, runtime.data)
            before_tcp = runtime.data.site("link_tcp").xpos.copy()
            before_rotation = runtime.data.site("link_tcp").xmat.copy()

            runtime._normalize_moveit_start_joints()

            self.assertGreaterEqual(runtime.current_joint_target[6], -math.pi)
            self.assertLessEqual(runtime.current_joint_target[6], math.pi)
            self.assertTrue(np.allclose(runtime.data.qpos[:7], runtime.data.ctrl[:7]))
            self.assertTrue(
                np.allclose(runtime.data.site("link_tcp").xpos, before_tcp)
            )
            self.assertTrue(
                np.allclose(runtime.data.site("link_tcp").xmat, before_rotation)
            )
        finally:
            runtime.close()

    def test_moveit_trajectory_waypoints_unwrap_wrist_and_preserve_timing(self):
        runtime = MuJoCoRobotRuntime(
            valid_scene(profile_id="optical_housings"),
            show_viewer=False,
            realtime=False,
        )
        try:
            start = np.zeros(7, dtype=float)
            start[6] = -3.05
            points = [
                {
                    "positions": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 3.05],
                    "velocities": [0.0] * 7,
                    "time_from_start_s": 0.4,
                },
                {
                    "positions": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 3.10],
                    "velocities": [0.0] * 7,
                    "time_from_start_s": 1.1,
                },
            ]

            times, positions, velocities = runtime._moveit_trajectory_waypoints(
                points,
                joint_names=[f"joint{index}" for index in range(1, 8)],
                start_joints=start,
                fallback_duration_s=2.0,
            )

            self.assertEqual(times, [0.0, 0.4, 1.1])
            self.assertLess(abs(float(positions[1][6] - start[6])), 0.25)
            self.assertLess(abs(float(positions[2][6] - positions[1][6])), 0.1)
            self.assertIsNotNone(velocities[1])
        finally:
            runtime.close()

    def test_moveit_trajectory_preflight_blocks_before_execution(self):
        runtime = MuJoCoRobotRuntime(
            valid_scene(profile_id="optical_housings"),
            show_viewer=False,
            realtime=False,
        )
        points = [
            {
                "positions": [0.0, -0.6, 0.0, 1.2, 0.0, 1.4, 0.0],
                "time_from_start_s": 0.1,
            }
        ]
        preflight_calls = []

        def blocked_preflight(start, end, *, allowed_tag):
            preflight_calls.append((start.copy(), end.copy(), allowed_tag))
            raise CollisionPlanError("blocked before execute")

        def fail_if_executed():
            raise AssertionError("MoveIt trajectory executed before preflight")

        runtime._preflight_joint_path = blocked_preflight
        runtime._step = fail_if_executed

        try:
            with self.assertRaisesRegex(CollisionPlanError, "blocked"):
                runtime._execute_moveit_trajectory(
                    points,
                    joint_names=runtime_module.MOVEIT_JOINT_NAMES,
                    fallback_duration_s=0.2,
                    allowed_tag="tag_pick",
                )
        finally:
            runtime.close()

        self.assertEqual(len(preflight_calls), 1)
        self.assertEqual(preflight_calls[0][2], "tag_pick")

    def test_moveit_prepick_feasibility_accepts_seed_before_motion(self):
        progress = []
        runtime = MuJoCoRobotRuntime(
            valid_scene(profile_id="optical_housings"),
            show_viewer=False,
            realtime=False,
            progress_callback=lambda event: progress.append(dict(event)),
        )
        original_joints = runtime.current_joint_target.copy()
        planned_names = []

        def make_stage(name):
            return runtime_module.MoveItStagePlan(
                name=name,
                stage_request_id=name,
                position_m=np.zeros(3, dtype=float),
                rotation=np.eye(3),
                points=[],
                joint_names=runtime_module.MOVEIT_JOINT_NAMES,
                fallback_duration_s=0.1,
                allowed_tag=None,
                final_joints=original_joints.copy(),
            )

        def fake_plan(name, *args, **kwargs):
            del args, kwargs
            planned_names.append(name)
            return make_stage(name)

        def fake_carry(name, **kwargs):
            del kwargs
            planned_names.append(f"{name} carry")
            return runtime_module.MoveItCarryPlan(
                stage_name=name,
                route_index=0,
                stages=(make_stage(name),),
            )

        runtime._plan_moveit_stage = fake_plan
        runtime._preview_manual_pickup_to_lift = lambda *args, **kwargs: None
        runtime._plan_moveit_carry_route = fake_carry

        try:
            with patch.dict(
                os.environ,
                {runtime_module.MOVEIT_PREPICK_FEASIBILITY_SEEDS_ENV_VAR: "4"},
            ):
                plan = runtime._plan_moveit_pre_pick_feasibility(
                    "tag_pick",
                    source_xy=np.array((0.3, -0.18), dtype=float),
                    target_xy=np.array((0.3, -0.04), dtype=float),
                    source_rotation=runtime._target_rotation(0.0),
                    target_rotation=runtime._target_rotation(20.0),
                    approach_z=0.36,
                    grasp_z=0.30,
                    transfer_z=0.36,
                    release_z=0.306,
                    omitted_target={"tag_pick"},
                )
        finally:
            runtime.close()

        self.assertIsNotNone(plan)
        self.assertEqual(plan.seed_index, 1)
        self.assertEqual(plan.seed_total, 4)
        self.assertEqual(planned_names, ["moveit move above source", "moveit translate carry"])
        self.assertTrue(np.allclose(runtime.current_joint_target, original_joints))
        self.assertTrue(any("seed 1/4" in event["message"] for event in progress))
        self.assertTrue(any("accepted seed 1/4" in event["message"] for event in progress))

    def test_moveit_prepick_feasibility_reports_seed_count_on_failure(self):
        progress = []
        runtime = MuJoCoRobotRuntime(
            valid_scene(profile_id="optical_housings"),
            show_viewer=False,
            realtime=False,
            progress_callback=lambda event: progress.append(dict(event)),
        )
        runtime._plan_moveit_stage = lambda *args, **kwargs: (_ for _ in ()).throw(
            runtime_module.SimulatorError("seed failed")
        )

        try:
            with patch.dict(
                os.environ,
                {runtime_module.MOVEIT_PREPICK_FEASIBILITY_SEEDS_ENV_VAR: "3"},
            ):
                with self.assertRaisesRegex(CollisionPlanError, "after 3 seeds"):
                    runtime._plan_moveit_pre_pick_feasibility(
                        "tag_pick",
                        source_xy=np.array((0.3, -0.18), dtype=float),
                        target_xy=np.array((0.3, -0.04), dtype=float),
                        source_rotation=runtime._target_rotation(0.0),
                        target_rotation=runtime._target_rotation(20.0),
                        approach_z=0.36,
                        grasp_z=0.30,
                        transfer_z=0.36,
                        release_z=0.306,
                        omitted_target={"tag_pick"},
                    )
        finally:
            runtime.close()

        attempted = [
            event
            for event in progress
            if event.get("phase") == "prepick_feasibility"
            and "seed" in event.get("message", "")
        ]
        self.assertGreaterEqual(len(attempted), 3)
        self.assertTrue(any("failed after 3 seeds" in event["message"] for event in progress))

    def test_moveit_carry_stage_reports_direct_failure_without_routes(self):
        runtime = MuJoCoRobotRuntime(
            valid_scene(profile_id="optical_housings"),
            show_viewer=False,
            realtime=False,
        )
        planned_calls = []

        def fake_plan(
            name,
            position_m,
            rotation,
            *,
            speed_mm_s,
            omit_tags=None,
            attached_tag=None,
            orientation_tolerance_rad=0.05,
            joint7_continuity_tolerance_rad=0.0,
            path_orientation_constraint=None,
            path_joint7_continuity=False,
        ):
            del rotation, speed_mm_s, omit_tags, attached_tag, orientation_tolerance_rad
            planned_calls.append((name, np.asarray(position_m, dtype=float).copy()))
            self.assertIsNotNone(path_orientation_constraint)
            self.assertGreater(joint7_continuity_tolerance_rad, 0.0)
            self.assertTrue(path_joint7_continuity)
            raise CollisionPlanError("direct blocked")

        runtime._plan_moveit_stage = fake_plan
        runtime._preview_moveit_stage_plan = lambda planned, attached_pose: None

        try:
            with self.assertRaisesRegex(CollisionPlanError, "direct held-object"):
                runtime._moveit_carry_stage(
                    "moveit translate",
                    source_xy=np.array((0.138, -0.090), dtype=float),
                    target_xy=np.array((-0.163, 0.1185), dtype=float),
                    transfer_z=0.41,
                    rotation=runtime._target_rotation(0.0),
                    speed_mm_s=170.0,
                    omit_tags={"tag_pick"},
                    attached_tag="tag_pick",
                )
        finally:
            runtime.close()

        self.assertEqual([name for name, _ in planned_calls], ["moveit translate"])
        self.assertTrue(
            np.allclose(planned_calls[0][1], np.array((-0.163, 0.1185, 0.41)))
        )
        self.assertTrue(all("route" not in name for name, _ in planned_calls))

    def test_moveit_carry_stage_validates_place_retreat_at_transfer_z(self):
        runtime = MuJoCoRobotRuntime(
            valid_scene(profile_id="optical_housings"),
            show_viewer=False,
            realtime=False,
        )
        planned_calls = []
        executed_calls = []
        place_checks = []

        def fake_plan(
            name,
            position_m,
            rotation,
            *,
            speed_mm_s,
            omit_tags=None,
            attached_tag=None,
            orientation_tolerance_rad=0.05,
            joint7_continuity_tolerance_rad=0.0,
            path_orientation_constraint=None,
            path_joint7_continuity=False,
        ):
            del rotation, speed_mm_s, omit_tags, attached_tag, orientation_tolerance_rad
            planned_calls.append(name)
            self.assertIsNotNone(path_orientation_constraint)
            self.assertGreater(joint7_continuity_tolerance_rad, 0.0)
            self.assertTrue(path_joint7_continuity)
            return runtime_module.MoveItStagePlan(
                name=name,
                stage_request_id=name,
                position_m=np.asarray(position_m, dtype=float).copy(),
                rotation=np.eye(3),
                points=[],
                joint_names=runtime_module.MOVEIT_JOINT_NAMES,
                fallback_duration_s=0.1,
                allowed_tag="tag_pick",
                final_joints=np.zeros(7, dtype=float),
            )

        def fake_place_check(
            place_position_m,
            rotation,
            *,
            speed_mm_s,
            retreat_position_m=None,
            retreat_speed_mm_s=130.0,
            released_tag=None,
        ):
            del place_position_m, rotation, speed_mm_s
            place_checks.append(len(place_checks) + 1)
            self.assertIsNotNone(retreat_position_m)
            self.assertAlmostEqual(float(retreat_position_m[2]), 0.41)
            self.assertEqual(retreat_speed_mm_s, 130.0)
            self.assertEqual(released_tag, "tag_pick")

        def fake_execute(planned):
            executed_calls.append(planned.name)
            runtime.stage_trace.append(planned.name)

        runtime._plan_moveit_stage = fake_plan
        runtime._preview_moveit_stage_plan = lambda planned, attached_pose: None
        runtime._preflight_post_carry_place = fake_place_check
        runtime._execute_moveit_stage_plan = fake_execute

        try:
            runtime._moveit_carry_stage(
                "moveit translate",
                source_xy=np.array((0.138, -0.090), dtype=float),
                target_xy=np.array((-0.2387, 0.1671), dtype=float),
                transfer_z=0.41,
                rotation=runtime._target_rotation(0.0),
                speed_mm_s=170.0,
                omit_tags={"tag_pick"},
                attached_tag="tag_pick",
                place_position_m=np.array((-0.2387, 0.1671, 0.306), dtype=float),
                place_speed_mm_s=80.0,
            )
        finally:
            runtime.close()

        self.assertEqual(planned_calls[0], "moveit translate")
        self.assertEqual(place_checks, [1])
        self.assertEqual(executed_calls, ["moveit translate"])
        self.assertTrue(all("route" not in name for name in executed_calls))

    def test_moveit_equivalent_yaw_prefers_shortest_tool_rotation(self):
        runtime = MuJoCoRobotRuntime(
            valid_scene(profile_id="optical_housings"),
            show_viewer=False,
            realtime=False,
        )
        try:
            reference = runtime._target_rotation(180.0)

            chosen = runtime._moveit_equivalent_target_rotation(
                0.0,
                reference_rotation=reference,
            )

            self.assertLess(
                runtime_module._rotation_distance_deg(reference, chosen),
                1.0,
            )
            self.assertGreater(
                runtime_module._rotation_distance_deg(
                    runtime._target_rotation(0.0),
                    chosen,
                ),
                170.0,
            )
        finally:
            runtime.close()

    def test_moveit_table_collision_disabled_by_default_but_lowered_when_enabled(self):
        runtime = MuJoCoRobotRuntime(
            valid_scene(profile_id="optical_housings"),
            show_viewer=False,
            realtime=False,
        )
        try:
            objects = runtime._moveit_world_objects()
        finally:
            runtime.close()

        self.assertNotIn("cloudlab_tabletop", {item["id"] for item in objects})

        previous = os.environ.get("CLOUDLAB_MOVEIT_INCLUDE_TABLE")
        os.environ["CLOUDLAB_MOVEIT_INCLUDE_TABLE"] = "1"
        runtime = MuJoCoRobotRuntime(
            valid_scene(profile_id="optical_housings"),
            show_viewer=False,
            realtime=False,
        )
        try:
            objects = runtime._moveit_world_objects()
            table = next(item for item in objects if item["id"] == "cloudlab_tabletop")
            component = runtime.scene.components["tag_pick"]
        finally:
            runtime.close()
            if previous is None:
                os.environ.pop("CLOUDLAB_MOVEIT_INCLUDE_TABLE", None)
            else:
                os.environ["CLOUDLAB_MOVEIT_INCLUDE_TABLE"] = previous

        table_z = table["pose"]["position"][2]
        table_height = table["dimensions"][2]
        table_top_z = table_z + table_height / 2.0
        component_bottom_z = component.center_z_m - component.height_m / 2.0
        self.assertLess(table_top_z, component_bottom_z)

    def test_optical_contact_ik_recovers_from_moveit_branch_seed(self):
        runtime = MuJoCoRobotRuntime(
            valid_scene(profile_id="optical_housings"),
            show_viewer=False,
            realtime=False,
        )
        try:
            spec = runtime.scene.components["tag_pick"]
            moveit_branch_seed = np.array(
                [
                    -0.37945894784963297,
                    -0.3554319917385644,
                    0.1448549017772666,
                    1.4824207353670384,
                    0.029763937022609613,
                    1.8658151534690286,
                    2.601356494402212,
                ],
                dtype=float,
            )
            solution = runtime.ik.solve(
                runtime.model,
                np.array((0.320, -0.060, spec.grasp_tcp_z_m + 0.006)),
                runtime._target_rotation(15),
                moveit_branch_seed,
                runtime.home,
            )
        finally:
            runtime.close()

        self.assertEqual(len(solution), 7)

    def test_moveit_optical_housings_use_local_transfer_height(self):
        runtime = MuJoCoRobotRuntime(
            valid_scene(profile_id="optical_housings"),
            show_viewer=False,
            realtime=False,
        )
        travel_z_values = []

        def fake_moveit_stage(
            name,
            position_m,
            rotation,
            *,
            speed_mm_s,
            omit_tags=None,
            attached_tag=None,
        ):
            del rotation, speed_mm_s, omit_tags, attached_tag
            runtime.stage_trace.append(name)
            travel_z_values.append(float(position_m[2]))

        def fake_carry_stage(
            name,
            *,
            source_xy,
            target_xy,
            transfer_z,
            rotation,
            speed_mm_s,
            omit_tags,
            attached_tag,
            source_rotation=None,
            place_position_m=None,
            place_speed_mm_s=80.0,
        ):
            del source_xy, source_rotation, place_position_m, place_speed_mm_s
            fake_moveit_stage(
                name,
                np.array((*target_xy, transfer_z)),
                rotation,
                speed_mm_s=speed_mm_s,
                omit_tags=omit_tags,
                attached_tag=attached_tag,
            )

        def fake_controller_stage(name, arm, position_m, rotation, *, speed_mm_s):
            del arm, rotation, speed_mm_s
            runtime.stage_trace.append(name)
            if name in {"pickup clearance lift", "contact retreat"}:
                travel_z_values.append(float(position_m[2]))

        def fake_result(tag_id, **kwargs):
            del kwargs
            return MoveResult(
                tag_id=tag_id,
                x_mm=300.0,
                y_mm=-40.0,
                rotation_deg=20.0,
                stage_trace=tuple(runtime.stage_trace),
            )

        runtime._moveit_move_stage = fake_moveit_stage
        runtime._moveit_carry_stage = fake_carry_stage
        runtime._move_stage = fake_controller_stage
        runtime._validate_grasp_envelope = lambda spec: None
        runtime._settle = lambda duration_s: None
        runtime._placement_result = fake_result

        try:
            runtime._pick_and_place_moveit(
                "tag_pick",
                target_x_mm=300,
                target_y_mm=-40,
                target_rotation_deg=20,
            )
        finally:
            runtime.close()

        self.assertTrue(travel_z_values)
        for z_m in travel_z_values:
            self.assertAlmostEqual(z_m, 0.41, places=6)
            self.assertLess(z_m, 0.571412)

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
