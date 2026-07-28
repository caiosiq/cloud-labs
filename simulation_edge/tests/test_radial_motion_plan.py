import unittest
from types import SimpleNamespace

import numpy as np

import simulation_edge.host.runtime as runtime_module
from simulation_edge.host.runtime import (
    GRIPPER_CLOSED,
    GRIPPER_OPEN,
    MuJoCoRobotRuntime,
    RadialClearCollisionAction,
    RadialComponentObservation,
    RadialGripperAction,
    RadialJointStageAction,
    RadialJointStagePlan,
    RadialMotionPlan,
    RadialRebaseAction,
    RadialSettleAction,
    RadialValidateGraspAction,
    RadialWorldSnapshot,
    SimulatorError,
)


class RadialMotionPlanTests(unittest.TestCase):
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
                RadialJointStageAction(
                    lift,
                    attached_tag="tag_1",
                    validate_grasp_after="pickup lift",
                ),
                RadialGripperAction("open and release", GRIPPER_OPEN),
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
                "pickup lift",
                "open and release",
                "retreat",
            ],
        )
        self.assertIn(("validate", "tag_1", "pickup"), runtime.events)
        self.assertIn(("validate", "tag_1", "pickup lift"), runtime.events)
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


if __name__ == "__main__":
    unittest.main()
