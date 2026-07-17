#!/usr/bin/env python3
"""HTTP bridge from cloud-labs/MuJoCo to ROS 2 MoveIt.

Run this inside WSL after launching the xArm7 MoveIt fake stack:

    source /opt/ros/jazzy/setup.bash
    source ~/dev_ws/install/setup.bash
    python3 cloud-labs/tools/moveit_sidecar/moveit_http_sidecar.py

The Windows MuJoCo runtime calls this process over localhost and receives
MoveIt joint trajectories without importing ROS packages on Windows.
"""

from __future__ import annotations

import argparse
import json
import os
import threading
import traceback
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Mapping, Sequence

import rclpy
from geometry_msgs.msg import Pose
from moveit_msgs.action import MoveGroup
from moveit_msgs.msg import (
    AttachedCollisionObject,
    CollisionObject,
    Constraints,
    JointConstraint,
    MotionPlanRequest,
    OrientationConstraint,
    PlanningScene,
    PlanningSceneComponents,
    PositionConstraint,
    RobotState,
)
from moveit_msgs.srv import ApplyPlanningScene, GetPlanningScene
from rclpy.action import ActionClient
from rclpy.node import Node
from sensor_msgs.msg import JointState
from shape_msgs.msg import SolidPrimitive


MOVEIT_SUCCESS = 1
SIDECAR_PROTOCOL_VERSION = 3
MOVEIT_ERROR_CODE_NAMES = {
    1: "SUCCESS",
    99999: "FAILURE",
    -1: "PLANNING_FAILED",
    -2: "INVALID_MOTION_PLAN",
    -3: "MOTION_PLAN_INVALIDATED_BY_ENVIRONMENT_CHANGE",
    -4: "CONTROL_FAILED",
    -5: "UNABLE_TO_ACQUIRE_SENSOR_DATA",
    -6: "TIMED_OUT",
    -7: "PREEMPTED",
    -10: "START_STATE_IN_COLLISION",
    -11: "START_STATE_VIOLATES_PATH_CONSTRAINTS",
    -12: "GOAL_IN_COLLISION",
    -13: "GOAL_VIOLATES_PATH_CONSTRAINTS",
    -14: "GOAL_CONSTRAINTS_VIOLATED",
    -15: "INVALID_GROUP_NAME",
    -16: "INVALID_GOAL_CONSTRAINTS",
    -17: "INVALID_ROBOT_STATE",
    -18: "INVALID_LINK_NAME",
    -19: "INVALID_OBJECT_NAME",
    -21: "FRAME_TRANSFORM_FAILURE",
    -22: "COLLISION_CHECKING_UNAVAILABLE",
    -23: "ROBOT_STATE_STALE",
    -24: "SENSOR_INFO_STALE",
    -25: "COMMUNICATION_FAILURE",
    -31: "NO_IK_SOLUTION",
}
DEFAULT_TOUCH_LINKS = (
    "link_tcp",
    "link_eef",
    "xarm_gripper_base_link",
    "left_outer_knuckle",
    "left_inner_knuckle",
    "left_finger",
    "right_outer_knuckle",
    "right_inner_knuckle",
    "right_finger",
)


def _is_cloudlab_collision_object_id(object_id: str) -> bool:
    object_id = str(object_id)
    return (
        object_id == "cloudlab_tabletop"
        or object_id.startswith("component_")
        or object_id.startswith("lab_frame_")
    )


def _float_list(values: Sequence[Any], *, length: int, label: str) -> list[float]:
    if not isinstance(values, Sequence) or len(values) != length:
        raise ValueError(f"{label} must contain {length} values")
    return [float(value) for value in values]


def _pose_from_payload(payload: Mapping[str, Any]) -> Pose:
    position = _float_list(payload.get("position", []), length=3, label="position")
    orientation = _float_list(
        payload.get("orientation_xyzw", []),
        length=4,
        label="orientation_xyzw",
    )
    pose = Pose()
    pose.position.x, pose.position.y, pose.position.z = position
    pose.orientation.x, pose.orientation.y, pose.orientation.z, pose.orientation.w = (
        orientation
    )
    return pose


