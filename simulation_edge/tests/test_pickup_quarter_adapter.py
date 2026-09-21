import unittest
from types import SimpleNamespace

import numpy as np

from simulation_edge.host.runtime import (
    CollisionPlanError,
    RADIAL_PHYSICAL_HOME_JOINTS_DEG,
    REAL_PICKUP_CAMERA_TO_GRIPPER_OFFSET_M,
    REAL_PICKUP_CANONICAL_RIGHT_CAMERA_YAW_DEG,
    RETURN_HOME_JOINT_TOLERANCE_RAD,
    MuJoCoRobotRuntime,
    _canonical_equivalent_angle_rad,
    _manual_motion_workspace_allows_xy,
    _pickup_quarter_frame,
    _short_edge_grasp_yaw,
    _radial_joint_path_sample_count,
    _radial_joint_path_travel_rad,
    _radial_joint_targets_equivalent,
    _rotate_xy_deg,
)


class PickupQuarterAdapterTests(unittest.TestCase):
    def test_pickup_quarters_are_selected_by_diagonal_triangles(self):
        self.assertEqual(_pickup_quarter_frame(np.array((2.0, 1.0))).name, "right")
        self.assertEqual(_pickup_quarter_frame(np.array((-1.0, 2.0))).name, "top")
        self.assertEqual(_pickup_quarter_frame(np.array((-2.0, -1.0))).name, "left")
        self.assertEqual(_pickup_quarter_frame(np.array((1.0, -2.0))).name, "bottom")

    def test_pickup_quarter_frames_are_exact_quarter_turns(self):
        expected = {
            "right": 0.0,
            "top": 90.0,
            "left": 180.0,
            "bottom": -90.0,
        }
        samples = ((2.0, 1.0), (-1.0, 2.0), (-2.0, -1.0), (1.0, -2.0))
        for sample in samples:
            frame = _pickup_quarter_frame(np.asarray(sample))
            self.assertEqual(frame.rotation_deg, expected[frame.name])

    def test_adapter_uses_quarter_frame_inside_old_300_mm_threshold(self):
        runtime = MuJoCoRobotRuntime.__new__(MuJoCoRobotRuntime)
        runtime.home_tcp_rotation = np.eye(3)
        samples = ((0.2, 0.1), (-0.1, 0.2), (-0.2, -0.1), (0.1, -0.2))
        for sample in samples:
            source_xy = np.asarray(sample)
            adapter = runtime._real_pickup_adapter_targets(
                source_xy,
                runtime._target_rotation(0.0),
                grasp_z=0.300,
            )
            self.assertTrue(adapter.quarter_oriented)
            self.assertEqual(
                adapter.workspace_quarter,
                _pickup_quarter_frame(source_xy).name,
            )
            expected = {
                "right": -25.6,
                "top": 64.4,
                "left": 154.4,
                "bottom": -115.6,
            }
            self.assertAlmostEqual(
                adapter.camera_yaw_deg,
                expected[adapter.workspace_quarter],
            )

    def test_storage_pickup_uses_fixed_bottom_left_camera_approach(self):
        runtime = MuJoCoRobotRuntime.__new__(MuJoCoRobotRuntime)
        runtime.home_tcp_rotation = np.eye(3)
        adapter = runtime._real_pickup_adapter_targets(
            np.array((-0.316, -0.316)),
            runtime._target_rotation(0.0),
            grasp_z=0.300,
            grasp_policy="short_edges",
            pickup_context="storage",
        )
        self.assertEqual(adapter.workspace_quarter, "storage_bottom_left")
        self.assertAlmostEqual(adapter.camera_yaw_deg, -160.6)
        self.assertTrue(adapter.base_first)
        self.assertEqual(abs(adapter.grasp_yaw_offset_deg), 90.0)

    def test_storage_pickup_base_align_uses_shortest_joint1_branch(self):
        class FakeModel:
            jnt_limited = np.ones(7, dtype=bool)
            jnt_range = np.tile(np.array((-2.0 * np.pi, 2.0 * np.pi)), (7, 1))

            @staticmethod
            def joint(name):
                return SimpleNamespace(id=int(name.removeprefix("joint")) - 1)

        runtime = MuJoCoRobotRuntime.__new__(MuJoCoRobotRuntime)
        runtime.model = FakeModel()
        runtime._log = lambda *args, **kwargs: None

        current = np.zeros(7)
        current[0] = np.deg2rad(-180.0)
        aligned = runtime._storage_pickup_base_aligned_joints(
            current,
            np.deg2rad(90.0),
        )

        self.assertAlmostEqual(np.rad2deg(aligned[0]), -270.0)
        self.assertAlmostEqual(np.rad2deg(aligned[0] - current[0]), -90.0)
        np.testing.assert_allclose(aligned[1:], current[1:])

    def test_inner_table_pickup_flips_camera_approach_outside_radial_minimum(self):
        runtime = MuJoCoRobotRuntime.__new__(MuJoCoRobotRuntime)
        runtime.home_tcp_rotation = np.eye(3)
        source_xy = np.array((-0.1275887, 0.0875656))
        adapter = runtime._real_pickup_adapter_targets(
            source_xy,
            runtime._target_rotation(0.0),
            grasp_z=0.300,
        )

        self.assertEqual(adapter.workspace_quarter, "left")
        self.assertTrue(adapter.radial_inner_flip)
        self.assertAlmostEqual(adapter.camera_yaw_deg, -25.6)
        self.assertGreaterEqual(np.linalg.norm(adapter.camera_xy), 0.134)
        self.assertGreaterEqual(np.linalg.norm(adapter.aligned_camera_xy), 0.134)
        np.testing.assert_allclose(adapter.gripper_xy, source_xy, atol=1e-12)

    def test_gripper_side_of_camera_rotates_with_workspace_quarter(self):
        expected_gripper_directions = {
            "right": np.array((-1.0, 0.0)),
            "top": np.array((0.0, -1.0)),
            "left": np.array((1.0, 0.0)),
            "bottom": np.array((0.0, 1.0)),
        }
        samples = ((2.0, 1.0), (-1.0, 2.0), (-2.0, -1.0), (1.0, -2.0))
        for sample in samples:
            frame = _pickup_quarter_frame(np.asarray(sample))
            camera_to_gripper = _rotate_xy_deg(
                REAL_PICKUP_CAMERA_TO_GRIPPER_OFFSET_M,
                REAL_PICKUP_CANONICAL_RIGHT_CAMERA_YAW_DEG
                + frame.rotation_deg,
            )
            gripper_from_camera = -camera_to_gripper
            direction = gripper_from_camera / np.linalg.norm(gripper_from_camera)
            np.testing.assert_allclose(
                direction,
                expected_gripper_directions[frame.name],
                atol=0.015,
            )

    def test_short_edge_grasp_closes_along_long_dimension(self):
        yaw, offset = _short_edge_grasp_yaw(0.0, 154.4)
        self.assertEqual(yaw, 90.0)
        self.assertEqual(offset, 90.0)

        yaw, offset = _short_edge_grasp_yaw(0.0, -115.6)
        self.assertEqual(yaw, -90.0)
        self.assertEqual(offset, -90.0)

        yaw, offset = _short_edge_grasp_yaw(45.0, 20.0)
        self.assertEqual(yaw, -45.0)
        self.assertEqual(offset, -90.0)

    def test_manual_workspace_only_trims_two_wall_corners(self):
        self.assertTrue(
            _manual_motion_workspace_allows_xy(
                np.array((-350.0, -190.0)),
                330.0,
            )
        )
        self.assertTrue(
            _manual_motion_workspace_allows_xy(
                np.array((330.0, 330.0)),
                330.0,
            )
        )
        self.assertFalse(
            _manual_motion_workspace_allows_xy(
                np.array((340.0, 340.0)),
                330.0,
            )
        )

    def test_radial_path_sampling_has_three_degree_max_step(self):
        start = np.zeros(7)
        self.assertEqual(_radial_joint_path_sample_count(start, start), 3)

        end = start.copy()
        end[0] = np.deg2rad(12.0)
        self.assertEqual(_radial_joint_path_sample_count(start, end), 5)

        end[0] = np.deg2rad(180.0)
        self.assertEqual(_radial_joint_path_sample_count(start, end), 24)

    def test_wrapped_wrist_angle_rebases_to_canonical_equivalent(self):
        rebased = _canonical_equivalent_angle_rad(
            np.deg2rad(-330.5),
            lower=-2.0 * np.pi,
            upper=2.0 * np.pi,
        )
        self.assertAlmostEqual(np.rad2deg(rebased), 29.5)

    def test_home_endpoint_accepts_only_periodic_base_and_wrist_branches(self):
        canonical = np.zeros(7)
        periodic = canonical.copy()
        periodic[0] += 2.0 * np.pi
        periodic[6] -= 2.0 * np.pi
        periodic[3] = RETURN_HOME_JOINT_TOLERANCE_RAD * 0.5
        self.assertTrue(
            _radial_joint_targets_equivalent(
                periodic,
                canonical,
                atol=RETURN_HOME_JOINT_TOLERANCE_RAD,
            )
        )

        periodic[3] = RETURN_HOME_JOINT_TOLERANCE_RAD * 2.0
        self.assertFalse(
            _radial_joint_targets_equivalent(
                periodic,
                canonical,
                atol=RETURN_HOME_JOINT_TOLERANCE_RAD,
            )
        )

    def test_radial_wrist_travel_guard_rejects_long_branch(self):
        runtime = MuJoCoRobotRuntime.__new__(MuJoCoRobotRuntime)
        start = np.zeros(7)
        long_end = start.copy()
        long_end[6] = np.deg2rad(306.9)
        self.assertAlmostEqual(
            np.rad2deg(_radial_joint_path_travel_rad((start, long_end), 6)),
            306.9,
        )
        with self.assertRaisesRegex(CollisionPlanError, "306.9 deg"):
            runtime._validate_radial_wrist_travel(
                "radial coordinated rotate",
                (start, long_end),
            )

        short_end = start.copy()
        short_end[6] = np.deg2rad(-53.1)
        runtime._validate_radial_wrist_travel(
            "radial coordinated rotate",
            (start, short_end),
        )

    def test_exact_wrapped_move_uses_short_wrist_branch_after_rebase(self):
        class FakeModel:
            jnt_limited = np.ones(7, dtype=bool)
            jnt_range = np.tile(np.array((-2.0 * np.pi, 2.0 * np.pi)), (7, 1))

            @staticmethod
            def joint(name):
                return SimpleNamespace(id=int(name.removeprefix("joint")) - 1)

        runtime = MuJoCoRobotRuntime.__new__(MuJoCoRobotRuntime)
        runtime.model = FakeModel()
        runtime.home_tcp_rotation = np.eye(3)
        runtime._validate_radial_pose_target = lambda *args, **kwargs: None
        runtime._load_radial_motion_library = lambda: SimpleNamespace(carry_z_m=0.550)
        runtime._log = lambda *args, **kwargs: None

        current = np.zeros(7)
        current[0] = np.deg2rad(31.86907858262687)
        current[6] = _canonical_equivalent_angle_rad(np.deg2rad(-330.5))
        plan = runtime._plan_radial_coordinated_rotation(
            name="radial coordinated rotate",
            tag_id="tag_21",
            radius_m=0.330,
            source_theta=np.deg2rad(31.86907858262687),
            target_theta=np.deg2rad(-22.67134362198085),
            source_rotation=runtime._target_rotation(2.3795485650758503),
            target_rotation=runtime._target_rotation(0.0),
            current=current,
        )
        self.assertIsNotNone(plan)
        wrist_travel_deg = np.rad2deg(
            _radial_joint_path_travel_rad(plan.waypoints, 6)
        )
        self.assertLess(wrist_travel_deg, 60.0)
        self.assertAlmostEqual(wrist_travel_deg, 52.1609, places=3)

    def test_half_turn_home_route_preserves_short_physical_home_wrist_move(self):
        class FakeModel:
            jnt_limited = np.ones(7, dtype=bool)
            jnt_range = np.tile(np.array((-2.0 * np.pi, 2.0 * np.pi)), (7, 1))

            @staticmethod
            def joint(name):
                return SimpleNamespace(id=int(name.removeprefix("joint")) - 1)

        runtime = MuJoCoRobotRuntime.__new__(MuJoCoRobotRuntime)
        runtime.model = FakeModel()
        runtime.home_tcp_rotation = np.eye(3)
        runtime._validate_radial_pose_target = lambda *args, **kwargs: None
        runtime._load_radial_motion_library = lambda: SimpleNamespace(carry_z_m=0.550)
        runtime._log = lambda *args, **kwargs: None

        current = np.zeros(7)
        current[0] = -np.pi
        current[6] = -np.pi
        physical_home = np.radians(
            np.asarray(RADIAL_PHYSICAL_HOME_JOINTS_DEG, dtype=float)
        )
        plan = runtime._plan_radial_coordinated_rotation(
            name="home coordinated rotate",
            tag_id=None,
            radius_m=0.193,
            source_theta=np.pi,
            target_theta=0.0,
            source_rotation=runtime._target_rotation(0.0),
            target_rotation=runtime._target_rotation(0.0),
            current=current,
            successor_target=physical_home,
        )

        self.assertIsNotNone(plan)
        endpoint = plan.waypoints[-1]
        self.assertAlmostEqual(np.rad2deg(endpoint[0]), 0.0, places=6)
        self.assertAlmostEqual(np.rad2deg(endpoint[6]), 0.0, places=6)
        nearest_home = runtime._nearest_equivalent_joints(physical_home, endpoint)
        self.assertAlmostEqual(
            abs(np.rad2deg(nearest_home[6] - endpoint[6])),
            60.0,
            places=6,
        )

    def test_cord_limited_joint1_uses_equivalent_long_way_route(self):
        class FakeModel:
            jnt_limited = np.ones(7, dtype=bool)
            jnt_range = np.tile(np.array((-2.0 * np.pi, 2.0 * np.pi)), (7, 1))
            jnt_range[0] = np.deg2rad((-10.0, 359.9))

            @staticmethod
            def joint(name):
                return SimpleNamespace(id=int(name.removeprefix("joint")) - 1)

        runtime = MuJoCoRobotRuntime.__new__(MuJoCoRobotRuntime)
        runtime.model = FakeModel()
        runtime.home_tcp_rotation = np.eye(3)
        runtime._validate_radial_pose_target = lambda *args, **kwargs: None
        runtime._load_radial_motion_library = lambda: SimpleNamespace(carry_z_m=0.550)
        runtime._log = lambda *args, **kwargs: None

        current = np.zeros(7)
        current[0] = np.deg2rad(10.0)
        plan = runtime._plan_radial_coordinated_rotation(
            name="radial coordinated rotate",
            tag_id="tag_0",
            radius_m=0.330,
            source_theta=np.deg2rad(10.0),
            target_theta=np.deg2rad(-20.0),
            source_rotation=runtime._target_rotation(0.0),
            target_rotation=runtime._target_rotation(0.0),
            current=current,
        )

        self.assertIsNotNone(plan)
        joint1_deg = np.rad2deg([waypoint[0] for waypoint in plan.waypoints])
        self.assertTrue(np.all(joint1_deg >= -10.0 - 1e-9))
        self.assertTrue(np.all(joint1_deg <= 359.9 + 1e-9))
        self.assertAlmostEqual(joint1_deg[0], 10.0)
        self.assertAlmostEqual(joint1_deg[-1], 340.0)
        self.assertGreater(joint1_deg[-1] - joint1_deg[0], 300.0)

    def test_cord_limited_ordinary_route_remains_the_short_route(self):
        class FakeModel:
            jnt_limited = np.ones(7, dtype=bool)
            jnt_range = np.tile(np.array((-2.0 * np.pi, 2.0 * np.pi)), (7, 1))
            jnt_range[0] = np.deg2rad((-10.0, 359.9))

            @staticmethod
            def joint(name):
                return SimpleNamespace(id=int(name.removeprefix("joint")) - 1)

        runtime = MuJoCoRobotRuntime.__new__(MuJoCoRobotRuntime)
        runtime.model = FakeModel()
        runtime.home_tcp_rotation = np.eye(3)
        runtime._validate_radial_pose_target = lambda *args, **kwargs: None
        runtime._load_radial_motion_library = lambda: SimpleNamespace(carry_z_m=0.550)
        runtime._log = lambda *args, **kwargs: None

        current = np.zeros(7)
        current[0] = np.deg2rad(10.0)
        plan = runtime._plan_radial_coordinated_rotation(
            name="radial coordinated rotate",
            tag_id="tag_0",
            radius_m=0.330,
            source_theta=np.deg2rad(10.0),
            target_theta=np.deg2rad(40.0),
            source_rotation=runtime._target_rotation(0.0),
            target_rotation=runtime._target_rotation(0.0),
            current=current,
        )

        self.assertIsNotNone(plan)
        joint1_deg = np.rad2deg([waypoint[0] for waypoint in plan.waypoints])
        self.assertAlmostEqual(joint1_deg[0], 10.0)
        self.assertAlmostEqual(joint1_deg[-1], 40.0)
        self.assertTrue(np.all(np.diff(joint1_deg) >= -1e-9))
        self.assertLessEqual(float(np.max(joint1_deg) - np.min(joint1_deg)), 30.0)

    def test_cord_limited_rebase_preserves_physical_joint1_representation(self):
        class FakeModel:
            jnt_limited = np.ones(7, dtype=bool)
            jnt_range = np.tile(np.array((-2.0 * np.pi, 2.0 * np.pi)), (7, 1))
            jnt_range[0] = np.deg2rad((-10.0, 359.9))

            @staticmethod
            def joint(name):
                return SimpleNamespace(id=int(name.removeprefix("joint")) - 1)

        runtime = MuJoCoRobotRuntime.__new__(MuJoCoRobotRuntime)
        runtime.model = FakeModel()
        runtime.data = SimpleNamespace(qpos=np.zeros(7), ctrl=np.zeros(7))
        runtime.current_joint_target = np.zeros(7)
        runtime.current_joint_target[0] = np.deg2rad(350.0)
        runtime.data.qpos[:] = runtime.current_joint_target
        runtime.data.ctrl[:] = runtime.current_joint_target
        runtime._radial_joint1_route_limits_overridden = True
        runtime._log = lambda *args, **kwargs: None

        runtime._rebase_radial_periodic_joint_branches(reason="test")

        self.assertAlmostEqual(
            np.rad2deg(runtime.current_joint_target[0]), 350.0)


if __name__ == "__main__":
    unittest.main()
