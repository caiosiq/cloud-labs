"""Regression coverage for post-release home routes near a wrist wrap limit."""
import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import mujoco

from simulation_edge.bootstrap import _resolve_catalog_rows
from simulation_edge.host.scene import build_scene_spec
from simulation_edge.host.runtime import (
    CollisionPlanError,
    MuJoCoRobotRuntime,
    RadialJointStageAction,
    SimulatorError,
)


class RadialHomeFallbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        lab_view = root / "lab_view"
        cls.temp = tempfile.TemporaryDirectory()
        cls.env = mock.patch.dict("os.environ", {
            "CLOUDLAB_RADIAL_LIBRARY_FILE": str(
                root / "radial_motion_libraries/optical_housings_noninverted.json"
            ),
            "CLOUDLAB_MUJOCO_LOG_DIR": cls.temp.name,
            "CLOUDLAB_RADIAL_JOINT1_MIN_DEG": "",
            "CLOUDLAB_RADIAL_JOINT1_MAX_DEG": "",
        })
        cls.env.start()
        cls.addClassCleanup(cls.env.stop)
        cls.addClassCleanup(cls.temp.cleanup)
        scene = build_scene_spec(
            json.loads((lab_view / "layout.json").read_text()),
            _resolve_catalog_rows(lab_view),
            json.loads((lab_view / "lab_state.json").read_text()),
            profile_id="optical_housings",
        )
        cls.runtime = MuJoCoRobotRuntime(scene, show_viewer=False, realtime=False)
        cls.addClassCleanup(cls.runtime.close)
        cls.initial = cls.runtime._motion_snapshot()

    def setUp(self):
        self.runtime._restore_motion_snapshot(self.initial)

    def tearDown(self):
        self.runtime._restore_motion_snapshot(self.initial)

    def plan(self, y):
        return self.runtime._build_radial_motion_plan(
            "tag_22", target_x_mm=-220, target_y_mm=y, target_rotation_deg=0,
        )

    def test_frame_envelope_destination_is_rejected_before_pickup_planning(self):
        with mock.patch.object(self.runtime, "_plan_radial_physical_home_transition") as entry:
            with self.assertRaisesRegex(CollisionPlanError, r"Requested Y = 490\.4 mm exceeds.*444\.4 mm"):
                self.runtime._build_radial_motion_plan(
                    "tag_22", target_x_mm=200, target_y_mm=490.4, target_rotation_deg=-90,
                )
        entry.assert_not_called()
        np.testing.assert_array_equal(self.runtime.data.qpos, self.initial["qpos"])

    def test_frame_boundary_margin_rejects_before_pickup_planning(self):
        limits = self.runtime._radial_tcp_axis_limits_m()
        for axis in ("x", "y"):
            for side, direction in (("min", -1), ("max", 1)):
                for inset_mm in (0, .001, .01, .099):
                    xy_mm = [200., 200.]
                    xy_mm[0 if axis == "x" else 1] = (
                        limits[f"{axis}_{side}"] * 1000 - direction * inset_mm
                    )
                    with self.subTest(axis=axis, side=side, inset_mm=inset_mm):
                        with mock.patch.object(self.runtime, "_plan_radial_physical_home_transition") as entry:
                            with self.assertRaisesRegex(CollisionPlanError, "Requested .*destination margin"):
                                self.runtime._build_radial_motion_plan(
                                    "tag_22", target_x_mm=xy_mm[0], target_y_mm=xy_mm[1],
                                    target_rotation_deg=-90,
                                )
                        entry.assert_not_called()
                        np.testing.assert_array_equal(self.runtime.data.qpos, self.initial["qpos"])

    def test_nearby_frame_envelope_destination_still_plans(self):
        plan = self.runtime._build_radial_motion_plan(
            "tag_22", target_x_mm=200, target_y_mm=440, target_rotation_deg=-90,
        )
        np.testing.assert_allclose(plan.target_xy, [.2, .440])

    def test_reproduced_move_allows_long_rotation_only_after_release(self):
        plan = self.plan(-320)
        names = [getattr(getattr(a, "stage", a), "name", "") for a in plan.actions]
        first = "home coordinated rotate"
        self.assertLess(names.index("open and release"), names.index(first))
        self.assertLess(names.index("post-place radial egress"), names.index(first))
        self.assertLess(names.index(first), names.index("radial observation home to physical home"))
        home_actions = [a for a in plan.actions if isinstance(a, RadialJointStageAction)
                        and a.stage.name == "home coordinated rotate"]
        self.assertEqual(len(home_actions), 1)
        home = home_actions[0].stage
        self.assertAlmostEqual(
            np.rad2deg(home.waypoints[-1][6] - home.waypoints[0][6]), 214.51, delta=.05,
        )
        self.assertAlmostEqual(np.rad2deg(home.waypoints[-1][6]), 0, places=5)
        for action in home_actions:
            self.assertIsNone(action.attached_tag)
            self.assertIsNone(action.stage.allowed_tag)
            self.assertTrue(action.stage.allow_long_wrist_return)
        np.testing.assert_array_equal(self.runtime.data.qpos, self.initial["qpos"])
        np.testing.assert_array_equal(self.runtime.current_joint_target, self.initial["current_joint_target"])

    def test_nearby_successful_move_keeps_combined_home(self):
        plan = self.plan(-220)
        names = [a.stage.name for a in plan.actions if isinstance(a, RadialJointStageAction)]
        self.assertIn("home coordinated rotate", names)
        home = next(a.stage for a in plan.actions if isinstance(a, RadialJointStageAction)
                    and a.stage.name == "home coordinated rotate")
        self.assertLess(abs(home.waypoints[-1][6] - home.waypoints[0][6]), np.pi)

    def test_pose_failure_rejects_all_return_candidates(self):
        with mock.patch.object(self.runtime, "_build_radial_rotation_stage",
                               side_effect=CollisionPlanError("unsafe pose")) as rotate:
            with self.assertRaisesRegex(CollisionPlanError, "unsafe pose"):
                self.runtime._plan_radial_home_rotations(
                    radius_m=.193, source_theta=0., target_theta=0.,
                    source_rotation=self.runtime._target_rotation(90),
                    target_rotation=self.runtime._target_rotation(0),
                    current=self.runtime.current_joint_target.copy(),
                )
        self.assertGreaterEqual(rotate.call_count, 1)

    def test_collision_on_fallback_path_rejects_whole_transaction(self):
        original = self.runtime._preflight_radial_joint_path
        checked = []

        def check_path(*args, **kwargs):
            checked.append(kwargs["stage"])
            if kwargs["stage"] == "home coordinated rotate":
                raise CollisionPlanError("obstructed fallback")
            return original(*args, **kwargs)

        with mock.patch.object(self.runtime, "_preflight_radial_joint_path", side_effect=check_path):
            with self.assertRaisesRegex(CollisionPlanError, "obstructed fallback"):
                self.plan(-320)
        self.assertIn("home coordinated rotate", checked)
        np.testing.assert_array_equal(self.runtime.data.qpos, self.initial["qpos"])
        np.testing.assert_array_equal(self.runtime.data.ctrl, self.initial["ctrl"])
        np.testing.assert_array_equal(self.runtime.data.eq_active, self.initial["eq_active"])

    def test_fallback_still_rejects_incompatible_wrist_limits(self):
        theta = np.deg2rad(-124.508523)
        current = self.runtime._radial_carry_pose_joints(
            .193, theta, self.runtime._target_rotation(90),
            seed=np.deg2rad([-304.508523, 47.5, -180, 61.7, 0, 109.3, -214.508523]),
            stage="test home start",
        )
        joint_id = self.runtime.model.joint("joint7").id
        original_limits = self.runtime.model.jnt_range[joint_id].copy()
        try:
            self.runtime.model.jnt_range[joint_id] = np.deg2rad([-360, -100])
            with self.assertRaisesRegex(CollisionPlanError, "joint7 target.*no equivalent"):
                self.runtime._plan_radial_home_rotations(
                    radius_m=.193, source_theta=theta, target_theta=0.,
                    source_rotation=self.runtime._target_rotation(90),
                    target_rotation=self.runtime._target_rotation(0), current=current,
                )
        finally:
            self.runtime.model.jnt_range[joint_id] = original_limits

    def test_fallback_still_rejects_unreachable_base_return(self):
        theta = np.deg2rad(-124.508523)
        current = self.runtime._radial_carry_pose_joints(
            .193, theta, self.runtime._target_rotation(90),
            seed=np.deg2rad([-304.508523, 47.5, -180, 61.7, 0, 109.3, -214.508523]),
            stage="test home start",
        )
        joint_id = self.runtime.model.joint("joint1").id
        original_limits = self.runtime.model.jnt_range[joint_id].copy()
        try:
            self.runtime.model.jnt_range[joint_id] = np.deg2rad([-350, -200])
            with self.assertRaisesRegex(CollisionPlanError, "no equivalent endpoint"):
                self.runtime._plan_radial_home_rotations(
                    radius_m=.193, source_theta=theta, target_theta=0.,
                    source_rotation=self.runtime._target_rotation(90),
                    target_rotation=self.runtime._target_rotation(0), current=current,
                )
        finally:
            self.runtime.model.jnt_range[joint_id] = original_limits

    def test_fallback_execution_preserves_gentle_rotation_finish(self):
        runtime = MuJoCoRobotRuntime.__new__(MuJoCoRobotRuntime)
        runtime._gripper_position = 0.
        runtime.allowed_collision_tag = None
        runtime.stage_trace = []
        runtime._log = mock.Mock()
        runtime._runtime_snapshot = mock.Mock(return_value={})
        runtime._emit_progress = mock.Mock()
        runtime._radial_joint_duration_s = mock.Mock(return_value=1.)
        runtime._drive_joint_waypoints = mock.Mock()
        start = np.zeros(7)
        end = start.copy()
        end[6] = np.deg2rad(90)
        for name in ("home coordinated rotate", "radial coordinated rotate"):
            with self.subTest(stage=name):
                runtime._execute_radial_joint_waypoints(
                    name, (start, end), allowed_tag=None, prevalidated=True,
                )
                self.assertTrue(runtime._drive_joint_waypoints.call_args.kwargs["gentle_final"])
                self.assertEqual(runtime._drive_joint_waypoints.call_args.args[1], [2.])

    def test_all_three_live_stress_failures_have_valid_complete_plans(self):
        adr = int(self.runtime.model.joint(self.runtime.scene.components["tag_22"].joint_name).qposadr[0])
        cases = [(-90, -220, 320, -135), (-90, 220, 320, -180), (-30, 280, -280, 135)]
        for source_yaw, x, y, rotation in cases:
            with self.subTest(source_yaw=source_yaw, target=(x, y, rotation)):
                self.runtime._restore_motion_snapshot(self.initial)
                angle = math.radians(source_yaw) / 2
                self.runtime.data.qpos[adr+3:adr+7] = [math.cos(angle), 0, 0, math.sin(angle)]
                mujoco.mj_forward(self.runtime.model, self.runtime.data)
                before = self.runtime._motion_snapshot()
                plan = self.runtime._build_radial_motion_plan(
                    "tag_22", target_x_mm=x, target_y_mm=y, target_rotation_deg=rotation,
                )
                joints = [a.stage for a in plan.actions if isinstance(a, RadialJointStageAction)]
                self.assertTrue(any(s.allow_long_wrist_return for s in joints))
                self.assertEqual(joints[-1].name, "radial observation home to physical home")
                for stage in joints:
                    self.runtime._validate_radial_wrist_travel(
                        stage.name, stage.waypoints,
                        allow_long_wrist_return=stage.allow_long_wrist_return,
                    )
                np.testing.assert_array_equal(self.runtime.data.qpos, before["qpos"])

    def test_full_negative_wrist_turn_returns_continuously_without_clipping(self):
        current = self.runtime._radial_carry_pose_joints(
            .193, 0., self.runtime._target_rotation(0), stage="test home start",
        )
        current[6] = -2 * np.pi
        before = self.runtime._motion_snapshot()
        stages = self.runtime._plan_radial_home_rotations(
            radius_m=.193, source_theta=0., target_theta=0.,
            source_rotation=self.runtime._target_rotation(0),
            target_rotation=self.runtime._target_rotation(0), current=current,
        )
        self.assertEqual(len(stages), 1)
        np.testing.assert_allclose(stages[0].waypoints[0], current)
        self.assertAlmostEqual(stages[-1].waypoints[-1][6], 0, places=8)
        for stage in stages:
            self.assertTrue(stage.allow_long_wrist_return)
            self.assertAlmostEqual(np.rad2deg(stage.waypoints[-1][6]-stage.waypoints[0][6]), 360, places=6)
            with self.assertRaisesRegex(CollisionPlanError, "limited.*180"):
                self.runtime._validate_radial_wrist_travel(stage.name, stage.waypoints)
            for first, last in zip(stage.waypoints, stage.waypoints[1:]):
                self.assertLess(np.max(np.abs(last-first)), np.deg2rad(31))
        np.testing.assert_array_equal(self.runtime.data.qpos, before["qpos"])

    def test_obstructed_shortest_candidate_tries_another_valid_home_branch(self):
        theta = np.deg2rad(-124.508523)
        current = self.runtime._radial_carry_pose_joints(
            .193, theta, self.runtime._target_rotation(90),
            seed=np.deg2rad([-304.508523, 47.5, -180, 61.7, 0, 109.3, -214.508523]),
            stage="test home start",
        )
        original = self.runtime._preflight_radial_stage
        blocked = []

        def check_stage(stage, **kwargs):
            if stage.waypoints[-1][0] < 0:
                blocked.append(stage.name)
                raise CollisionPlanError("obstructed shortest home branch")
            return original(stage, **kwargs)

        before = self.runtime._motion_snapshot()
        with mock.patch.object(self.runtime, "_preflight_radial_stage", side_effect=check_stage):
            stages = self.runtime._plan_radial_home_rotations(
                radius_m=.193, source_theta=theta, target_theta=0.,
                source_rotation=self.runtime._target_rotation(90),
                target_rotation=self.runtime._target_rotation(0), current=current,
            )
        self.assertTrue(blocked)
        self.assertAlmostEqual(np.rad2deg(stages[-1].waypoints[-1][0]), 180, places=5)
        np.testing.assert_array_equal(self.runtime.data.qpos, before["qpos"])

    def test_long_return_permission_cannot_be_used_while_carrying(self):
        stage = self.runtime._plan_radial_home_rotations(
            radius_m=.193, source_theta=0., target_theta=0.,
            source_rotation=self.runtime._target_rotation(0),
            target_rotation=self.runtime._target_rotation(0),
            current=self.runtime._radial_carry_pose_joints(
                .193, 0., self.runtime._target_rotation(0), stage="test home start",
            ) + np.array([0,0,0,0,0,0,-2*np.pi]),
        )[0]
        carried = type(stage)(stage.name, stage.waypoints, "tag_22", True)
        with self.assertRaisesRegex(SimulatorError, "open, empty gripper"):
            self.runtime._preflight_radial_stage(carried)
        with self.assertRaisesRegex(SimulatorError, "open, empty gripper"):
            self.runtime._execute_radial_joint_waypoints(
                stage.name, stage.waypoints, allowed_tag="tag_22",
                prevalidated=True, allow_long_wrist_return=True,
            )
        self.runtime._gripper_position = 255.
        with self.assertRaisesRegex(SimulatorError, "open, empty gripper"):
            self.runtime._preflight_radial_stage(stage)

    def test_home_return_preserves_the_cord_limited_base_representation(self):
        joint_id = self.runtime.model.joint("joint1").id
        original_limits = self.runtime.model.jnt_range[joint_id].copy()
        try:
            self.runtime.model.jnt_range[joint_id] = np.deg2rad([-10, 359.9])
            current = self.runtime._radial_carry_pose_joints(
                .193, np.deg2rad(170), self.runtime._target_rotation(90),
                stage="test cord-limited home start",
            )
            self.assertAlmostEqual(np.rad2deg(current[0]), 350, places=5)
            stages = self.runtime._plan_radial_home_rotations(
                radius_m=.193, source_theta=np.deg2rad(170), target_theta=0.,
                source_rotation=self.runtime._target_rotation(90),
                target_rotation=self.runtime._target_rotation(0), current=current,
            )
            self.assertAlmostEqual(np.rad2deg(stages[0].waypoints[0][0]), 350, places=5)
            self.assertAlmostEqual(np.rad2deg(stages[-1].waypoints[-1][0]), 180, places=5)
            for stage in stages:
                for waypoint in stage.waypoints:
                    self.assertGreaterEqual(np.rad2deg(waypoint[0]), -10)
                    self.assertLessEqual(np.rad2deg(waypoint[0]), 359.9)
        finally:
            self.runtime.model.jnt_range[joint_id] = original_limits


if __name__ == "__main__":
    unittest.main()