def _collision_object_from_payload(payload: Mapping[str, Any]) -> CollisionObject:
    obj = CollisionObject()
    obj.id = str(payload["id"])
    obj.header.frame_id = str(payload.get("frame_id") or "world")
    obj.operation = CollisionObject.ADD
    primitive = SolidPrimitive()
    object_type = str(payload.get("type") or "box").lower()
    if object_type != "box":
        raise ValueError(f"unsupported collision object type {object_type!r}")
    primitive.type = SolidPrimitive.BOX
    primitive.dimensions = _float_list(
        payload.get("dimensions", []),
        length=3,
        label=f"{obj.id}.dimensions",
    )
    obj.primitives.append(primitive)
    obj.primitive_poses.append(_pose_from_payload(payload.get("pose") or {}))
    return obj


def _attached_object_from_payload(payload: Mapping[str, Any]) -> AttachedCollisionObject:
    attached = AttachedCollisionObject()
    attached.link_name = str(payload.get("link_name") or "link_tcp")
    touch_links = list(DEFAULT_TOUCH_LINKS)
    touch_links.extend(str(link) for link in payload.get("touch_links") or [])
    attached.touch_links = list(dict.fromkeys(touch_links))
    obj_payload = {
        **dict(payload),
        "frame_id": attached.link_name,
    }
    attached.object = _collision_object_from_payload(obj_payload)
    return attached


def _error_code_name(code: int) -> str:
    return MOVEIT_ERROR_CODE_NAMES.get(int(code), f"UNKNOWN_{int(code)}")


def _failure_hint(code: int) -> str:
    hints = {
        99999: (
            "MoveIt returned generic FAILURE. Inspect the request summary, "
            "the ROS terminal output, and whether the goal is reachable with "
            "the attached object and planning-scene collisions."
        ),
        -1: "MoveIt could not find a valid motion plan.",
        -10: "The supplied start joint state is in collision.",
        -12: "The goal state is in collision.",
        -13: "The goal violates path constraints.",
        -14: "The goal constraints were not satisfied.",
        -31: "No inverse-kinematics solution was found for the target pose.",
    }
    return hints.get(int(code), "MoveIt did not provide a more specific hint.")


def _request_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    world_objects = list(payload.get("world_objects") or [])
    attached_objects = list(payload.get("attached_objects") or [])
    return {
        "request_id": payload.get("request_id"),
        "group_name": payload.get("group_name") or "xarm7",
        "pose_link": payload.get("pose_link") or "link_tcp",
        "frame_id": payload.get("frame_id") or "world",
        "target_pose": payload.get("target_pose"),
        "joint_names": payload.get("joint_names") or [],
        "start_joints": payload.get("start_joints") or [],
        "world_object_ids": [item.get("id") for item in world_objects],
        "attached_object_ids": [item.get("id") for item in attached_objects],
        "known_collision_object_ids": payload.get("known_collision_object_ids") or [],
        "attached_touch_links": {
            str(item.get("id")): list(item.get("touch_links") or [])
            for item in attached_objects
        },
        "planning_attempts": payload.get("planning_attempts") or 8,
        "allowed_planning_time_s": payload.get("allowed_planning_time_s") or 5.0,
        "position_tolerance_m": payload.get("position_tolerance_m") or 0.004,
        "orientation_tolerance_rad": payload.get("orientation_tolerance_rad")
        or 0.05,
        "joint7_continuity_tolerance_rad": payload.get(
            "joint7_continuity_tolerance_rad"
        )
        or 0.0,
        "path_orientation_constraint": payload.get("path_orientation_constraint"),
        "path_joint_constraints": payload.get("path_joint_constraints") or [],
    }


def _orientation_constraint(
    *,
    frame_id: str,
    link_name: str,
    orientation_xyzw: Sequence[Any],
    absolute_x_axis_tolerance: float,
    absolute_y_axis_tolerance: float,
    absolute_z_axis_tolerance: float,
    weight: float = 1.0,
) -> OrientationConstraint:
    constraint = OrientationConstraint()
    constraint.header.frame_id = frame_id
    constraint.link_name = link_name
    orientation = _float_list(
        orientation_xyzw,
        length=4,
        label=f"{link_name}.orientation_xyzw",
    )
    (
        constraint.orientation.x,
        constraint.orientation.y,
        constraint.orientation.z,
        constraint.orientation.w,
    ) = orientation
    constraint.absolute_x_axis_tolerance = float(absolute_x_axis_tolerance)
    constraint.absolute_y_axis_tolerance = float(absolute_y_axis_tolerance)
    constraint.absolute_z_axis_tolerance = float(absolute_z_axis_tolerance)
    constraint.weight = float(weight)
    return constraint


