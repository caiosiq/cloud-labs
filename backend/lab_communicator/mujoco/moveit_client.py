"""HTTP client for the WSL/ROS 2 MoveIt planning sidecar."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Mapping, Sequence


DEFAULT_MOVEIT_URL = "http://127.0.0.1:8765"
REQUIRED_SIDECAR_PROTOCOL_VERSION = 3


class MoveItPlannerError(RuntimeError):
    def __init__(self, message: str, *, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = dict(details or {})


def moveit_url_from_env() -> str:
    return (os.getenv("CLOUDLAB_MOVEIT_URL") or DEFAULT_MOVEIT_URL).rstrip("/")


class MoveItPlannerClient:
    """Small JSON-over-HTTP boundary to keep ROS 2 inside WSL/Linux."""

    def __init__(self, base_url: str | None = None, *, timeout_s: float = 45.0) -> None:
        self.base_url = (base_url or moveit_url_from_env()).rstrip("/")
        self.timeout_s = float(timeout_s)

    def health(self, *, timeout_s: float = 5.0) -> Mapping[str, Any]:
        result = self._request("GET", "/health", timeout_s=timeout_s)
        self._require_current_sidecar(result, context="health")
        return result

    def plan_pose(
        self,
        *,
        request_id: str,
        joint_names: Sequence[str],
        start_joints: Sequence[float],
        target_pose: Mapping[str, Any],
        world_objects: Sequence[Mapping[str, Any]],
        attached_objects: Sequence[Mapping[str, Any]] = (),
        known_collision_object_ids: Sequence[str] = (),
        group_name: str = "xarm7",
        pose_link: str = "link_tcp",
        frame_id: str = "world",
        allowed_planning_time_s: float = 5.0,
        planning_attempts: int = 8,
        orientation_tolerance_rad: float = 0.05,
        joint7_continuity_tolerance_rad: float = 0.0,
        path_orientation_constraint: Mapping[str, Any] | None = None,
        path_joint_constraints: Sequence[Mapping[str, Any]] = (),
        max_velocity_scaling_factor: float = 0.35,
        max_acceleration_scaling_factor: float = 0.35,
    ) -> Mapping[str, Any]:
        payload = {
            "client_protocol_version": REQUIRED_SIDECAR_PROTOCOL_VERSION,
            "request_id": request_id,
            "group_name": group_name,
            "pose_link": pose_link,
            "frame_id": frame_id,
            "joint_names": list(joint_names),
            "start_joints": [float(value) for value in start_joints],
            "target_pose": dict(target_pose),
            "world_objects": [dict(item) for item in world_objects],
            "attached_objects": [dict(item) for item in attached_objects],
            "known_collision_object_ids": [
                str(object_id) for object_id in known_collision_object_ids
            ],
            "allowed_planning_time_s": float(allowed_planning_time_s),
            "planning_attempts": int(planning_attempts),
            "orientation_tolerance_rad": float(orientation_tolerance_rad),
            "joint7_continuity_tolerance_rad": float(
                joint7_continuity_tolerance_rad
            ),
            "path_joint_constraints": [
                dict(item) for item in path_joint_constraints
            ],
            "max_velocity_scaling_factor": float(max_velocity_scaling_factor),
            "max_acceleration_scaling_factor": float(max_acceleration_scaling_factor),
        }
        if path_orientation_constraint is not None:
            payload["path_orientation_constraint"] = dict(path_orientation_constraint)
        result = self._request("POST", "/plan_pose", payload)
        self._require_current_sidecar(result, context="plan_pose")
        if not result.get("success"):
            message = result.get("error") or "MoveIt planner returned failure"
            raise MoveItPlannerError(str(message), details=result)
        return result

    def _require_current_sidecar(
        self,
        result: Mapping[str, Any],
        *,
        context: str,
    ) -> None:
        version = result.get("sidecar_protocol_version")
        try:
            parsed = int(version)
        except (TypeError, ValueError):
            parsed = 0
        if parsed >= REQUIRED_SIDECAR_PROTOCOL_VERSION:
            return
        raise MoveItPlannerError(
            (
                "MoveIt sidecar is stale or incompatible. Restart the WSL "
                "sidecar with the current cloud-labs file before using "
                f"MuJoCo: MoveIt. Required protocol "
                f"{REQUIRED_SIDECAR_PROTOCOL_VERSION}, got {version!r} "
                f"during {context}."
            ),
            details={
                "context": context,
                "required_sidecar_protocol_version": REQUIRED_SIDECAR_PROTOCOL_VERSION,
                "sidecar_protocol_version": version,
                "sidecar_response": dict(result),
            },
        )

    def _request(
        self,
        method: str,
        path: str,
        payload: Mapping[str, Any] | None = None,
        *,
        timeout_s: float | None = None,
    ) -> Mapping[str, Any]:
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=self.timeout_s if timeout_s is None else float(timeout_s),
            ) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            parsed_detail: Mapping[str, Any] | None = None
            try:
                parsed = json.loads(detail or "{}")
                if isinstance(parsed, Mapping):
                    parsed_detail = parsed
            except json.JSONDecodeError:
                parsed_detail = None
            raise MoveItPlannerError(
                f"MoveIt sidecar HTTP {exc.code}: {detail or exc.reason}",
                details=parsed_detail,
            ) from exc
        except OSError as exc:
            raise MoveItPlannerError(
                f"MoveIt sidecar unavailable at {self.base_url}: {exc}"
            ) from exc
        try:
            parsed = json.loads(body or "{}")
        except json.JSONDecodeError as exc:
            raise MoveItPlannerError(
                f"MoveIt sidecar returned non-JSON response: {body[:200]!r}"
            ) from exc
        if not isinstance(parsed, Mapping):
            raise MoveItPlannerError("MoveIt sidecar returned a non-object response")
        return parsed
