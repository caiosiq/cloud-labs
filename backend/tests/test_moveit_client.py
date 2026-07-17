from __future__ import annotations

import unittest

from lab_communicator.mujoco.moveit_client import (
    REQUIRED_SIDECAR_PROTOCOL_VERSION,
    MoveItPlannerClient,
    MoveItPlannerError,
)


class MoveItPlannerClientTests(unittest.TestCase):
    def test_plan_pose_sends_expected_payload(self):
        client = MoveItPlannerClient("http://moveit.test")
        captured = {}

        def fake_request(method, path, payload=None, **kwargs):
            del kwargs
            captured["method"] = method
            captured["path"] = path
            captured["payload"] = payload
            return {
                "success": True,
                "sidecar_protocol_version": REQUIRED_SIDECAR_PROTOCOL_VERSION,
                "joint_trajectory": {"points": []},
            }

        client._request = fake_request

        result = client.plan_pose(
            request_id="request-1",
            joint_names=("joint1",),
            start_joints=(0.0,),
            target_pose={
                "position": [0.0, 0.0, 0.1],
                "orientation_xyzw": [0.0, 0.0, 0.0, 1.0],
            },
            world_objects=(),
            attached_objects=(),
            known_collision_object_ids=("cloudlab_tabletop", "component_tag_19"),
            allowed_planning_time_s=3.0,
            planning_attempts=4,
            orientation_tolerance_rad=0.04,
            path_orientation_constraint={
                "frame_id": "world",
                "link_name": "link_tcp",
                "orientation_xyzw": [0.0, 0.0, 0.0, 1.0],
                "absolute_x_axis_tolerance": 0.1,
                "absolute_y_axis_tolerance": 0.1,
                "absolute_z_axis_tolerance": 3.14159,
            },
            path_joint_constraints=(
                {
                    "joint_name": "joint7",
                    "position": 0.0,
                    "tolerance_above": 1.0,
                    "tolerance_below": 1.0,
                    "weight": 1.0,
                },
            ),
            max_velocity_scaling_factor=0.6,
            max_acceleration_scaling_factor=0.7,
        )

        self.assertTrue(result["success"])
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["path"], "/plan_pose")
        self.assertEqual(
            captured["payload"]["client_protocol_version"],
            REQUIRED_SIDECAR_PROTOCOL_VERSION,
        )
        self.assertEqual(captured["payload"]["request_id"], "request-1")
        self.assertEqual(
            captured["payload"]["known_collision_object_ids"],
            ["cloudlab_tabletop", "component_tag_19"],
        )
        self.assertEqual(
            captured["payload"]["joint7_continuity_tolerance_rad"],
            0.0,
        )
        self.assertEqual(captured["payload"]["orientation_tolerance_rad"], 0.04)
        self.assertEqual(
            captured["payload"]["path_orientation_constraint"]["link_name"],
            "link_tcp",
        )
        self.assertEqual(
            captured["payload"]["path_joint_constraints"][0]["joint_name"],
            "joint7",
        )
        self.assertEqual(captured["payload"]["allowed_planning_time_s"], 3.0)
        self.assertEqual(captured["payload"]["planning_attempts"], 4)
        self.assertEqual(captured["payload"]["max_velocity_scaling_factor"], 0.6)
        self.assertEqual(captured["payload"]["max_acceleration_scaling_factor"], 0.7)
        self.assertNotIn("allowed_collision_pairs", captured["payload"])

    def test_failure_preserves_moveit_details(self):
        client = MoveItPlannerClient("http://moveit.test")

        def fake_request(*args, **kwargs):
            del args, kwargs
            return {
                "success": False,
                "sidecar_protocol_version": REQUIRED_SIDECAR_PROTOCOL_VERSION,
                "error": "MoveIt error code 99999 (FAILURE)",
                "error_code": 99999,
                "error_code_name": "FAILURE",
            }

        client._request = fake_request

        with self.assertRaises(MoveItPlannerError) as caught:
            client.plan_pose(
                request_id="request-1",
                joint_names=("joint1",),
                start_joints=(0.0,),
                target_pose={
                    "position": [0.0, 0.0, 0.1],
                    "orientation_xyzw": [0.0, 0.0, 0.0, 1.0],
                },
                world_objects=(),
                attached_objects=(),
            )

        self.assertEqual(caught.exception.details["error_code_name"], "FAILURE")

    def test_health_rejects_unversioned_sidecar(self):
        client = MoveItPlannerClient("http://moveit.test")
        client._request = lambda *args, **kwargs: {"ok": True}

        with self.assertRaisesRegex(MoveItPlannerError, "sidecar is stale"):
            client.health()


if __name__ == "__main__":
    unittest.main()