def _joint_constraint_from_payload(payload: Mapping[str, Any]) -> JointConstraint:
    constraint = JointConstraint()
    constraint.joint_name = str(payload["joint_name"])
    constraint.position = float(payload["position"])
    constraint.tolerance_above = float(payload.get("tolerance_above") or 0.0)
    constraint.tolerance_below = float(payload.get("tolerance_below") or 0.0)
    constraint.weight = float(payload.get("weight") or 1.0)
    return constraint


class MoveItActionPlanner:
    def __init__(self, *, action_name: str | None = None) -> None:
        rclpy.init(args=None)
        self.node = Node("cloud_labs_moveit_http_sidecar")
        self._lock = threading.RLock()
        self._known_world_ids: set[str] = set()
        self._known_attached_ids: set[str] = set()
        self._known_attached_links: dict[str, str] = {}
        self._last_scene_update_summary: dict[str, Any] = {}
        self.action_name = action_name or os.getenv("CLOUDLAB_MOVEIT_ACTION") or "/move_action"
        self.action_client = ActionClient(self.node, MoveGroup, self.action_name)
        self.scene_client = self.node.create_client(
            ApplyPlanningScene,
            "/apply_planning_scene",
        )
        self.scene_query_client = self.node.create_client(
            GetPlanningScene,
            "/get_planning_scene",
        )

    def close(self) -> None:
        self.node.destroy_node()
        rclpy.shutdown()

    def health(self) -> dict[str, Any]:
        ready = False
        health_error = None
        try:
            ready = self.action_client.wait_for_server(timeout_sec=0.1)
        except Exception as exc:  # noqa: BLE001
            health_error = f"{type(exc).__name__}: {exc}"
        return {
            "ok": True,
            "sidecar_protocol_version": SIDECAR_PROTOCOL_VERSION,
            "action_name": self.action_name,
            "move_group_action_ready": ready,
            "health_error": health_error,
        }

    def _discover_move_group_action(self) -> str:
        return self.action_name

    def plan_pose(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._apply_scene(payload)
            goal = self._build_goal(payload)
            timeout_s = float(payload.get("action_timeout_s") or 30.0)
            if not self.action_client.wait_for_server(timeout_sec=timeout_s):
                raise RuntimeError(
                    f"MoveGroup action server unavailable at {self.action_name}"
                )
            send_future = self.action_client.send_goal_async(goal)
            rclpy.spin_until_future_complete(
                self.node,
                send_future,
                timeout_sec=timeout_s,
            )
            goal_handle = send_future.result()
            if goal_handle is None or not goal_handle.accepted:
                raise RuntimeError("MoveGroup rejected planning goal")
            result_future = goal_handle.get_result_async()
            rclpy.spin_until_future_complete(
                self.node,
                result_future,
                timeout_sec=timeout_s,
            )
            result_wrapper = result_future.result()
            if result_wrapper is None:
                raise RuntimeError("MoveGroup returned no result")
            result = result_wrapper.result
            code = int(result.error_code.val)
            code_name = _error_code_name(code)
            if code != MOVEIT_SUCCESS:
                return {
                    "success": False,
                    "sidecar_protocol_version": SIDECAR_PROTOCOL_VERSION,
                    "error": f"MoveIt error code {code} ({code_name})",
                    "error_code": code,
                    "error_code_name": code_name,
                    "failure_hint": _failure_hint(code),
                    "action_status": int(result_wrapper.status),
                    "request_summary": _request_summary(payload),
                    "scene_update_summary": dict(self._last_scene_update_summary),
                }
            trajectory = result.planned_trajectory.joint_trajectory
            return {
                "success": True,
                "sidecar_protocol_version": SIDECAR_PROTOCOL_VERSION,
                "error_code": code,
                "error_code_name": code_name,
                "action_status": int(result_wrapper.status),
                "planning_time_s": float(getattr(result, "planning_time", 0.0)),
                "trajectory_point_count": len(trajectory.points),
                "request_summary": _request_summary(payload),
                "scene_update_summary": dict(self._last_scene_update_summary),
                "joint_trajectory": self._serialize_joint_trajectory(trajectory),
            }

    def _apply_scene(self, payload: Mapping[str, Any]) -> None:
        world_objects = [
            _collision_object_from_payload(item)
            for item in payload.get("world_objects") or []
        ]
        attached_objects = [
            _attached_object_from_payload(item)
            for item in payload.get("attached_objects") or []
        ]
        next_world_ids = {obj.id for obj in world_objects}
        next_attached_ids = {obj.object.id for obj in attached_objects}
        overlapping_ids = next_world_ids & next_attached_ids
        if overlapping_ids:
            raise ValueError(
                "collision object cannot be both world and attached: "
                + ", ".join(sorted(overlapping_ids))
            )
        scoped_ids = {
            str(object_id)
            for object_id in payload.get("known_collision_object_ids") or []
            if str(object_id)
        }
        existing_world_ids = self._query_world_ids(scoped_ids)
        existing_attached_links = self._query_attached_links(scoped_ids)
        world_ids_to_remove = existing_world_ids - next_world_ids
        attached_ids_to_remove = set(existing_attached_links) - next_attached_ids
        attached_link_lookup = {
            **existing_attached_links,
            **self._known_attached_links,
        }
        self._last_scene_update_summary = {
            "known_world_ids": sorted(self._known_world_ids),
            "known_attached_ids": sorted(self._known_attached_ids),
            "scoped_ids": sorted(scoped_ids),
            "next_world_ids": sorted(next_world_ids),
            "next_attached_ids": sorted(next_attached_ids),
            "world_ids_to_remove": sorted(world_ids_to_remove),
            "attached_ids_to_remove": sorted(attached_ids_to_remove),
            "existing_world_ids": sorted(existing_world_ids),
            "existing_attached_links": dict(sorted(existing_attached_links.items())),
            "phased_remove_then_add": bool(
                world_ids_to_remove or attached_ids_to_remove
            ),
        }

        removal_scene = PlanningScene()
        removal_scene.is_diff = True
        removal_scene.robot_state.is_diff = True
        for old_id in sorted(world_ids_to_remove):
            obj = CollisionObject()
            obj.id = old_id
            obj.header.frame_id = str(payload.get("frame_id") or "world")
            obj.operation = CollisionObject.REMOVE
            removal_scene.world.collision_objects.append(obj)
        for old_id in sorted(attached_ids_to_remove):
            attached = AttachedCollisionObject()
            attached.link_name = attached_link_lookup.get(old_id, "link_tcp")
            attached.object.id = old_id
            attached.object.operation = CollisionObject.REMOVE
            removal_scene.robot_state.attached_collision_objects.append(attached)

        if (
            removal_scene.world.collision_objects
            or removal_scene.robot_state.attached_collision_objects
        ):
            self._apply_scene_diff(removal_scene)

        scene = PlanningScene()
        scene.is_diff = True
        scene.world.collision_objects.extend(world_objects)
        scene.robot_state.attached_collision_objects.extend(attached_objects)
        scene.robot_state.is_diff = True

        self._apply_scene_diff(scene)
        self._known_world_ids = next_world_ids
        self._known_attached_ids = next_attached_ids
        self._known_attached_links = {
            obj.object.id: obj.link_name
            for obj in attached_objects
        }

    def _apply_scene_diff(self, scene: PlanningScene) -> None:
        if not self.scene_client.wait_for_service(timeout_sec=5.0):
            raise RuntimeError("/apply_planning_scene service unavailable")
        future = self.scene_client.call_async(
            ApplyPlanningScene.Request(scene=scene)
        )
        rclpy.spin_until_future_complete(self.node, future, timeout_sec=10.0)
        response = future.result()
        if response is None or not response.success:
            raise RuntimeError("MoveIt failed to apply planning scene")

    def _query_attached_links(self, scoped_ids: set[str]) -> dict[str, str]:
        if not scoped_ids:
            return {}
        if not self.scene_query_client.wait_for_service(timeout_sec=1.0):
            return {}

        request = GetPlanningScene.Request()
        request.components.components = int(
            getattr(PlanningSceneComponents, "ROBOT_STATE_ATTACHED_OBJECTS", 4)
        )
        future = self.scene_query_client.call_async(request)
        rclpy.spin_until_future_complete(self.node, future, timeout_sec=2.0)
        response = future.result()
        if response is None:
            return {}

        attached_objects = (
            response.scene.robot_state.attached_collision_objects
            if response.scene is not None
            else []
        )
        return {
            obj.object.id: obj.link_name
            for obj in attached_objects
            if obj.object.id in scoped_ids
            or _is_cloudlab_collision_object_id(obj.object.id)
        }

    def _query_world_ids(self, scoped_ids: set[str]) -> set[str]:
        if not scoped_ids:
            return set()
        if not self.scene_query_client.wait_for_service(timeout_sec=1.0):
            return set()

        request = GetPlanningScene.Request()
        request.components.components = int(
            getattr(PlanningSceneComponents, "WORLD_OBJECT_NAMES", 8)
            | getattr(PlanningSceneComponents, "WORLD_OBJECT_GEOMETRY", 16)
        )
        future = self.scene_query_client.call_async(request)
        rclpy.spin_until_future_complete(self.node, future, timeout_sec=2.0)
        response = future.result()
        if response is None:
            return set()

        world_objects = (
            response.scene.world.collision_objects
            if response.scene is not None
            else []
        )
        return {
            obj.id
            for obj in world_objects
            if obj.id in scoped_ids or _is_cloudlab_collision_object_id(obj.id)
        }

    def _build_goal(self, payload: Mapping[str, Any]) -> MoveGroup.Goal:
        joint_names = [str(name) for name in payload.get("joint_names") or []]
        start_joints = _float_list(
            payload.get("start_joints") or [],
            length=len(joint_names),
            label="start_joints",
        )
        pose = _pose_from_payload(payload.get("target_pose") or {})
        frame_id = str(payload.get("frame_id") or "world")
        pose_link = str(payload.get("pose_link") or "link_tcp")

        request = MotionPlanRequest()
        request.group_name = str(payload.get("group_name") or "xarm7")
        request.num_planning_attempts = int(payload.get("planning_attempts") or 8)
        request.allowed_planning_time = float(
            payload.get("allowed_planning_time_s") or 5.0
        )
        request.max_velocity_scaling_factor = float(
            payload.get("max_velocity_scaling_factor") or 0.35
        )
        request.max_acceleration_scaling_factor = float(
            payload.get("max_acceleration_scaling_factor") or 0.35
        )
        if payload.get("planner_id"):
            request.planner_id = str(payload["planner_id"])
        if payload.get("pipeline_id"):
            request.pipeline_id = str(payload["pipeline_id"])

        request.start_state = RobotState()
        # MuJoCo is the source of truth for the arm state. Supplying this as a
        # complete state avoids MoveIt trying to merge against stale/missing
        # fake-controller joint_states before the first plan.
        request.start_state.is_diff = False
        request.start_state.joint_state = JointState()
        request.start_state.joint_state.name = joint_names
        request.start_state.joint_state.position = start_joints
        request.start_state.attached_collision_objects.extend(
            _attached_object_from_payload(item)
            for item in payload.get("attached_objects") or []
        )

        tolerance_m = float(payload.get("position_tolerance_m") or 0.004)
        orientation_tolerance_rad = float(
            payload.get("orientation_tolerance_rad") or 0.05
        )

        position_box = SolidPrimitive()
        position_box.type = SolidPrimitive.BOX
        position_box.dimensions = [tolerance_m, tolerance_m, tolerance_m]

        position_constraint = PositionConstraint()
        position_constraint.header.frame_id = frame_id
        position_constraint.link_name = pose_link
        position_constraint.constraint_region.primitives.append(position_box)
        position_constraint.constraint_region.primitive_poses.append(pose)
        position_constraint.weight = 1.0

        orientation_constraint = _orientation_constraint(
            frame_id=frame_id,
            link_name=pose_link,
            orientation_xyzw=[
                pose.orientation.x,
                pose.orientation.y,
                pose.orientation.z,
                pose.orientation.w,
            ],
            absolute_x_axis_tolerance=orientation_tolerance_rad,
            absolute_y_axis_tolerance=orientation_tolerance_rad,
            absolute_z_axis_tolerance=orientation_tolerance_rad,
            weight=1.0,
        )

        constraints = Constraints()
        constraints.name = "cloud_labs_pose_goal"
        constraints.position_constraints.append(position_constraint)
        constraints.orientation_constraints.append(orientation_constraint)
        joint7_tolerance = float(payload.get("joint7_continuity_tolerance_rad") or 0.0)
        if joint7_tolerance > 0.0 and "joint7" in joint_names:
            joint7_constraint = JointConstraint()
            joint7_constraint.joint_name = "joint7"
            joint7_constraint.position = float(start_joints[joint_names.index("joint7")])
            joint7_constraint.tolerance_above = joint7_tolerance
            joint7_constraint.tolerance_below = joint7_tolerance
            joint7_constraint.weight = 0.6
            constraints.joint_constraints.append(joint7_constraint)
        request.goal_constraints.append(constraints)

        path_constraints = Constraints()
        path_constraints.name = "cloud_labs_path_constraints"
        path_orientation = payload.get("path_orientation_constraint")
        if isinstance(path_orientation, Mapping):
            path_constraints.orientation_constraints.append(
                _orientation_constraint(
                    frame_id=str(path_orientation.get("frame_id") or frame_id),
                    link_name=str(path_orientation.get("link_name") or pose_link),
                    orientation_xyzw=path_orientation.get("orientation_xyzw") or [],
                    absolute_x_axis_tolerance=float(
                        path_orientation.get("absolute_x_axis_tolerance")
                        if path_orientation.get("absolute_x_axis_tolerance") is not None
                        else orientation_tolerance_rad
                    ),
                    absolute_y_axis_tolerance=float(
                        path_orientation.get("absolute_y_axis_tolerance")
                        if path_orientation.get("absolute_y_axis_tolerance") is not None
                        else orientation_tolerance_rad
                    ),
                    absolute_z_axis_tolerance=float(
                        path_orientation.get("absolute_z_axis_tolerance")
                        if path_orientation.get("absolute_z_axis_tolerance") is not None
                        else orientation_tolerance_rad
                    ),
                    weight=float(path_orientation.get("weight") or 1.0),
                )
            )
        for item in payload.get("path_joint_constraints") or []:
            if not isinstance(item, Mapping):
                raise ValueError("path_joint_constraints entries must be objects")
            path_constraints.joint_constraints.append(
                _joint_constraint_from_payload(item)
            )
        if (
            path_constraints.orientation_constraints
            or path_constraints.joint_constraints
        ):
            request.path_constraints = path_constraints

        goal = MoveGroup.Goal()
        goal.request = request
        goal.planning_options.plan_only = True
        goal.planning_options.look_around = False
        goal.planning_options.replan = False
        return goal

    def _serialize_joint_trajectory(self, trajectory: Any) -> dict[str, Any]:
        return {
            "joint_names": list(trajectory.joint_names),
            "points": [
                {
                    "positions": [float(value) for value in point.positions],
                    "velocities": [float(value) for value in point.velocities],
                    "accelerations": [
                        float(value) for value in point.accelerations
                    ],
                    "time_from_start_s": (
                        float(point.time_from_start.sec)
                        + float(point.time_from_start.nanosec) / 1e9
                    ),
                }
                for point in trajectory.points
            ],
        }


class MoveItHttpHandler(BaseHTTPRequestHandler):
    planner: MoveItActionPlanner

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        self.server.planner.node.get_logger().info(format % args)

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/health":
            self._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)
            return
        self._send_json(self.server.planner.health())

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/plan_pose":
            self._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)
            return
        payload: Mapping[str, Any] | None = None
        try:
            length = int(self.headers.get("Content-Length") or "0")
            parsed = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            payload = parsed
            if not isinstance(payload, Mapping):
                raise ValueError("request body must be a JSON object")
            result = self.server.planner.plan_pose(payload)
            self._send_json(result)
        except Exception as exc:  # noqa: BLE001
            summary = _request_summary(payload) if isinstance(payload, Mapping) else None
            self._send_json(
                {
                    "success": False,
                    "sidecar_protocol_version": SIDECAR_PROTOCOL_VERSION,
                    "error": str(exc),
                    "exception_type": type(exc).__name__,
                    "traceback": traceback.format_exc(),
                    "request_summary": summary,
                    "scene_update_summary": dict(
                        getattr(self.server.planner, "_last_scene_update_summary", {})
                    ),
                },
                status=HTTPStatus.BAD_REQUEST,
            )

    def _send_json(self, payload: Mapping[str, Any], *, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class PlannerHTTPServer(ThreadingHTTPServer):
    planner: MoveItActionPlanner


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.getenv("CLOUDLAB_MOVEIT_HOST", "127.0.0.1"))
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("CLOUDLAB_MOVEIT_PORT", "8765")),
    )
    parser.add_argument("--action-name", default=os.getenv("CLOUDLAB_MOVEIT_ACTION"))
    args = parser.parse_args()

    planner = MoveItActionPlanner(action_name=args.action_name)
    server = PlannerHTTPServer((args.host, args.port), MoveItHttpHandler)
    server.planner = planner
    print(
        f"[cloud-labs MoveIt sidecar] listening on http://{args.host}:{args.port} "
        f"using action {planner.action_name}",
        flush=True,
    )
    try:
        server.serve_forever()
    finally:
        server.server_close()
        planner.close()


if __name__ == "__main__":
    main()
