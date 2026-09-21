import unittest
import tempfile
from pathlib import Path
from unittest import mock
from types import SimpleNamespace

import numpy as np
import mujoco

import simulation_edge.host.runtime as runtime_module
from simulation_edge.host.runtime import (
    GRIPPER_CLOSED,
    GRIPPER_OPEN,
    RADIAL_PHYSICAL_HOME_JOINTS_DEG,
    MuJoCoRobotRuntime,
    MUJOCO_HIGH_RES_CAPTURE_KEY,
    MUJOCO_PAUSE_KEY_REPEAT_GUARD_S,
    MUJOCO_PHOTO_PAUSE_KEY,
    RadialAssistedWeldAction,
    RadialClearCollisionAction,
    RadialComponentObservation,
    RadialGripperAction,
    RadialJointStageAction,
    RadialJointStagePlan,
    RadialMotionPlan,
    RadialMotionLibrary,
    RadialPoseSample,
    RadialRebaseAction,
    RadialSettleAction,
    RadialValidateGraspAction,
    RadialWorldSnapshot,
    RadialVerticalPose,
    SimulatorError,
)


class RadialMotionPlanTests(unittest.TestCase):
    @staticmethod
    def _analytical_runtime():
        runtime = MuJoCoRobotRuntime.__new__(MuJoCoRobotRuntime)
        runtime.scene = SimpleNamespace(
            lab_bounds_mm={
                "x_min": -590.55,
                "x_max": 590.55,
                "y_min": -609.6,
                "y_max": 609.6,
            },
            frame_safety_clearance_mm=76.2,
            tcp_tool_envelope_radius_mm=88.9,
        )
        runtime._radial_kinematic_radius_bounds_m = lambda: (0.134, 0.550)
        return runtime

    def test_analytical_radius_uses_corrected_asymmetric_frame(self):
        runtime = self._analytical_runtime()

        self.assertAlmostEqual(runtime._radial_max_radius_at_theta_m(0.0), 0.42545)
        self.assertAlmostEqual(
            runtime._radial_max_radius_at_theta_m(np.deg2rad(45.0)),
            0.550,
        )
        self.assertAlmostEqual(
            runtime._radial_max_radius_at_theta_m(np.deg2rad(90.0)),
            0.4445,
        )

    def test_physical_home_matches_experimental_robot_manager(self):
        self.assertEqual(
            RADIAL_PHYSICAL_HOME_JOINTS_DEG,
            (180.0, 75.0, -180.0, 20.0, 0.0, 90.0, -60.0),
        )

    def test_f8_key_requests_high_resolution_capture(self):
        runtime = MuJoCoRobotRuntime.__new__(MuJoCoRobotRuntime)
        runtime._capture_requested = False

        runtime._on_viewer_key(MUJOCO_HIGH_RES_CAPTURE_KEY)

        self.assertTrue(runtime._capture_requested)

    def test_f9_key_toggles_photo_pause(self):
        runtime = MuJoCoRobotRuntime.__new__(MuJoCoRobotRuntime)
        runtime._photo_paused = False

        with (
            mock.patch("builtins.print"),
            mock.patch.object(
                runtime_module.time,
                "perf_counter",
                side_effect=(10.0, 11.0),
            ),
        ):
            runtime._on_viewer_key(MUJOCO_PHOTO_PAUSE_KEY)
            self.assertTrue(runtime._photo_paused)
            runtime._on_viewer_key(MUJOCO_PHOTO_PAUSE_KEY)

        self.assertFalse(runtime._photo_paused)

    def test_f9_keyboard_repeat_does_not_resume_motion(self):
        runtime = MuJoCoRobotRuntime.__new__(MuJoCoRobotRuntime)
        runtime._photo_paused = False
        quiet_time = MUJOCO_PAUSE_KEY_REPEAT_GUARD_S + 0.1

        with (
            mock.patch("builtins.print"),
            mock.patch.object(
                runtime_module.time,
                "perf_counter",
                side_effect=(10.0, 10.4, 10.45, 10.5, 10.5 + quiet_time),
            ),
        ):
            runtime._on_viewer_key(MUJOCO_PHOTO_PAUSE_KEY)
            self.assertTrue(runtime._photo_paused)
            for _ in range(3):
                runtime._on_viewer_key(MUJOCO_PHOTO_PAUSE_KEY)
                self.assertTrue(runtime._photo_paused)
            runtime._on_viewer_key(MUJOCO_PHOTO_PAUSE_KEY)

        self.assertFalse(runtime._photo_paused)

    def test_photo_pause_services_capture_without_advancing_physics(self):
        runtime = MuJoCoRobotRuntime.__new__(MuJoCoRobotRuntime)
        runtime.model = SimpleNamespace(opt=SimpleNamespace(timestep=0.002))
        runtime.data = object()
        runtime.realtime = True
        runtime.playback_rate = 1.0
        runtime._photo_paused = True
        runtime._capture_requested = True
        runtime._viewer_sync_interval_s = 1.0 / 60.0
        runtime._last_viewer_sync_perf_s = 0.0
        runtime._viewer_entered = mock.Mock()
        runtime._viewer_entered.is_running.return_value = True
        runtime._capture_high_resolution_png = mock.Mock()

        with (
            mock.patch.object(runtime_module.mujoco, "mj_step") as mj_step,
            mock.patch.object(runtime_module.time, "perf_counter", side_effect=(10.0, 10.0)),
            mock.patch.object(runtime_module.time, "sleep") as sleep,
        ):
            runtime._step()

        runtime._capture_high_resolution_png.assert_called_once_with()
        runtime._viewer_entered.sync.assert_called_once_with()
        sleep.assert_called_once_with(0.01)
        mj_step.assert_not_called()

    def test_png_writer_produces_lossless_rgb_png(self):
        pixels = np.zeros((3, 5, 3), dtype=np.uint8)
        pixels[:, :, 0] = 255
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "capture.png"
            MuJoCoRobotRuntime._write_rgb_png(path, pixels)
            payload = path.read_bytes()

        self.assertEqual(payload[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(int.from_bytes(payload[16:20], "big"), 5)
        self.assertEqual(int.from_bytes(payload[20:24], "big"), 3)

    def test_capture_background_accepts_six_digit_hex(self):
        np.testing.assert_array_equal(
            MuJoCoRobotRuntime._parse_hex_rgb("#2B3440"),
            np.array([43, 52, 64], dtype=np.uint8),
        )

        with self.assertRaisesRegex(ValueError, "six-digit hex color"):
            MuJoCoRobotRuntime._parse_hex_rgb("slate")

    def test_paper_render_disables_model_textures(self):
        option = mujoco.MjvOption()
        self.assertTrue(option.flags[mujoco.mjtVisFlag.mjVIS_TEXTURE])

        MuJoCoRobotRuntime._disable_model_textures(option)

        self.assertFalse(option.flags[mujoco.mjtVisFlag.mjVIS_TEXTURE])

    def test_physical_home_transitions_are_explicit_and_directional(self):
        runtime = MuJoCoRobotRuntime.__new__(MuJoCoRobotRuntime)
        physical_home = np.radians(RADIAL_PHYSICAL_HOME_JOINTS_DEG)
        observation_home = np.radians((180.0, 47.5, -180.0, 61.7, 0.0, 109.3, 0.0))
        runtime.current_joint_target = physical_home.copy()
        runtime._radial_observation_home_joints = mock.Mock(
            return_value=observation_home.copy()
        )
        runtime._radial_physical_home_joints = mock.Mock(
            return_value=physical_home.copy()
        )
        runtime._preflight_radial_physical_home_transition = mock.Mock()

        entry = runtime._plan_radial_physical_home_transition(
            to_observation_home=True
        )
        self.assertEqual(entry.name, "physical home to radial observation home")
        np.testing.assert_allclose(entry.waypoints[0], physical_home)
        np.testing.assert_allclose(entry.waypoints[-1], observation_home)
        runtime._preflight_radial_physical_home_transition.assert_called_once_with(
            entry
        )

        runtime.current_joint_target = observation_home.copy()
        runtime._preflight_radial_physical_home_transition.reset_mock()
        exit_stage = runtime._plan_radial_physical_home_transition(
            to_observation_home=False
        )
        self.assertEqual(
            exit_stage.name,
            "radial observation home to physical home",
        )
        np.testing.assert_allclose(exit_stage.waypoints[0], observation_home)
        np.testing.assert_allclose(exit_stage.waypoints[-1], physical_home)
        runtime._preflight_radial_physical_home_transition.assert_called_once_with(
            exit_stage
        )

    def test_rotation_sweep_uses_tightest_crossed_axis(self):
        runtime = self._analytical_runtime()

        self.assertAlmostEqual(
            runtime._radial_rotation_sweep_max_radius_m(
                np.deg2rad(45.0),
                np.deg2rad(135.0),
            ),
            0.4445,
        )

    def test_diagnostic_unsafe_poses_are_kinematic_only_candidates(self):
        runtime = MuJoCoRobotRuntime.__new__(MuJoCoRobotRuntime)
        safe_pose = RadialVerticalPose(0.55, np.zeros(7), 0.1, 0.0)
        safe_sample = RadialPoseSample(
            0.348,
            np.zeros(7),
            0.1,
            0.0,
            (safe_pose,),
        )
        library = RadialMotionLibrary(
            version=3,
            profile_id="test",
            carry_z_m=0.55,
            grasp_z_m=0.30,
            max_vertical_z_m=0.55,
            min_radius_m=0.134,
            max_radius_m=0.55,
            step_m=0.005,
            component_height_limit_m=0.34,
            height_zone_min_radius_m=0.134,
            height_zone_margin_m=0.0,
            samples=(safe_sample,),
            unsafe=(
                {
                    "diagnostic_only": True,
                    "radius_m": 0.55,
                    "joints": [0.1] * 7,
                    "vertical_poses": [
                        {"z_m": 0.55, "joints": [0.1] * 7}
                    ],
                },
                {"radius_m": 0.56, "vertical_poses": []},
            ),
        )
        runtime._load_radial_motion_library = lambda: library

        candidates = runtime._radial_kinematic_samples()

        self.assertEqual([sample.radius_m for sample in candidates], [0.348, 0.55])
        self.assertEqual([sample.radius_m for sample in library.samples], [0.348])

    def test_transfer_orders_rotate_before_extension_and_after_retraction(self):
        runtime = self._analytical_runtime()
        runtime.current_joint_target = np.zeros(7)
        runtime._log = lambda *args, **kwargs: None
        runtime._target_yaw_from_rotation = lambda rotation: 0.0
        runtime._radial_rotation_sweep_max_radius_m = lambda source, target: 0.55

        def pose(radius, theta, rotation, *, seed, stage):
            del rotation, seed, stage
            result = np.zeros(7)
            result[0] = theta
            result[1] = radius
            return result

        def rotation_stage(**kwargs):
            current = kwargs["current"]
            target = current.copy()
            target[0] = kwargs["target_theta"]
            return RadialJointStagePlan(
                kwargs["name"],
                (current.copy(), target),
                kwargs["tag_id"],
            )

        runtime._radial_carry_pose_joints = pose
        runtime._plan_radial_coordinated_rotation = rotation_stage
        identity = np.eye(3)

        outward = runtime._plan_radial_transfer(
            "tag_1",
            source_xy=np.array((0.30, 0.0)),
            target_xy=np.array((0.55 / np.sqrt(2.0),) * 2),
            source_rotation=identity,
            target_rotation=identity,
        )
        inward = runtime._plan_radial_transfer(
            "tag_1",
            source_xy=np.array((0.55 / np.sqrt(2.0),) * 2),
            target_xy=np.array((0.30, 0.0)),
            source_rotation=identity,
            target_rotation=identity,
        )

        self.assertEqual(
            [stage.name for stage in outward],
            ["radial source carry", "radial coordinated rotate", "outer transfer radial extend"],
        )
        self.assertEqual(
            [stage.name for stage in inward],
            ["radial source carry", "outer transfer radial retract", "radial coordinated rotate"],
        )

    def test_mujoco_playback_rate_scales_wall_clock_not_simulation_step(self):
        runtime = MuJoCoRobotRuntime.__new__(MuJoCoRobotRuntime)
        runtime.model = SimpleNamespace(opt=SimpleNamespace(timestep=0.002))
        runtime.data = object()
        runtime.realtime = True
        runtime.playback_rate = 2.0
        runtime._viewer_sync_interval_s = 1.0
        runtime._last_viewer_sync_perf_s = 10.0
        runtime._viewer_entered = mock.Mock()
        runtime._viewer_entered.is_running.return_value = True

        with (
            mock.patch.object(runtime_module.mujoco, "mj_step") as mj_step,
            mock.patch.object(
                runtime_module.time,
                "perf_counter",
                side_effect=(10.0, 10.0, 10.0),
            ),
            mock.patch.object(runtime_module.time, "sleep") as sleep,
        ):
            runtime._step()

        mj_step.assert_called_once_with(runtime.model, runtime.data)
        self.assertAlmostEqual(sleep.call_args.args[0], 0.001)

    def test_viewer_frame_interval_starts_after_blocking_sync_returns(self):
        runtime = MuJoCoRobotRuntime.__new__(MuJoCoRobotRuntime)
        runtime.model = SimpleNamespace(opt=SimpleNamespace(timestep=0.002))
        runtime.data = object()
        runtime.realtime = False
        runtime.playback_rate = 1.0
        runtime._viewer_sync_interval_s = 1.0 / 30.0
        runtime._last_viewer_sync_perf_s = 9.0
        runtime._viewer_entered = mock.Mock()
        runtime._viewer_entered.is_running.return_value = True

        with (
            mock.patch.object(runtime_module.mujoco, "mj_step"),
            mock.patch.object(
                runtime_module.time,
                "perf_counter",
                # start, frame-due check, time after blocking viewer sync
                side_effect=(10.0, 10.0, 10.02),
            ),
        ):
            runtime._step()

        runtime._viewer_entered.sync.assert_called_once_with()
        self.assertEqual(runtime._last_viewer_sync_perf_s, 10.02)

    def test_radial_joint_speed_is_configurable_in_degrees_per_second(self):
        runtime = MuJoCoRobotRuntime.__new__(MuJoCoRobotRuntime)
        start = np.zeros(7)
        end = np.zeros(7)
        end[0] = np.deg2rad(180.0)

        with mock.patch.dict(
            "os.environ",
            {"CLOUDLAB_RADIAL_JOINT_SPEED_DEG_PER_S": "30"},
        ):
            duration_s = runtime._radial_joint_duration_s(start, end)

        # Long moves must not be shortened past the requested 30 deg/s.
        self.assertAlmostEqual(duration_s, 6.0)

    def test_joint_speed_scales_short_and_long_radial_segments_uniformly(self):
        runtime = MuJoCoRobotRuntime.__new__(MuJoCoRobotRuntime)
        start = np.zeros(7)
        segment_deltas_deg = (1.0, 10.0, 90.0)

        def durations_at(speed_deg_s):
            with mock.patch.dict(
                "os.environ",
                {
                    "CLOUDLAB_RADIAL_JOINT_SPEED_DEG_PER_S": str(
                        speed_deg_s
                    )
                },
            ):
                return [
                    runtime._radial_joint_duration_s(
                        start,
                        np.array(
                            (
                                np.deg2rad(delta_deg),
                                0.0,
                                0.0,
                                0.0,
                                0.0,
                                0.0,
                                0.0,
                            )
                        ),
                    )
                    for delta_deg in segment_deltas_deg
                ]

        durations_30 = durations_at(30.0)
        durations_80 = durations_at(80.0)
        for duration_30, duration_80 in zip(durations_30, durations_80):
            self.assertAlmostEqual(duration_30 / duration_80, 80.0 / 30.0)

    def test_world_snapshot_normalizes_backend_component_pose(self):
        observation = RadialComponentObservation(
            tag_id="tag_1",
            position_m=np.array((0.10, 0.20, 0.30)),
            yaw_deg=45.0,
            source="test",
        )
        snapshot = RadialWorldSnapshot(
            components={"tag_1": observation},
            current_joints=np.zeros(7),
            gripper_position=0.0,
            frame_id="test_frame_m",
            source="test_backend",
        )

        self.assertIs(snapshot.component("tag_1"), observation)
        self.assertEqual(snapshot.source, "test_backend")
        self.assertEqual(snapshot.frame_id, "test_frame_m")
        with self.assertRaises(SimulatorError):
            snapshot.component("missing")

    def test_joint_stage_action_captures_nominal_segment_durations(self):
        runtime = MuJoCoRobotRuntime.__new__(MuJoCoRobotRuntime)
        start = np.zeros(7)
        middle = np.array((0.10, -0.20, 0.05, 0.0, 0.0, 0.0, 0.0))
        end = np.array((0.20, -0.30, 0.10, 0.0, 0.0, 0.0, 0.15))
        stage = RadialJointStagePlan(
            "radial translate",
            (start, middle, end),
            "tag_1",
        )

        action = runtime._radial_joint_stage_action(
            stage,
            attached_tag="tag_1",
            validate_grasp_after="radial translate",
        )

        self.assertIs(action.stage, stage)
        self.assertEqual(action.attached_tag, "tag_1")
        self.assertEqual(action.validate_grasp_after, "radial translate")
        self.assertEqual(
            action.nominal_durations_s,
            (
                runtime._radial_joint_duration_s(start, middle),
                runtime._radial_joint_duration_s(middle, end),
            ),
        )

    def test_executor_consumes_plan_actions_in_order(self):
        runtime = MuJoCoRobotRuntime.__new__(MuJoCoRobotRuntime)
        runtime.scene = SimpleNamespace(
            components={"tag_1": SimpleNamespace(tag_id="tag_1")}
        )
        runtime.stage_trace = []
        runtime.allowed_collision_tag = "stale"
        runtime.events = []

        def fake_component_relative_pose(tag_id):
            runtime.events.append(("attached_pose", tag_id))
            return ("attached", tag_id)

        def fake_execute_joint_stage(
            name,
            waypoints,
            *,
            allowed_tag,
            attached_pose=None,
            prevalidated=False,
        ):
            runtime.events.append(
                (
                    "joint",
                    name,
                    allowed_tag,
                    attached_pose,
                    prevalidated,
                    runtime.allowed_collision_tag,
                    len(waypoints),
                )
            )
            runtime.stage_trace.append(name)

        def fake_settle(duration_s):
            runtime.events.append(("settle", duration_s))
            if duration_s == 0.25:
                runtime.allowed_collision_tag = "needs-clear"

        def fake_validate_grasp(spec, *, stage):
            runtime.events.append(("validate", spec.tag_id, stage))

        def fake_rebase(*, reason):
            runtime.events.append(("rebase", reason))

        runtime._component_relative_pose = fake_component_relative_pose
        runtime._execute_radial_joint_waypoints = fake_execute_joint_stage
        runtime._settle = fake_settle
        runtime._validate_physical_grasp = fake_validate_grasp
        runtime._rebase_radial_periodic_joint_branches = fake_rebase
        runtime._set_assisted_weld = lambda spec, *, active: runtime.events.append(
            ("weld", spec.tag_id, active)
        )

        class FakeArm:
            def __init__(self, ip=None, *, runtime):
                del ip
                self.runtime = runtime
                runtime.events.append(("arm", "init"))

            def motion_enable(self, enable=True):
                self.runtime.events.append(("arm", "motion_enable", enable))
                return 0

            def set_mode(self, mode):
                self.runtime.events.append(("arm", "mode", mode))
                return 0

            def set_state(self, state=0):
                self.runtime.events.append(("arm", "state", state))
                return 0

            def set_gripper_enable(self, enable=True):
                self.runtime.events.append(("arm", "gripper_enable", enable))
                return 0

            def set_gripper_speed(self, speed):
                self.runtime.events.append(("arm", "gripper_speed", speed))
                return 0

            def set_gripper_position(self, position, *, wait=True):
                self.runtime.events.append(("gripper", position, wait))
                return 0

        start = np.zeros(7)
        approach = RadialJointStagePlan(
            "pickup camera align",
            (start, start + 0.1),
            None,
        )
        lift = RadialJointStagePlan(
            "pickup lift",
            (start + 0.1, start + 0.2),
            "tag_1",
        )
        retreat = RadialJointStagePlan(
            "retreat",
            (start + 0.2, start + 0.3),
            None,
        )
        plan = RadialMotionPlan(
            tag_id="tag_1",
            target_xy=np.array((0.10, 0.20)),
            target_rotation_deg=0.0,
            planned_target_xy=np.array((0.10, 0.20)),
            source_outer=False,
            target_outer=False,
            portal_radius_m=0.473,
            outer_carry_z_m=0.416,
            actions=(
                RadialGripperAction("open gripper", GRIPPER_OPEN),
                RadialJointStageAction(approach),
                RadialGripperAction("close while stationary", GRIPPER_CLOSED),
                RadialSettleAction("secure physical grasp", 0.20),
                RadialValidateGraspAction("pickup"),
                RadialAssistedWeldAction("activate assisted weld", True),
                RadialSettleAction("", 0.08),
                RadialJointStageAction(
                    lift,
                    attached_tag="tag_1",
                    validate_grasp_after="pickup lift",
                ),
                RadialGripperAction("open and release", GRIPPER_OPEN),
                RadialAssistedWeldAction("deactivate assisted weld", False),
                RadialSettleAction("", 0.25),
                RadialClearCollisionAction("post-release"),
                RadialJointStageAction(retreat),
                RadialRebaseAction("radial observation home"),
            ),
        )

        original_xarm = runtime_module.XArmAPI
        runtime_module.XArmAPI = FakeArm
        try:
            runtime._execute_radial_motion_plan(plan)
        finally:
            runtime_module.XArmAPI = original_xarm

        self.assertEqual(
            runtime.stage_trace,
            [
                "open gripper",
                "pickup camera align",
                "close while stationary",
                "secure physical grasp",
                "activate assisted weld",
                "pickup lift",
                "open and release",
                "deactivate assisted weld",
                "retreat",
            ],
        )
        self.assertIn(("validate", "tag_1", "pickup"), runtime.events)
        self.assertIn(("validate", "tag_1", "pickup lift"), runtime.events)
        pickup_validate_index = runtime.events.index(
            ("validate", "tag_1", "pickup")
        )
        weld_on_index = runtime.events.index(("weld", "tag_1", True))
        weld_settle_index = runtime.events.index(("settle", 0.08))
        lift_index = next(
            index
            for index, event in enumerate(runtime.events)
            if event[:2] == ("joint", "pickup lift")
        )
        release_index = max(
            index
            for index, event in enumerate(runtime.events)
            if event == ("gripper", GRIPPER_OPEN, True)
        )
        weld_off_index = runtime.events.index(("weld", "tag_1", False))
        self.assertLess(
            pickup_validate_index,
            weld_on_index,
        )
        self.assertLess(weld_on_index, weld_settle_index)
        self.assertLess(weld_settle_index, lift_index)
        self.assertLess(lift_index, release_index)
        self.assertLess(release_index, weld_off_index)
        self.assertIn(("rebase", "radial observation home"), runtime.events)
        self.assertIn(
            (
                "joint",
                "pickup lift",
                "tag_1",
                ("attached", "tag_1"),
                True,
                None,
                2,
            ),
            runtime.events,
        )
        self.assertIn(
            ("joint", "retreat", None, None, True, None, 2),
            runtime.events,
        )
        self.assertEqual(runtime.allowed_collision_tag, None)

    def test_assisted_weld_updates_equality_and_forwards_model(self):
        runtime = MuJoCoRobotRuntime.__new__(MuJoCoRobotRuntime)
        runtime.model = SimpleNamespace(
            equality=lambda name: SimpleNamespace(id=1, name=name)
        )
        runtime.data = SimpleNamespace(eq_active=np.zeros(3, dtype=np.uint8))
        runtime._log = mock.Mock()
        runtime._runtime_snapshot = lambda: {"ok": True}
        runtime._align_assisted_weld_site_to_tcp = mock.Mock()
        runtime._reset_assisted_weld_site = mock.Mock()
        spec = SimpleNamespace(tag_id="tag_1", weld_name="assisted_grasp_tag_1")

        with mock.patch.object(runtime_module.mujoco, "mj_forward") as forward:
            runtime._set_assisted_weld(spec, active=True)
            self.assertEqual(int(runtime.data.eq_active[1]), 1)
            runtime._align_assisted_weld_site_to_tcp.assert_called_once_with(spec)
            runtime._reset_assisted_weld_site.assert_not_called()
            forward.assert_called_once_with(runtime.model, runtime.data)

            forward.reset_mock()
            runtime._set_assisted_weld(spec, active=False)
            self.assertEqual(int(runtime.data.eq_active[1]), 0)
            runtime._reset_assisted_weld_site.assert_called_once_with(spec)
            forward.assert_called_once_with(runtime.model, runtime.data)

    def test_assisted_weld_activation_preserves_measured_component_pose(self):
        model = runtime_module.mujoco.MjModel.from_xml_string(
            """
            <mujoco model="no-snap assisted weld test">
              <option gravity="0 0 0" timestep="0.002"/>
              <worldbody>
                <body name="tcp_body" pos="0.10 -0.20 0.40"
                  quat="0.9238795 0 0 0.3826834">
                  <site name="link_tcp" pos="0.03 0.01 0.02"
                    quat="0.9659258 0.2588190 0 0"/>
                </body>
                <body name="component" pos="0.18 -0.24 0.32"
                  quat="0.8660254 0 0 0.5">
                  <freejoint name="component_joint"/>
                  <geom type="box" size="0.02 0.03 0.04" mass="0.2"/>
                  <site name="grasp_site" pos="0 0 0.04" quat="0 0 1 0"/>
                </body>
              </worldbody>
              <equality>
                <weld name="assisted_grasp" site1="link_tcp" site2="grasp_site"
                  active="false" solref="0.002 1"
                  solimp="0.99 0.999 0.0001"/>
              </equality>
            </mujoco>
            """
        )
        data = runtime_module.mujoco.MjData(model)
        runtime_module.mujoco.mj_forward(model, data)
        runtime = MuJoCoRobotRuntime.__new__(MuJoCoRobotRuntime)
        runtime.model = model
        runtime.data = data
        runtime._log = mock.Mock()
        runtime._runtime_snapshot = lambda: {"ok": True}
        spec = SimpleNamespace(
            tag_id="tag_1",
            body_name="component",
            site_name="grasp_site",
            weld_name="assisted_grasp",
            grasp_site_local_z_m=0.04,
        )
        position_before = data.body("component").xpos.copy()
        rotation_before = data.body("component").xmat.copy()

        runtime._set_assisted_weld(spec, active=True)
        np.testing.assert_allclose(
            data.site("grasp_site").xpos,
            data.site("link_tcp").xpos,
            atol=1e-10,
        )
        np.testing.assert_allclose(
            data.site("grasp_site").xmat,
            data.site("link_tcp").xmat,
            atol=1e-7,
        )
        for _ in range(100):
            runtime_module.mujoco.mj_step(model, data)

        np.testing.assert_allclose(
            data.body("component").xpos,
            position_before,
            atol=1e-9,
        )
        np.testing.assert_allclose(
            data.body("component").xmat,
            rotation_before,
            atol=1e-8,
        )

        runtime._set_assisted_weld(spec, active=False)
        site_id = model.site("grasp_site").id
        np.testing.assert_allclose(model.site_pos[site_id], (0.0, 0.0, 0.04))
        np.testing.assert_allclose(model.site_quat[site_id], (0.0, 0.0, 1.0, 0.0))

    def test_executor_releases_assisted_weld_after_failure(self):
        runtime = MuJoCoRobotRuntime.__new__(MuJoCoRobotRuntime)
        spec = SimpleNamespace(tag_id="tag_1", weld_name="assisted_grasp_tag_1")
        runtime.scene = SimpleNamespace(components={"tag_1": spec})
        runtime.stage_trace = []
        runtime.allowed_collision_tag = None
        runtime._runtime_snapshot = lambda: {"ok": True}
        runtime._log = mock.Mock()
        weld_changes = []
        runtime._set_assisted_weld = lambda item, *, active: weld_changes.append(
            (item.tag_id, active)
        )

        plan = RadialMotionPlan(
            tag_id="tag_1",
            target_xy=np.array((0.10, 0.20)),
            target_rotation_deg=0.0,
            planned_target_xy=np.array((0.10, 0.20)),
            source_outer=False,
            target_outer=False,
            portal_radius_m=0.473,
            outer_carry_z_m=0.416,
            actions=(
                RadialAssistedWeldAction("activate assisted weld", True),
                object(),
            ),
        )

        with mock.patch.object(runtime_module, "XArmAPI"):
            with self.assertRaisesRegex(SimulatorError, "unknown radial plan action"):
                runtime._execute_radial_motion_plan(plan)

        self.assertEqual(
            weld_changes,
            [("tag_1", True), ("tag_1", False)],
        )
        runtime._log.assert_called_once_with(
            "assisted_weld_emergency_release",
            tag_id="tag_1",
            state={"ok": True},
        )

    def test_assisted_weld_environment_setting_is_strict_and_defaults_on(self):
        variable = runtime_module.ASSISTED_WELD_ENV_VAR
        with mock.patch.dict(runtime_module.os.environ, {}, clear=True):
            self.assertTrue(runtime_module._env_bool(variable, True))

        for value in ("1", "true", "YES", "on"):
            with self.subTest(value=value):
                with mock.patch.dict(
                    runtime_module.os.environ,
                    {variable: value},
                    clear=True,
                ):
                    self.assertTrue(runtime_module._env_bool(variable, True))

        for value in ("0", "false", "NO", "off"):
            with self.subTest(value=value):
                with mock.patch.dict(
                    runtime_module.os.environ,
                    {variable: value},
                    clear=True,
                ):
                    self.assertFalse(runtime_module._env_bool(variable, True))

        with mock.patch.dict(
            runtime_module.os.environ,
            {variable: "sometimes"},
            clear=True,
        ):
            with self.assertRaisesRegex(SimulatorError, variable):
                runtime_module._env_bool(variable, True)

    def test_radial_assisted_weld_actions_follow_runtime_setting(self):
        runtime = MuJoCoRobotRuntime.__new__(MuJoCoRobotRuntime)

        runtime.assisted_weld_enabled = True
        enabled_pickup = runtime._radial_assisted_weld_actions(active=True)
        enabled_release = runtime._radial_assisted_weld_actions(active=False)
        self.assertEqual(
            enabled_pickup,
            (
                RadialAssistedWeldAction("activate assisted weld", True),
                RadialSettleAction("", 0.08),
            ),
        )
        self.assertEqual(
            enabled_release,
            (RadialAssistedWeldAction("deactivate assisted weld", False),),
        )

        runtime.assisted_weld_enabled = False
        self.assertEqual(
            runtime._radial_assisted_weld_actions(active=True),
            (),
        )
        self.assertEqual(
            runtime._radial_assisted_weld_actions(active=False),
            (),
        )


if __name__ == "__main__":
    unittest.main()
