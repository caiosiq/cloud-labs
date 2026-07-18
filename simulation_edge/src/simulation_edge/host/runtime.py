"""Long-running MuJoCo runtime and process entrypoint."""

from __future__ import annotations

import json
import math
import os
import queue
import time
import traceback
from collections.abc import Mapping as AbcMapping
from collections.abc import Sequence as AbcSequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Sequence

import mujoco
import mujoco.viewer
import numpy as np

from simulation_edge.host.ik import ARM_DOF, DampedLeastSquaresIK, IKError
from simulation_edge.host.moveit_client import (
    MoveItPlannerClient,
    MoveItPlannerError,
)
from simulation_edge.host.scene import TABLE_SURFACE_Z_M, ComponentSpec, SceneSpec
from simulation_edge.sim_xarm.wrapper import XArmAPI


GRIPPER_OPEN = 0.0
GRIPPER_CLOSED = 255.0
CLEARANCE_Z_M = 0.48
APPROACH_CLEARANCE_M = 0.11
RELEASE_CLEARANCE_M = 0.006
TRAVEL_CLEARANCE_ABOVE_COMPONENT_TOP_M = 0.23
TRAVEL_CLEARANCE_ABOVE_GRASP_M = 0.25
MAX_TRAVEL_CLEARANCE_Z_M = 0.80
MOVEIT_CARRY_TRANSLATION_EPSILON_M = 0.005
MOVEIT_CARRY_UPRIGHT_TOLERANCE_DEG_ENV_VAR = (
    "CLOUDLAB_MOVEIT_CARRY_UPRIGHT_TOLERANCE_DEG"
)
MOVEIT_CARRY_UPRIGHT_TOLERANCE_DEG = 10.0
MOVEIT_CARRY_JOINT7_CONTINUITY_TOLERANCE_DEG_ENV_VAR = (
    "CLOUDLAB_MOVEIT_CARRY_JOINT7_CONTINUITY_TOLERANCE_DEG"
)
MOVEIT_CARRY_JOINT7_CONTINUITY_TOLERANCE_DEG = 135.0
MOVEIT_CARRY_YAW_STEP_DEG_ENV_VAR = "CLOUDLAB_MOVEIT_CARRY_YAW_STEP_DEG"
MOVEIT_CARRY_YAW_STEP_DEG = 30.0
MOVEIT_MAX_VELOCITY_SCALE_ENV_VAR = "CLOUDLAB_MOVEIT_MAX_VELOCITY_SCALE"
MOVEIT_MAX_ACCELERATION_SCALE_ENV_VAR = "CLOUDLAB_MOVEIT_MAX_ACCELERATION_SCALE"
MOVEIT_ALLOWED_PLANNING_TIME_ENV_VAR = "CLOUDLAB_MOVEIT_ALLOWED_PLANNING_TIME"
MOVEIT_PLANNING_ATTEMPTS_ENV_VAR = "CLOUDLAB_MOVEIT_PLANNING_ATTEMPTS"
MOVEIT_DEFAULT_VELOCITY_SCALE = 0.55
MOVEIT_DEFAULT_ACCELERATION_SCALE = 0.55
MOVEIT_DEFAULT_ALLOWED_PLANNING_TIME_S = 3.0
MOVEIT_DEFAULT_PLANNING_ATTEMPTS = 4
MOVEIT_PREPICK_FEASIBILITY_SEEDS_ENV_VAR = "CLOUDLAB_MOVEIT_PREPICK_FEASIBILITY_SEEDS"
MOVEIT_PREPICK_FEASIBILITY_TIMEOUT_ENV_VAR = "CLOUDLAB_MOVEIT_PREPICK_FEASIBILITY_TIMEOUT_S"
MOVEIT_PREPICK_FEASIBILITY_SEEDS = 4
MOVEIT_PREPICK_FEASIBILITY_MAX_SEEDS = 12
MOVEIT_PREPICK_FEASIBILITY_TIMEOUT_S = 60.0
MOVEIT_TABLE_COLLISION_HEIGHT_M = 0.06
MOVEIT_TABLE_COLLISION_SURFACE_MARGIN_M = 0.01
MOVEIT_INCLUDE_TABLE_ENV_VAR = "CLOUDLAB_MOVEIT_INCLUDE_TABLE"
MOVEIT_MANUAL_CARTESIAN_STEP_M = 0.025
MOVEIT_MANUAL_CORRECTION_TOLERANCE_M = 0.006
MOVEIT_TRAJECTORY_TIME_SCALE_ENV_VAR = "CLOUDLAB_MOVEIT_TRAJECTORY_TIME_SCALE"
MOVEIT_TRAJECTORY_TIME_SCALE = 1.0
MUJOCO_VIEWER_SYNC_HZ_ENV_VAR = "CLOUDLAB_MUJOCO_VIEWER_SYNC_HZ"
MUJOCO_VIEWER_SYNC_HZ = 60.0
ARM_SERVO_STIFFNESS_SCALE = 2.0
PLACEMENT_POSITION_TOLERANCE_MM = 5.0
MOVEIT_PLACEMENT_POSITION_TOLERANCE_MM = 8.0
PLACEMENT_YAW_TOLERANCE_DEG = 2.0
MOVEIT_JOINT_NAMES = tuple(f"joint{index}" for index in range(1, ARM_DOF + 1))
MOVEIT_JOINT_LIMIT_MARGIN_RAD = 0.010
MOVEIT_JOINT_LIMITS_RAD = (
    (-math.pi * 0.99, math.pi * 0.99),
    (-2.18, 2.18),
    (-math.pi * 0.99, math.pi * 0.99),
    (-0.11, math.pi * 0.99),
    (-math.pi * 0.99, math.pi * 0.99),
    (-1.75, math.pi * 0.99),
    (-math.pi * 0.99, math.pi * 0.99),
)
MOVEIT_COLLISION_AWARE_REST_JOINTS = (
    0.0,
    -0.6,
    0.0,
    1.2,
    0.0,
    1.4,
    0.0,
)
MUJOCO_PLANNER_CUSTOM_IK = "custom_ik"
MUJOCO_PLANNER_MOVEIT = "moveit"
LOG_ENV_VAR = "CLOUDLAB_MUJOCO_LOG_DIR"
MOVEIT_MANUAL_CARTESIAN_STAGES = {
    "descend open",
    "lower around box",
    "pickup clearance lift",
    "contact descend destination",
    "contact retreat",
}
RETURN_HOME_JOINT_TOLERANCE_RAD = 1e-4
RETURN_HOME_MIN_DURATION_S = 1.0
RETURN_HOME_MAX_DURATION_S = 5.0
RETURN_HOME_RAD_PER_S = 0.7
RETURN_HOME_MIN_PLAYBACK_DURATION_S = 0.75
GRIPPER_COMPONENT_TOUCH_BODIES = {
    "link_tcp",
    "link_eef",
    "xarm_gripper_base_link",
    "left_outer_knuckle",
    "left_inner_knuckle",
    "left_finger",
    "right_outer_knuckle",
    "right_inner_knuckle",
    "right_finger",
}


class SimulatorError(RuntimeError):
    pass


class ViewerClosedError(SimulatorError):
    pass


class CollisionPlanError(SimulatorError):
    pass


def _cloud_labs_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _diagnostic_log_root() -> Path:
    override = os.getenv(LOG_ENV_VAR)
    if override:
        return Path(override).expanduser().resolve()
    return _cloud_labs_root() / "logs" / "mujoco_sessions"


def _env_float(
    name: str,
    default: float,
    *,
    minimum: float,
    maximum: float,
) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return float(default)
    try:
        value = float(raw)
    except ValueError as exc:
        raise SimulatorError(f"{name} must be a numeric value") from exc
    return float(np.clip(value, minimum, maximum))


def _env_int(
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return int(default)
    try:
        value = int(raw)
    except ValueError as exc:
        raise SimulatorError(f"{name} must be an integer") from exc
    return int(max(minimum, min(value, maximum)))


def _json_safe(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return _json_safe(value.tolist())
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


class JsonlDiagnostics:
    def __init__(self, *, scene: SceneSpec, planner_backend: str) -> None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        root = _diagnostic_log_root()
        root.mkdir(parents=True, exist_ok=True)
        self.session_id = f"{timestamp}_{os.getpid()}"
        self.path = root / f"mujoco_{self.session_id}.jsonl"
        self.log(
            "session_start",
            planner_backend=planner_backend,
            profile=scene.profile_id,
            pid=os.getpid(),
            log_path=str(self.path),
        )

    def log(self, event: str, **payload: Any) -> None:
        row = {
            "timestamp": datetime.now().isoformat(timespec="milliseconds"),
            "event": event,
            **payload,
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(_json_safe(row), sort_keys=True) + "\n")


def _smoothstep(value: float) -> float:
    value = float(np.clip(value, 0.0, 1.0))
    return value * value * (3.0 - 2.0 * value)


def _rotation_z(yaw_rad: float) -> np.ndarray:
    c, s = math.cos(yaw_rad), math.sin(yaw_rad)
    return np.array(((c, -s, 0.0), (s, c, 0.0), (0.0, 0.0, 1.0)))


MOVEIT_TCP_TO_MUJOCO_TCP_ROTATION = _rotation_z(math.pi)


def _angle_error_deg(actual: float, target: float) -> float:
    return abs((float(actual) - float(target) + 180.0) % 360.0 - 180.0)


def _rotation_distance_deg(first: np.ndarray, second: np.ndarray) -> float:
    relative = np.asarray(first, dtype=float).T @ np.asarray(second, dtype=float)
    trace = float(np.trace(relative))
    angle = math.acos(float(np.clip((trace - 1.0) / 2.0, -1.0, 1.0)))
    return math.degrees(angle)


def _matrix_to_quat_xyzw(matrix: np.ndarray) -> list[float]:
    matrix = np.asarray(matrix, dtype=float)
    trace = float(np.trace(matrix))
    if trace > 0.0:
        scale = math.sqrt(trace + 1.0) * 2.0
        w = 0.25 * scale
        x = (matrix[2, 1] - matrix[1, 2]) / scale
        y = (matrix[0, 2] - matrix[2, 0]) / scale
        z = (matrix[1, 0] - matrix[0, 1]) / scale
    else:
        diagonal = np.diag(matrix)
        index = int(np.argmax(diagonal))
        if index == 0:
            scale = math.sqrt(1.0 + matrix[0, 0] - matrix[1, 1] - matrix[2, 2]) * 2.0
            w = (matrix[2, 1] - matrix[1, 2]) / scale
            x = 0.25 * scale
            y = (matrix[0, 1] + matrix[1, 0]) / scale
            z = (matrix[0, 2] + matrix[2, 0]) / scale
        elif index == 1:
            scale = math.sqrt(1.0 + matrix[1, 1] - matrix[0, 0] - matrix[2, 2]) * 2.0
            w = (matrix[0, 2] - matrix[2, 0]) / scale
            x = (matrix[0, 1] + matrix[1, 0]) / scale
            y = 0.25 * scale
            z = (matrix[1, 2] + matrix[2, 1]) / scale
        else:
            scale = math.sqrt(1.0 + matrix[2, 2] - matrix[0, 0] - matrix[1, 1]) * 2.0
            w = (matrix[1, 0] - matrix[0, 1]) / scale
            x = (matrix[0, 2] + matrix[2, 0]) / scale
            y = (matrix[1, 2] + matrix[2, 1]) / scale
            z = 0.25 * scale
    quat = np.asarray((x, y, z, w), dtype=float)
    quat /= max(float(np.linalg.norm(quat)), 1e-12)
    return quat.tolist()


def _matrix_to_quat_wxyz(matrix: np.ndarray) -> np.ndarray:
    x, y, z, w = _matrix_to_quat_xyzw(matrix)
    return np.array((w, x, y, z), dtype=float)


def _matrix_to_rotvec_deg(matrix: np.ndarray) -> np.ndarray:
    trace = float(np.trace(matrix))
    angle = math.acos(float(np.clip((trace - 1.0) / 2.0, -1.0, 1.0)))
    if angle < 1e-9:
        return np.zeros(3)
    if abs(math.pi - angle) < 1e-5:
        eigenvalues, eigenvectors = np.linalg.eigh((matrix + np.eye(3)) / 2.0)
        axis = eigenvectors[:, int(np.argmax(eigenvalues))]
    else:
        axis = np.array(
            (
                matrix[2, 1] - matrix[1, 2],
                matrix[0, 2] - matrix[2, 0],
                matrix[1, 0] - matrix[0, 1],
            )
        ) / (2.0 * math.sin(angle))
    axis /= max(float(np.linalg.norm(axis)), 1e-12)
    return np.degrees(axis * angle)


def _rotvec_deg_to_matrix(rotvec_deg: Sequence[float]) -> np.ndarray:
    vector = np.radians(np.asarray(rotvec_deg, dtype=float))
    angle = float(np.linalg.norm(vector))
    if angle < 1e-12:
        return np.eye(3)
    axis = vector / angle
    skew = np.array(
        (
            (0.0, -axis[2], axis[1]),
            (axis[2], 0.0, -axis[0]),
            (-axis[1], axis[0], 0.0),
        )
    )
    return np.eye(3) + math.sin(angle) * skew + (1.0 - math.cos(angle)) * (skew @ skew)


class _NullViewer:
    def __enter__(self) -> "_NullViewer":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def sync(self) -> None:
        return None

    def is_running(self) -> bool:
        return True


@dataclass(frozen=True)
class MoveResult:
    tag_id: str
    x_mm: float
    y_mm: float
    rotation_deg: float
    stage_trace: tuple[str, ...]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "tag_id": self.tag_id,
            "x_mm": self.x_mm,
            "y_mm": self.y_mm,
            "rotation_deg": self.rotation_deg,
            "stage_trace": list(self.stage_trace),
        }


@dataclass(frozen=True)
class MoveItStagePlan:
    name: str
    stage_request_id: str
    position_m: np.ndarray
    rotation: np.ndarray
    points: Sequence[Mapping[str, Any]]
    joint_names: tuple[str, ...]
    fallback_duration_s: float
    allowed_tag: Optional[str]
    final_joints: np.ndarray


@dataclass(frozen=True)
class ManualCartesianStagePlan:
    name: str
    position_m: np.ndarray
    rotation: np.ndarray
    joint_waypoints: tuple[np.ndarray, ...]
    durations_s: tuple[float, ...]
    speed_mm_s: float
    allowed_tag: Optional[str]
    gripper_position: float


@dataclass(frozen=True)
class MoveItCarryPlan:
    stage_name: str
    route_index: int
    stages: tuple[MoveItStagePlan, ...]
    post_carry_manual_stages: tuple[ManualCartesianStagePlan, ...] = ()


@dataclass(frozen=True)
class MoveItPrePickFeasibilityPlan:
    seed_index: int
    seed_total: int
    above_source: MoveItStagePlan
    carry: MoveItCarryPlan
    manual_pickup_stages: tuple[ManualCartesianStagePlan, ...] = ()


class MuJoCoRobotRuntime:
    """Owns one compiled model, physics state, viewer, and IK solver."""

    def __init__(
        self,
        scene: SceneSpec,
        *,
        show_viewer: bool,
        realtime: bool,
        planner_backend: str = MUJOCO_PLANNER_CUSTOM_IK,
        progress_callback: Callable[[Mapping[str, Any]], None] | None = None,
    ) -> None:
        self.scene = scene
        self.planner_backend = str(planner_backend or MUJOCO_PLANNER_CUSTOM_IK)
        self._progress_callback = progress_callback
        self.diagnostics = JsonlDiagnostics(
            scene=scene,
            planner_backend=self.planner_backend,
        )
        self.active_request_id: str | None = None
        if self.planner_backend not in {MUJOCO_PLANNER_CUSTOM_IK, MUJOCO_PLANNER_MOVEIT}:
            raise SimulatorError(f"unknown MuJoCo planner backend {planner_backend!r}")
        self._log(
            "runtime_init_start",
            show_viewer=bool(show_viewer),
            realtime=bool(realtime),
        )
        self.model = mujoco.MjModel.from_xml_string(scene.xml)
        self.model.actuator_gainprm[:ARM_DOF, 0] *= ARM_SERVO_STIFFNESS_SCALE
        self.model.actuator_biasprm[:ARM_DOF, 1] *= ARM_SERVO_STIFFNESS_SCALE
        self.model.actuator_biasprm[:ARM_DOF, 2] *= math.sqrt(
            ARM_SERVO_STIFFNESS_SCALE
        )
        self.model.actuator_forcerange[:ARM_DOF] *= ARM_SERVO_STIFFNESS_SCALE
        self.data = mujoco.MjData(self.model)
        # Resetting the whole composed model to the xArm keyframe also zeros
        # free-joint qpos values added by the generated scene, which teleports
        # every component to the origin. Keep the generated free-body poses
        # and copy only the seven arm joints from the Menagerie home keyframe.
        mujoco.mj_resetData(self.model, self.data)
        if self.model.nkey:
            self.data.qpos[:ARM_DOF] = self.model.key_qpos[0, :ARM_DOF]
        mujoco.mj_forward(self.model, self.data)
        self.home = self.data.qpos[:ARM_DOF].copy()
        self.home_tcp_rotation = self.data.site("link_tcp").xmat.reshape(3, 3).copy()
        initial_joint_target = self.home.copy()
        if self.planner_backend == MUJOCO_PLANNER_MOVEIT:
            initial_joint_target = np.asarray(
                MOVEIT_COLLISION_AWARE_REST_JOINTS,
                dtype=float,
            ).copy()
            DampedLeastSquaresIK._clip_joint_limits(self.model, initial_joint_target)
            self.data.qpos[:ARM_DOF] = initial_joint_target
            mujoco.mj_forward(self.model, self.data)
            self._log(
                "moveit_collision_aware_rest_selected",
                keyframe_home_joints=self.home.tolist(),
                rest_joints=initial_joint_target.tolist(),
            )
        self.current_joint_target = initial_joint_target.copy()
        self.ik = DampedLeastSquaresIK()
        self.moveit_tcp_offset_local_m = np.zeros(3, dtype=float)
        self._moveit_tcp_offset_auto_calibrated = False
        self._moveit_plan_progress_message_override: str | None = None
        self._moveit_plan_progress_prefix: str = ""
        self._load_moveit_tcp_offset()
        self._moveit_lab_yaw_by_tag = {
            tag_id: float(spec.yaw_deg)
            for tag_id, spec in self.scene.components.items()
        }
        self.moveit: MoveItPlannerClient | None = None
        if self.planner_backend == MUJOCO_PLANNER_MOVEIT:
            self.moveit = MoveItPlannerClient()
            health = self.moveit.health()
            self._log("moveit_health", response=health)
        self.realtime = bool(realtime)
        self.stage_trace: list[str] = []
        self.allowed_collision_tag: Optional[str] = None
        self._gripper_position = GRIPPER_OPEN
        self._viewer = (
            mujoco.viewer.launch_passive(self.model, self.data)
            if show_viewer
            else _NullViewer()
        )
        self._viewer_entered = self._viewer.__enter__()
        viewer_sync_hz = _env_float(
            MUJOCO_VIEWER_SYNC_HZ_ENV_VAR,
            MUJOCO_VIEWER_SYNC_HZ,
            minimum=1.0,
            maximum=240.0,
        )
        self._viewer_sync_interval_s = 1.0 / viewer_sync_hz
        self._last_viewer_sync_perf_s = 0.0
        self.data.ctrl[:ARM_DOF] = self.current_joint_target
        self.data.ctrl[ARM_DOF] = GRIPPER_OPEN
        self._component_body_ids = {
            tag: self.model.body(spec.body_name).id
            for tag, spec in self.scene.components.items()
        }
        self._component_geom_ids = {
            tag: self.model.geom(f"geom_{spec.body_name}").id
            for tag, spec in self.scene.components.items()
        }
        self._table_geom_id = self.model.geom("tabletop").id
        self._static_collision_geom_ids = {
            self.model.geom(obj.geom_name).id: obj.object_id
            for obj in self.scene.static_collision_objects
        }
        self._settle(0.25)
        self._log(
            "runtime_ready",
            home_joints=self.home.tolist(),
            initial_joint_target=self.current_joint_target.tolist(),
            initial_tcp_m=self.data.site("link_tcp").xpos.copy(),
            component_ids=sorted(self.scene.components),
        )

    def _log(self, event: str, **payload: Any) -> None:
        payload.setdefault("request_id", self.active_request_id)
        try:
            self.diagnostics.log(event, **payload)
        except Exception:
            pass

    def _emit_progress(self, message: str, **payload: Any) -> None:
        event = _json_safe({
            "type": "progress",
            "request_id": self.active_request_id,
            "message": str(message),
            "timestamp": datetime.now().isoformat(timespec="milliseconds"),
            **payload,
        })
        self._log("simulator_progress", **event)
        if self._progress_callback is None:
            return
        try:
            self._progress_callback(event)
        except Exception:
            pass

    def diagnostic_log_path(self) -> str:
        return str(self.diagnostics.path)

    def _load_moveit_tcp_offset(self) -> None:
        raw = os.getenv("CLOUDLAB_MOVEIT_TCP_OFFSET_M", "").strip()
        if raw:
            parts = [part.strip() for part in raw.replace(";", ",").split(",")]
            if len(parts) != 3:
                raise SimulatorError(
                    "CLOUDLAB_MOVEIT_TCP_OFFSET_M must be three comma-separated "
                    "meter values, for example: 0,0,0.118"
                )
            try:
                self.moveit_tcp_offset_local_m = np.asarray(
                    [float(part) for part in parts],
                    dtype=float,
                )
            except ValueError as exc:
                raise SimulatorError(
                    "CLOUDLAB_MOVEIT_TCP_OFFSET_M must contain numeric meter values"
                ) from exc
            self._moveit_tcp_offset_auto_calibrated = True
            return

        raw_z = os.getenv("CLOUDLAB_MOVEIT_TCP_Z_OFFSET_M", "").strip()
        if raw_z:
            try:
                self.moveit_tcp_offset_local_m[2] = float(raw_z)
            except ValueError as exc:
                raise SimulatorError(
                    "CLOUDLAB_MOVEIT_TCP_Z_OFFSET_M must be a numeric meter value"
                ) from exc
            self._moveit_tcp_offset_auto_calibrated = True

    def close(self) -> None:
        try:
            self._viewer.__exit__(None, None, None)
        except Exception:
            pass

    def viewer_running(self) -> bool:
        try:
            return bool(self._viewer_entered.is_running())
        except Exception:
            return False

    def _step(self) -> None:
        if not self.viewer_running():
            raise ViewerClosedError("MuJoCo viewer was closed")
        started = time.perf_counter()
        mujoco.mj_step(self.model, self.data)
        now = time.perf_counter()
        if now - self._last_viewer_sync_perf_s >= self._viewer_sync_interval_s:
            self._viewer_entered.sync()
            self._last_viewer_sync_perf_s = now
        if self.realtime:
            remaining = self.model.opt.timestep - (time.perf_counter() - started)
            if remaining > 0:
                time.sleep(remaining)

    def _settle(self, duration_s: float) -> None:
        duration_s = self._scaled_manual_duration_s(duration_s, minimum_s=0.0)
        start = float(self.data.time)
        while self.data.time - start < duration_s:
            self._step()

    def set_cartesian_pose(
        self,
        pose_mm_axis_angle_deg: Sequence[float],
        *,
        speed_mm_s: float,
    ) -> None:
        pose = np.asarray(pose_mm_axis_angle_deg, dtype=float)
        target_position = pose[:3] / 1000.0
        target_rotation = _rotvec_deg_to_matrix(pose[3:])
        target_joints = self.ik.solve(
            self.model,
            target_position,
            target_rotation,
            self.current_joint_target,
            self.home,
        )
        target_joints = self._moveit_safe_joints(target_joints)
        self._preflight_joint_path(
            self.current_joint_target,
            target_joints,
            allowed_tag=self.allowed_collision_tag,
        )
        current_tcp = self.data.site("link_tcp").xpos.copy()
        distance_m = float(np.linalg.norm(target_position - current_tcp))
        speed_m_s = max(float(speed_mm_s) / 1000.0, 0.04)
        duration_s = float(np.clip(distance_m / speed_m_s, 0.35, 2.8))
        self._execute_joint_target(target_joints, duration_s)
        for _ in range(2):
            mujoco.mj_forward(self.model, self.data)
            actual_position = self.data.site("link_tcp").xpos.copy()
            position_error = target_position - actual_position
            if float(np.linalg.norm(position_error)) < 0.0006:
                break
            compensated_position = target_position + position_error
            actual_joints = self.data.qpos[:ARM_DOF].copy()
            try:
                correction_joints = self.ik.solve(
                    self.model,
                    compensated_position,
                    target_rotation,
                    actual_joints,
                    self.home,
                )
                correction_joints = self._moveit_safe_joints(correction_joints)
            except IKError as exc:
                if (
                    self.planner_backend == MUJOCO_PLANNER_MOVEIT
                    and float(np.linalg.norm(position_error))
                    <= MOVEIT_MANUAL_CORRECTION_TOLERANCE_M
                ):
                    self._log(
                        "cartesian_correction_skipped",
                        error=str(exc),
                        target_position_m=target_position,
                        actual_position_m=actual_position,
                        position_error_m=position_error,
                    )
                    break
                raise
            self._preflight_joint_path(
                actual_joints,
                correction_joints,
                allowed_tag=self.allowed_collision_tag,
            )
            self._execute_joint_target(correction_joints, 0.35)

    def get_cartesian_pose(self) -> list[float]:
        mujoco.mj_forward(self.model, self.data)
        position_mm = self.data.site("link_tcp").xpos.copy() * 1000.0
        rotation = self.data.site("link_tcp").xmat.reshape(3, 3).copy()
        return [*position_mm.tolist(), *_matrix_to_rotvec_deg(rotation).tolist()]

    def set_gripper_position(self, position: float, *, speed: float) -> None:
        start_value = float(self._gripper_position)
        target = float(np.clip(position, GRIPPER_OPEN, GRIPPER_CLOSED))
        normalized_speed = max(float(speed), 1.0)
        duration_s = float(np.clip(abs(target - start_value) / normalized_speed, 0.25, 0.8))
        duration_s = self._scaled_manual_duration_s(duration_s, minimum_s=0.03)
        started = float(self.data.time)
        while self.data.time - started < duration_s:
            phase = _smoothstep((self.data.time - started) / duration_s)
            self.data.ctrl[:ARM_DOF] = self.current_joint_target
            self.data.ctrl[ARM_DOF] = start_value + phase * (target - start_value)
            self._step()
        self.data.ctrl[ARM_DOF] = target
        self._gripper_position = target

    def get_gripper_position(self) -> float:
        return float(self._gripper_position)

    def _component_snapshot(self) -> Dict[str, Any]:
        mujoco.mj_forward(self.model, self.data)
        out: Dict[str, Any] = {}
        for tag_id, spec in self.scene.components.items():
            body = self.data.body(spec.body_name)
            rotation = body.xmat.reshape(3, 3).copy()
            out[tag_id] = {
                "body": spec.body_name,
                "position_m": body.xpos.copy(),
                "yaw_deg": math.degrees(math.atan2(rotation[1, 0], rotation[0, 0])),
                "size_m": [spec.width_m, spec.depth_m, spec.height_m],
            }
        return out

    def _contact_snapshot(self, *, limit: int = 30) -> list[Dict[str, Any]]:
        contacts: list[Dict[str, Any]] = []
        for contact_index in range(min(int(self.data.ncon), limit)):
            contact = self.data.contact[contact_index]
            geom1 = int(contact.geom1)
            geom2 = int(contact.geom2)
            body1 = int(self.model.geom_bodyid[geom1])
            body2 = int(self.model.geom_bodyid[geom2])
            contacts.append(
                {
                    "geom1": self.model.geom(geom1).name,
                    "geom2": self.model.geom(geom2).name,
                    "body1": self.model.body(body1).name,
                    "body2": self.model.body(body2).name,
                    "distance_m": float(contact.dist),
                }
            )
        return contacts

    def _runtime_snapshot(self) -> Dict[str, Any]:
        mujoco.mj_forward(self.model, self.data)
        return {
            "time_s": float(self.data.time),
            "joint_qpos": self.data.qpos[:ARM_DOF].copy(),
            "joint_targets": self.current_joint_target.copy(),
            "gripper_position": float(self._gripper_position),
            "tcp_position_m": self.data.site("link_tcp").xpos.copy(),
            "tcp_rotation": self.data.site("link_tcp").xmat.reshape(3, 3).copy(),
            "components": self._component_snapshot(),
            "contacts": self._contact_snapshot(),
        }

    def _drive_joint_segment(
        self,
        start: np.ndarray,
        target: np.ndarray,
        duration_s: float,
        *,
        minimum_s: float = 0.001,
    ) -> None:
        duration_s = self._scaled_manual_duration_s(
            duration_s,
            minimum_s=minimum_s,
        )
        started = float(self.data.time)
        while self.data.time - started < duration_s:
            phase = _smoothstep((self.data.time - started) / duration_s)
            self.data.ctrl[:ARM_DOF] = start + phase * (target - start)
            self.data.ctrl[ARM_DOF] = self._gripper_position
            self._step()
        self.data.ctrl[:ARM_DOF] = target

    def _drive_joint_waypoints(
        self,
        waypoints: Sequence[np.ndarray],
        durations_s: Sequence[float],
    ) -> None:
        if len(waypoints) < 2:
            return
        if len(durations_s) != len(waypoints) - 1:
            raise SimulatorError("joint waypoint durations do not match path length")
        cumulative = [0.0]
        for duration in durations_s:
            cumulative.append(
                cumulative[-1]
                + self._scaled_manual_duration_s(duration, minimum_s=0.01)
            )
        total_duration = cumulative[-1]
        started = float(self.data.time)
        segment_index = 0
        while self.data.time - started < total_duration:
            elapsed = self.data.time - started
            while (
                segment_index < len(cumulative) - 2
                and elapsed > cumulative[segment_index + 1]
            ):
                segment_index += 1
            start_time = cumulative[segment_index]
            end_time = cumulative[segment_index + 1]
            phase = float(
                np.clip(
                    (elapsed - start_time) / max(end_time - start_time, 1e-6),
                    0.0,
                    1.0,
                )
            )
            self.data.ctrl[:ARM_DOF] = (
                waypoints[segment_index]
                + phase * (waypoints[segment_index + 1] - waypoints[segment_index])
            )
            self.data.ctrl[ARM_DOF] = self._gripper_position
            self._step()
        self.data.ctrl[:ARM_DOF] = waypoints[-1]

    def _execute_joint_target(self, target: np.ndarray, duration_s: float) -> None:
        previous = self.current_joint_target.copy()
        self._drive_joint_segment(previous, target, duration_s)
        self.current_joint_target = target.copy()
        self._settle(0.20)

    def _execute_cartesian_path(
        self,
        targets: Sequence[tuple[np.ndarray, np.ndarray]],
        *,
        speed_mm_s: float,
    ) -> None:
        joint_waypoints, durations_s = self._plan_cartesian_path(
            targets,
            speed_mm_s=speed_mm_s,
        )
        if len(joint_waypoints) < 2:
            return
        self._drive_joint_waypoints(joint_waypoints, durations_s)
        self.current_joint_target = joint_waypoints[-1].copy()
        self._settle(0.06)

    def _execute_cartesian_stage_plan(
        self,
        planned: ManualCartesianStagePlan,
        arm: XArmAPI | None = None,
    ) -> None:
        del arm
        self.stage_trace.append(planned.name)
        previous_allowed = self.allowed_collision_tag
        self.allowed_collision_tag = planned.allowed_tag
        self._gripper_position = float(planned.gripper_position)
        self.data.ctrl[ARM_DOF] = self._gripper_position
        self._emit_progress(
            f"Simulator executing cached {planned.name}.",
            phase="manual_execute",
            stage=planned.name,
        )
        self._log(
            "controller_stage_request",
            stage=planned.name,
            target_mujoco_tcp_m=np.asarray(planned.position_m, dtype=float),
            speed_mm_s=float(planned.speed_mm_s),
            cached_plan=True,
            state=self._runtime_snapshot(),
        )
        try:
            waypoints = [
                np.asarray(waypoint, dtype=float).copy()
                for waypoint in planned.joint_waypoints
            ]
            if len(waypoints) >= 2:
                waypoints[0] = self.current_joint_target.copy()
                self._drive_joint_waypoints(waypoints, planned.durations_s)
                self.current_joint_target = waypoints[-1].copy()
                self._settle(0.06)
        except Exception as exc:  # noqa: BLE001
            self._log(
                "controller_stage_failure",
                stage=planned.name,
                error=str(exc),
                traceback=traceback.format_exc(),
                cached_plan=True,
                state=self._runtime_snapshot(),
            )
            raise
        finally:
            self.allowed_collision_tag = previous_allowed
        self._log(
            "controller_stage_complete",
            stage=planned.name,
            cached_plan=True,
            state=self._runtime_snapshot(),
        )
        self._emit_progress(
            f"Simulator completed cached {planned.name}.",
            phase="manual_execute",
            stage=planned.name,
        )

    def _plan_cartesian_path(
        self,
        targets: Sequence[tuple[np.ndarray, np.ndarray]],
        *,
        speed_mm_s: float,
    ) -> tuple[list[np.ndarray], list[float]]:
        mujoco.mj_forward(self.model, self.data)
        seed = self.current_joint_target.copy()
        previous_position = self.data.site("link_tcp").xpos.copy()
        joint_waypoints = [seed.copy()]
        durations_s: list[float] = []
        if not targets:
            return joint_waypoints, durations_s
        speed_m_s = max(float(speed_mm_s) / 1000.0, 0.04)
        for position_m, rotation in targets:
            target_position = np.asarray(position_m, dtype=float)
            target_rotation = np.asarray(rotation, dtype=float)
            target_joints = self.ik.solve(
                self.model,
                target_position,
                target_rotation,
                seed,
                self.home,
            )
            target_joints = self._moveit_safe_joints(target_joints)
            self._preflight_joint_path(
                seed,
                target_joints,
                allowed_tag=self.allowed_collision_tag,
            )
            distance_m = float(np.linalg.norm(target_position - previous_position))
            durations_s.append(float(np.clip(distance_m / speed_m_s, 0.03, 0.35)))
            joint_waypoints.append(target_joints.copy())
            seed = target_joints
            previous_position = target_position
        return joint_waypoints, durations_s

    def _execute_moveit_trajectory(
        self,
        points: Sequence[Mapping[str, Any]],
        *,
        joint_names: Sequence[str],
        fallback_duration_s: float,
        allowed_tag: Optional[str] = None,
        minimum_s: float = 0.02,
    ) -> None:
        times, positions, velocities = self._moveit_trajectory_with_preflight(
            points,
            joint_names=joint_names,
            fallback_duration_s=fallback_duration_s,
            allowed_tag=allowed_tag,
        )
        trajectory_time_scale = self._moveit_trajectory_time_scale()
        total_time_s = max(float(times[-1]) * trajectory_time_scale, minimum_s)
        started = float(self.data.time)
        segment_index = 0
        while self.data.time - started < total_time_s:
            trajectory_time = (self.data.time - started) / trajectory_time_scale
            while (
                segment_index < len(times) - 2
                and trajectory_time > times[segment_index + 1]
            ):
                segment_index += 1
            start_time = float(times[segment_index])
            end_time = float(times[segment_index + 1])
            duration = max(end_time - start_time, 1e-6)
            phase = float(np.clip((trajectory_time - start_time) / duration, 0.0, 1.0))
            self.data.ctrl[:ARM_DOF] = self._interpolate_moveit_joint_target(
                positions[segment_index],
                positions[segment_index + 1],
                velocities[segment_index],
                velocities[segment_index + 1],
                duration,
                phase,
            )
            self.data.ctrl[ARM_DOF] = self._gripper_position
            self._step()
        self.data.ctrl[:ARM_DOF] = positions[-1]
        self.current_joint_target = positions[-1].copy()
        self._settle(0.20)

    def _moveit_trajectory_with_preflight(
        self,
        points: Sequence[Mapping[str, Any]],
        *,
        joint_names: Sequence[str],
        fallback_duration_s: float,
        allowed_tag: Optional[str],
    ) -> tuple[list[float], list[np.ndarray], list[np.ndarray | None]]:
        times, positions, velocities = self._moveit_trajectory_waypoints(
            points,
            joint_names=joint_names,
            start_joints=self.current_joint_target,
            fallback_duration_s=fallback_duration_s,
        )
        for start, end in zip(positions, positions[1:]):
            self._preflight_joint_path(start, end, allowed_tag=allowed_tag)
        return times, positions, velocities

    @staticmethod
    def _nearest_equivalent_joints(target: np.ndarray, reference: np.ndarray) -> np.ndarray:
        target = np.asarray(target, dtype=float).copy()
        reference = np.asarray(reference, dtype=float)
        return target - (2.0 * math.pi) * np.round((target - reference) / (2.0 * math.pi))

    @staticmethod
    def _moveit_limited_joints(joints: np.ndarray) -> np.ndarray:
        limited = np.asarray(joints, dtype=float).copy()
        for index, (lower, upper) in enumerate(MOVEIT_JOINT_LIMITS_RAD):
            limited[index] = float(
                np.clip(
                    limited[index],
                    lower + MOVEIT_JOINT_LIMIT_MARGIN_RAD,
                    upper - MOVEIT_JOINT_LIMIT_MARGIN_RAD,
                )
            )
        return limited

    def _moveit_safe_joints(self, joints: np.ndarray) -> np.ndarray:
        if self.planner_backend != MUJOCO_PLANNER_MOVEIT:
            return np.asarray(joints, dtype=float).copy()
        return self._moveit_limited_joints(joints)

    def _observation_home_joints(self) -> np.ndarray:
        if self.planner_backend == MUJOCO_PLANNER_MOVEIT:
            return self._moveit_limited_joints(
                np.asarray(MOVEIT_COLLISION_AWARE_REST_JOINTS, dtype=float)
            )
        return np.asarray(self.home, dtype=float).copy()

    def _tcp_pose_for_joint_target(self, joints: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        snapshot = self._motion_snapshot()
        try:
            joint_target = np.asarray(joints, dtype=float)
            self.data.qpos[:ARM_DOF] = joint_target
            self.data.ctrl[:ARM_DOF] = joint_target
            self.current_joint_target = joint_target.copy()
            mujoco.mj_forward(self.model, self.data)
            return (
                self.data.site("link_tcp").xpos.copy(),
                self.data.site("link_tcp").xmat.reshape(3, 3).copy(),
            )
        finally:
            self._restore_motion_snapshot(snapshot)

    def _return_to_observation_home(self) -> None:
        target_joints = self._observation_home_joints()
        start_joints = self.current_joint_target.copy()
        joint_delta = target_joints - start_joints
        max_delta = float(np.max(np.abs(joint_delta)))
        if max_delta <= RETURN_HOME_JOINT_TOLERANCE_RAD:
            self._log(
                "return_home_skipped",
                reason="already_at_observation_home",
                target_joints=target_joints,
                state=self._runtime_snapshot(),
            )
            return
        self.allowed_collision_tag = None
        try:
            self._preflight_joint_path(
                start_joints,
                target_joints,
                allowed_tag=None,
            )
        except CollisionPlanError as exc:
            if self.planner_backend == MUJOCO_PLANNER_MOVEIT and self.moveit is not None:
                self._log(
                    "return_home_joint_preflight_rejected",
                    error=str(exc),
                    start_joints=start_joints,
                    target_joints=target_joints,
                    state=self._runtime_snapshot(),
                )
                self._return_to_observation_home_with_moveit(
                    target_joints=target_joints,
                    rejected_error=exc,
                )
                return
            raise
        duration_s = float(
            np.clip(
                max_delta / RETURN_HOME_RAD_PER_S,
                RETURN_HOME_MIN_DURATION_S,
                RETURN_HOME_MAX_DURATION_S,
            )
        )
        self.stage_trace.append("return home")
        self._emit_progress(
            "Simulator returning arm to observation home.",
            phase="return_home",
            stage="return home",
        )
        self._log(
            "return_home_start",
            start_joints=start_joints,
            target_joints=target_joints,
            duration_s=duration_s,
            trajectory_time_scale=self._moveit_trajectory_time_scale(),
            minimum_playback_duration_s=RETURN_HOME_MIN_PLAYBACK_DURATION_S,
            state=self._runtime_snapshot(),
        )
        self._drive_joint_segment(
            start_joints,
            target_joints,
            duration_s,
            minimum_s=RETURN_HOME_MIN_PLAYBACK_DURATION_S,
        )
        self.current_joint_target = target_joints.copy()
        self.data.qpos[:ARM_DOF] = target_joints
        self.data.ctrl[:ARM_DOF] = target_joints
        mujoco.mj_forward(self.model, self.data)
        self._settle(0.20)
        self._log(
            "return_home_complete",
            target_joints=target_joints,
            state=self._runtime_snapshot(),
        )

    def _return_to_observation_home_with_moveit(
        self,
        *,
        target_joints: np.ndarray,
        rejected_error: CollisionPlanError,
    ) -> None:
        target_position, target_rotation = self._tcp_pose_for_joint_target(
            target_joints
        )
        self.stage_trace.append("return home")
        self._emit_progress(
            "Simulator returning arm to observation home with MoveIt.",
            phase="return_home",
            stage="moveit return home",
            fallback_reason=str(rejected_error),
        )
        self._log(
            "return_home_moveit_start",
            fallback_reason=str(rejected_error),
            target_joints=target_joints,
            target_mujoco_tcp_m=target_position,
            trajectory_time_scale=self._moveit_trajectory_time_scale(),
            minimum_playback_duration_s=RETURN_HOME_MIN_PLAYBACK_DURATION_S,
            state=self._runtime_snapshot(),
        )
        planned = self._plan_moveit_stage(
            "moveit return home",
            target_position,
            target_rotation,
            speed_mm_s=180.0,
            omit_tags=set(),
            orientation_tolerance_rad=0.08,
        )
        self._execute_moveit_stage_plan(
            planned,
            minimum_s=RETURN_HOME_MIN_PLAYBACK_DURATION_S,
        )
        self._settle(0.20)
        self._log(
            "return_home_complete",
            method="moveit_pose",
            target_joints=target_joints,
            final_joints=self.current_joint_target.copy(),
            state=self._runtime_snapshot(),
        )

    @staticmethod
    def _interpolate_moveit_joint_target(
        start_position: np.ndarray,
        end_position: np.ndarray,
        start_velocity: np.ndarray | None,
        end_velocity: np.ndarray | None,
        duration_s: float,
        phase: float,
    ) -> np.ndarray:
        phase = float(np.clip(phase, 0.0, 1.0))
        if start_velocity is None or end_velocity is None:
            return start_position + phase * (end_position - start_position)
        h00 = 2.0 * phase**3 - 3.0 * phase**2 + 1.0
        h10 = phase**3 - 2.0 * phase**2 + phase
        h01 = -2.0 * phase**3 + 3.0 * phase**2
        h11 = phase**3 - phase**2
        return (
            h00 * start_position
            + h10 * duration_s * start_velocity
            + h01 * end_position
            + h11 * duration_s * end_velocity
        )

    def _moveit_trajectory_waypoints(
        self,
        points: Sequence[Mapping[str, Any]],
        *,
        joint_names: Sequence[str],
        start_joints: np.ndarray,
        fallback_duration_s: float,
    ) -> tuple[list[float], list[np.ndarray], list[np.ndarray | None]]:
        if not points:
            raise SimulatorError("MoveIt returned an empty trajectory")
        index_by_name = {name: index for index, name in enumerate(joint_names)}
        missing = [name for name in MOVEIT_JOINT_NAMES if name not in index_by_name]
        if missing:
            raise SimulatorError(
                f"MoveIt trajectory is missing xArm joints: {', '.join(missing)}"
            )
        times = [0.0]
        positions = [np.asarray(start_joints, dtype=float).copy()]
        velocities: list[np.ndarray | None] = [np.zeros(ARM_DOF, dtype=float)]
        fallback_step_s = max(float(fallback_duration_s), 0.05) / max(len(points), 1)

        for point in points:
            raw_positions = point.get("positions")
            if not isinstance(raw_positions, AbcSequence):
                raise SimulatorError("MoveIt trajectory point lacks positions")
            target = np.asarray(
                [
                    float(raw_positions[index_by_name[name]])
                    for name in MOVEIT_JOINT_NAMES
                ],
                dtype=float,
            )
            if len(target) != ARM_DOF:
                raise SimulatorError("MoveIt trajectory point has wrong joint count")
            target = self._nearest_equivalent_joints(target, positions[-1])

            target_time = point.get("time_from_start_s")
            if target_time is None:
                target_time = times[-1] + fallback_step_s
            target_time = float(target_time)
            if target_time <= times[-1]:
                target_time = times[-1] + fallback_step_s

            raw_velocities = point.get("velocities")
            velocity: np.ndarray | None = None
            if isinstance(raw_velocities, AbcSequence) and len(raw_velocities) >= len(joint_names):
                velocity = np.asarray(
                    [
                        float(raw_velocities[index_by_name[name]])
                        for name in MOVEIT_JOINT_NAMES
                    ],
                    dtype=float,
                )
            times.append(target_time)
            positions.append(target)
            velocities.append(velocity)
        return times, positions, velocities

    def _normalize_moveit_start_joints(self) -> None:
        normalized = (
            (self.current_joint_target + math.pi) % (2.0 * math.pi)
        ) - math.pi
        normalized = self._moveit_limited_joints(normalized)
        if np.allclose(normalized, self.current_joint_target, atol=1e-9):
            return
        previous = self.current_joint_target.copy()
        self.current_joint_target = normalized.copy()
        self.data.qpos[:ARM_DOF] = normalized
        self.data.ctrl[:ARM_DOF] = normalized
        mujoco.mj_forward(self.model, self.data)
        self._log(
            "moveit_start_joints_normalized",
            previous_joints=previous,
            normalized_joints=normalized,
            moveit_joint_limits_rad=MOVEIT_JOINT_LIMITS_RAD,
        )

    def _preflight_joint_path(
        self,
        start: np.ndarray,
        end: np.ndarray,
        *,
        allowed_tag: Optional[str],
    ) -> None:
        probe = mujoco.MjData(self.model)
        probe.qpos[:] = self.data.qpos
        probe.eq_active[:] = self.data.eq_active
        allowed_body = self._component_body_ids.get(allowed_tag or "")
        attached_probe_state = None
        if allowed_tag is not None and allowed_tag in self.scene.components:
            spec = self.scene.components[allowed_tag]
            weld_id = self.model.equality(spec.weld_name).id
            if bool(self.data.eq_active[weld_id]):
                mujoco.mj_forward(self.model, self.data)
                tcp_position = self.data.site("link_tcp").xpos.copy()
                tcp_rotation = self.data.site("link_tcp").xmat.reshape(3, 3).copy()
                object_position = self.data.body(spec.body_name).xpos.copy()
                object_rotation = self.data.body(spec.body_name).xmat.reshape(3, 3).copy()
                joint_id = self.model.joint(spec.joint_name).id
                attached_probe_state = (
                    int(self.model.jnt_qposadr[joint_id]),
                    tcp_rotation.T @ (object_position - tcp_position),
                    tcp_rotation.T @ object_rotation,
                )
        component_bodies = set(self._component_body_ids.values())
        for fraction in np.linspace(0.0, 1.0, 18):
            probe.qpos[:ARM_DOF] = start + float(fraction) * (end - start)
            mujoco.mj_forward(self.model, probe)
            if attached_probe_state is not None:
                qpos_addr, relative_position, relative_rotation = attached_probe_state
                tcp_position = probe.site("link_tcp").xpos.copy()
                tcp_rotation = probe.site("link_tcp").xmat.reshape(3, 3).copy()
                object_position = tcp_position + tcp_rotation @ relative_position
                object_rotation = tcp_rotation @ relative_rotation
                probe.qpos[qpos_addr : qpos_addr + 3] = object_position
                probe.qpos[qpos_addr + 3 : qpos_addr + 7] = _matrix_to_quat_wxyz(
                    object_rotation
                )
                mujoco.mj_forward(self.model, probe)
            for contact_index in range(probe.ncon):
                contact = probe.contact[contact_index]
                geom1, geom2 = int(contact.geom1), int(contact.geom2)
                body1 = int(self.model.geom_bodyid[geom1])
                body2 = int(self.model.geom_bodyid[geom2])
                name1 = self.model.body(body1).name
                name2 = self.model.body(body2).name

                if geom1 == self._table_geom_id or geom2 == self._table_geom_id:
                    arm_body = body2 if geom1 == self._table_geom_id else body1
                    arm_name = self.model.body(arm_body).name
                    if arm_body not in component_bodies and arm_name != "link_base":
                        raise CollisionPlanError(
                            f"planned robot path contacts tabletop at {arm_name}"
                        )
                    continue

                static_object_id = self._static_collision_geom_ids.get(geom1)
                static_geom = geom1
                moving_body = body2
                moving_name = name2
                if static_object_id is None:
                    static_object_id = self._static_collision_geom_ids.get(geom2)
                    static_geom = geom2
                    moving_body = body1
                    moving_name = name1
                if static_object_id is not None:
                    if self.model.geom_bodyid[static_geom] == moving_body:
                        continue
                    if moving_name != "world":
                        raise CollisionPlanError(
                            f"planned robot path contacts {static_object_id} "
                            f"at {moving_name}"
                        )
                    continue

                component_body = None
                arm_body = None
                if body1 in component_bodies and body2 not in component_bodies:
                    component_body, arm_body = body1, body2
                elif body2 in component_bodies and body1 not in component_bodies:
                    component_body, arm_body = body2, body1
                if component_body is None:
                    continue
                if component_body != allowed_body:
                    hit_tag = next(
                        tag
                        for tag, body_id in self._component_body_ids.items()
                        if body_id == component_body
                    )
                    raise CollisionPlanError(
                        f"planned robot path contacts {hit_tag} "
                        f"({name1} / {name2})"
                    )
                if self.planner_backend != MUJOCO_PLANNER_MOVEIT:
                    continue
                arm_name = self.model.body(arm_body).name
                if arm_name not in GRIPPER_COMPONENT_TOUCH_BODIES:
                    hit_tag = next(
                        tag
                        for tag, body_id in self._component_body_ids.items()
                        if body_id == component_body
                    )
                    raise CollisionPlanError(
                        f"planned robot path contacts {hit_tag} with {arm_name}"
                    )

    def _cartesian_stage_targets(
        self,
        position_m: np.ndarray,
        rotation: np.ndarray,
    ) -> list[tuple[np.ndarray, np.ndarray]]:
        mujoco.mj_forward(self.model, self.data)
        start_position = self.data.site("link_tcp").xpos.copy()
        target_position = np.asarray(position_m, dtype=float)
        target_rotation = np.asarray(rotation, dtype=float)
        distance_m = float(np.linalg.norm(target_position - start_position))
        step_count = max(
            1,
            int(math.ceil(distance_m / MOVEIT_MANUAL_CARTESIAN_STEP_M)),
        )
        targets: list[tuple[np.ndarray, np.ndarray]] = []
        for index in range(1, step_count + 1):
            fraction = index / step_count
            interpolated_position = start_position + fraction * (
                target_position - start_position
            )
            targets.append((interpolated_position, target_rotation))
        return targets

    def _target_rotation(self, yaw_deg: float) -> np.ndarray:
        return _rotation_z(math.radians(float(yaw_deg))) @ self.home_tcp_rotation

    def _moveit_equivalent_target_rotation(
        self,
        yaw_deg: float,
        *,
        reference_rotation: np.ndarray | None = None,
    ) -> np.ndarray:
        if reference_rotation is None:
            mujoco.mj_forward(self.model, self.data)
            reference_rotation = self.data.site("link_tcp").xmat.reshape(3, 3).copy()
        candidates = [
            (float(yaw_deg), self._target_rotation(float(yaw_deg))),
            (float(yaw_deg) + 180.0, self._target_rotation(float(yaw_deg) + 180.0)),
        ]
        chosen_yaw, chosen_rotation = min(
            candidates,
            key=lambda candidate: _rotation_distance_deg(
                reference_rotation,
                candidate[1],
            ),
        )
        if _angle_error_deg(chosen_yaw, yaw_deg) > 1.0:
            self._log(
                "moveit_equivalent_yaw_selected",
                requested_yaw_deg=float(yaw_deg),
                chosen_yaw_deg=float(chosen_yaw),
                rotation_distance_deg=_rotation_distance_deg(
                    reference_rotation,
                    chosen_rotation,
                ),
            )
        return chosen_rotation

    def _preflight_waypoints(
        self,
        waypoints: Sequence[tuple[np.ndarray, np.ndarray]],
        *,
        allowed_tag: str,
    ) -> None:
        seed = self.current_joint_target.copy()
        for position_m, rotation in waypoints:
            target = self.ik.solve(
                self.model,
                np.asarray(position_m, dtype=float),
                np.asarray(rotation, dtype=float),
                seed,
                self.home,
            )
            target = self._moveit_safe_joints(target)
            self._preflight_joint_path(seed, target, allowed_tag=allowed_tag)
            seed = target

    def _validate_destination(
        self,
        spec: ComponentSpec,
        target_xy: np.ndarray,
    ) -> None:
        bounds = self.scene.lab_bounds_mm
        x_mm, y_mm = target_xy * 1000.0
        half_width_mm = spec.width_m * 500.0
        half_depth_mm = spec.depth_m * 500.0
        if not (
            bounds["x_min"] + half_width_mm <= x_mm <= bounds["x_max"] - half_width_mm
            and bounds["y_min"] + half_depth_mm <= y_mm <= bounds["y_max"] - half_depth_mm
        ):
            raise SimulatorError(
                f"destination for {spec.tag_id} places part outside table bounds"
            )

        for other_tag, other_spec in self.scene.components.items():
            if other_tag == spec.tag_id:
                continue
            other_position, _ = self._object_pose(other_tag)
            separation = np.abs(target_xy - other_position[:2])
            required = np.array(
                (
                    (spec.width_m + other_spec.width_m) / 2.0,
                    (spec.depth_m + other_spec.depth_m) / 2.0,
                )
            )
            if bool(np.all(separation < required + 0.005)):
                raise CollisionPlanError(
                    f"destination for {spec.tag_id} overlaps {other_tag}"
                )

    def _travel_clearance_z(self, spec: ComponentSpec) -> float:
        highest_component_top = max(
            component.center_z_m + component.height_m / 2.0
            for component in self.scene.components.values()
        )
        clearance_z = max(
            CLEARANCE_Z_M,
            spec.grasp_tcp_z_m + TRAVEL_CLEARANCE_ABOVE_GRASP_M,
            highest_component_top + TRAVEL_CLEARANCE_ABOVE_COMPONENT_TOP_M,
        )
        return float(min(clearance_z, MAX_TRAVEL_CLEARANCE_Z_M))

    def _pose_for(self, position_m: np.ndarray, rotation: np.ndarray) -> list[float]:
        return [
            *(np.asarray(position_m) * 1000.0).tolist(),
            *_matrix_to_rotvec_deg(rotation).tolist(),
        ]

    def _move_stage(
        self,
        name: str,
        arm: XArmAPI,
        position_m: np.ndarray,
        rotation: np.ndarray,
        *,
        speed_mm_s: float,
    ) -> None:
        self.stage_trace.append(name)
        self._log(
            "controller_stage_request",
            stage=name,
            target_mujoco_tcp_m=np.asarray(position_m, dtype=float),
            speed_mm_s=float(speed_mm_s),
            state=self._runtime_snapshot(),
        )
        targets = [(np.asarray(position_m, dtype=float), np.asarray(rotation, dtype=float))]
        if (
            self.planner_backend == MUJOCO_PLANNER_MOVEIT
            and name in MOVEIT_MANUAL_CARTESIAN_STAGES
        ):
            targets = self._cartesian_stage_targets(position_m, rotation)
            try:
                self._emit_progress(
                    f"Simulator solving manual {name}.",
                    phase="manual_execute",
                    stage=name,
                )
                self._execute_cartesian_path(targets, speed_mm_s=speed_mm_s)
            except Exception as exc:  # noqa: BLE001
                self._log(
                    "controller_stage_failure",
                    stage=name,
                    error=str(exc),
                    traceback=traceback.format_exc(),
                    state=self._runtime_snapshot(),
                )
                raise
            self._log(
                "controller_stage_complete",
                stage=name,
                state=self._runtime_snapshot(),
            )
            self._emit_progress(
                f"Simulator completed manual {name}.",
                phase="manual_execute",
                stage=name,
            )
            return
        try:
            self._emit_progress(
                f"Simulator executing {name}.",
                phase="manual_execute",
                stage=name,
            )
            for target_position, target_rotation in targets:
                arm.set_position_aa(
                    self._pose_for(target_position, target_rotation),
                    speed=speed_mm_s,
                    mvacc=100.0,
                    wait=True,
                    is_radian=False,
                )
        except Exception as exc:  # noqa: BLE001
            self._log(
                "controller_stage_failure",
                stage=name,
                error=str(exc),
                traceback=traceback.format_exc(),
                state=self._runtime_snapshot(),
            )
            raise
        self._log(
            "controller_stage_complete",
            stage=name,
            state=self._runtime_snapshot(),
        )
        self._emit_progress(
            f"Simulator completed {name}.",
            phase="manual_execute",
            stage=name,
        )

    def _moveit_pose_payload(
        self,
        position_m: np.ndarray,
        rotation: np.ndarray,
    ) -> Dict[str, Any]:
        return {
            "position": [float(value) for value in np.asarray(position_m, dtype=float)],
            "orientation_xyzw": _matrix_to_quat_xyzw(np.asarray(rotation, dtype=float)),
        }

    def _moveit_pose_link_position(
        self,
        mujoco_tcp_position_m: np.ndarray,
        rotation: np.ndarray,
    ) -> np.ndarray:
        return (
            np.asarray(mujoco_tcp_position_m, dtype=float)
            - np.asarray(rotation, dtype=float) @ self.moveit_tcp_offset_local_m
        )

    def _moveit_pose_link_rotation(self, mujoco_tcp_rotation: np.ndarray) -> np.ndarray:
        return (
            np.asarray(mujoco_tcp_rotation, dtype=float)
            @ MOVEIT_TCP_TO_MUJOCO_TCP_ROTATION.T
        )

    def _calibrate_moveit_tcp_offset(
        self,
        desired_mujoco_tcp_position_m: np.ndarray,
        rotation: np.ndarray,
    ) -> None:
        if self._moveit_tcp_offset_auto_calibrated:
            return
        mujoco.mj_forward(self.model, self.data)
        actual_mujoco_tcp = self.data.site("link_tcp").xpos.copy()
        world_error = actual_mujoco_tcp - np.asarray(
            desired_mujoco_tcp_position_m,
            dtype=float,
        )
        local_error = np.asarray(rotation, dtype=float).T @ world_error
        if abs(float(local_error[2])) < 0.02:
            return
        self.moveit_tcp_offset_local_m[2] += float(local_error[2])
        self._moveit_tcp_offset_auto_calibrated = True
        self.stage_trace.append(
            "calibrated moveit tcp z offset "
            f"{self.moveit_tcp_offset_local_m[2] * 1000.0:.1f} mm"
        )

    def _moveit_object_pose(
        self,
        position_m: np.ndarray,
        rotation: np.ndarray,
    ) -> Dict[str, Any]:
        return {
            "position": [float(value) for value in np.asarray(position_m, dtype=float)],
            "orientation_xyzw": _matrix_to_quat_xyzw(np.asarray(rotation, dtype=float)),
        }

    def _moveit_world_objects(
        self,
        *,
        omit_tags: set[str] | None = None,
    ) -> list[Dict[str, Any]]:
        omit_tags = omit_tags or set()
        bounds = self.scene.table_bounds_mm
        x_center_m = (bounds["x_min"] + bounds["x_max"]) / 2000.0
        y_center_m = (bounds["y_min"] + bounds["y_max"]) / 2000.0
        table_width_m = (bounds["x_max"] - bounds["x_min"]) / 1000.0
        table_depth_m = (bounds["y_max"] - bounds["y_min"]) / 1000.0
        objects: list[Dict[str, Any]] = []
        include_table = (
            os.getenv(MOVEIT_INCLUDE_TABLE_ENV_VAR, "").strip().lower()
            in {"1", "true", "yes", "on"}
        )
        if include_table:
            objects.append(
                {
                "id": "cloudlab_tabletop",
                "type": "box",
                "frame_id": "world",
                "dimensions": [
                    table_width_m,
                    table_depth_m,
                    MOVEIT_TABLE_COLLISION_HEIGHT_M,
                ],
                "pose": self._moveit_object_pose(
                    np.array(
                        (
                            x_center_m,
                            y_center_m,
                            TABLE_SURFACE_Z_M
                            - MOVEIT_TABLE_COLLISION_SURFACE_MARGIN_M
                            - MOVEIT_TABLE_COLLISION_HEIGHT_M / 2.0,
                        )
                    ),
                    np.eye(3),
                ),
            }
            )
        for obj in self.scene.static_collision_objects:
            objects.append(
                {
                    "id": obj.object_id,
                    "type": "box",
                    "frame_id": "world",
                    "dimensions": list(obj.dimensions_m),
                    "pose": self._moveit_object_pose(
                        np.asarray(obj.position_m, dtype=float),
                        np.eye(3),
                    ),
                }
            )
        for tag_id, spec in self.scene.components.items():
            if tag_id in omit_tags:
                continue
            position, _ = self._object_pose(tag_id)
            rotation = self.data.body(spec.body_name).xmat.reshape(3, 3).copy()
            objects.append(
                {
                    "id": f"component_{tag_id}",
                    "tag_id": tag_id,
                    "type": "box",
                    "frame_id": "world",
                    "dimensions": [spec.width_m, spec.depth_m, spec.height_m],
                    "pose": self._moveit_object_pose(position, rotation),
                }
            )
        return objects

    def _moveit_known_collision_object_ids(self) -> list[str]:
        return [
            "cloudlab_tabletop",
            *[
                obj.object_id
                for obj in self.scene.static_collision_objects
            ],
            *[
                f"component_{tag_id}"
                for tag_id in sorted(self.scene.components)
            ],
        ]

    def _moveit_attached_object(self, tag_id: str) -> Dict[str, Any]:
        spec = self.scene.components[tag_id]
        mujoco.mj_forward(self.model, self.data)
        tcp_position = self.data.site("link_tcp").xpos.copy()
        tcp_rotation = self.data.site("link_tcp").xmat.reshape(3, 3).copy()
        object_position = self.data.body(spec.body_name).xpos.copy()
        object_rotation = self.data.body(spec.body_name).xmat.reshape(3, 3).copy()
        relative_position_mujoco_tcp = (
            tcp_rotation.T @ (object_position - tcp_position)
            + self.moveit_tcp_offset_local_m
        )
        relative_position = (
            MOVEIT_TCP_TO_MUJOCO_TCP_ROTATION @ relative_position_mujoco_tcp
        )
        relative_rotation = (
            MOVEIT_TCP_TO_MUJOCO_TCP_ROTATION @ tcp_rotation.T @ object_rotation
        )
        return {
            "id": f"component_{tag_id}",
            "tag_id": tag_id,
            "link_name": "link_tcp",
            "touch_links": [
                "link_tcp",
                "link_eef",
                "xarm_gripper_base_link",
                "left_outer_knuckle",
                "left_inner_knuckle",
                "left_finger",
                "right_outer_knuckle",
                "right_inner_knuckle",
                "right_finger",
            ],
            "type": "box",
            "dimensions": [spec.width_m, spec.depth_m, spec.height_m],
            "pose": self._moveit_object_pose(relative_position, relative_rotation),
        }

    def _moveit_velocity_scale(self) -> float:
        return _env_float(
            MOVEIT_MAX_VELOCITY_SCALE_ENV_VAR,
            MOVEIT_DEFAULT_VELOCITY_SCALE,
            minimum=0.05,
            maximum=1.0,
        )

    def _moveit_acceleration_scale(self) -> float:
        return _env_float(
            MOVEIT_MAX_ACCELERATION_SCALE_ENV_VAR,
            MOVEIT_DEFAULT_ACCELERATION_SCALE,
            minimum=0.05,
            maximum=1.0,
        )

    def _moveit_allowed_planning_time_s(self) -> float:
        return _env_float(
            MOVEIT_ALLOWED_PLANNING_TIME_ENV_VAR,
            MOVEIT_DEFAULT_ALLOWED_PLANNING_TIME_S,
            minimum=0.5,
            maximum=10.0,
        )

    def _moveit_planning_attempts(self) -> int:
        return _env_int(
            MOVEIT_PLANNING_ATTEMPTS_ENV_VAR,
            MOVEIT_DEFAULT_PLANNING_ATTEMPTS,
            minimum=1,
            maximum=16,
        )

    def _moveit_trajectory_time_scale(self) -> float:
        return _env_float(
            MOVEIT_TRAJECTORY_TIME_SCALE_ENV_VAR,
            MOVEIT_TRAJECTORY_TIME_SCALE,
            minimum=0.25,
            maximum=2.0,
        )

    def _moveit_carry_upright_tolerance_rad(self) -> float:
        return math.radians(
            _env_float(
                MOVEIT_CARRY_UPRIGHT_TOLERANCE_DEG_ENV_VAR,
                MOVEIT_CARRY_UPRIGHT_TOLERANCE_DEG,
                minimum=1.0,
                maximum=30.0,
            )
        )

    def _moveit_carry_joint7_continuity_tolerance_rad(self) -> float:
        return math.radians(
            _env_float(
                MOVEIT_CARRY_JOINT7_CONTINUITY_TOLERANCE_DEG_ENV_VAR,
                MOVEIT_CARRY_JOINT7_CONTINUITY_TOLERANCE_DEG,
                minimum=15.0,
                maximum=180.0,
            )
        )

    def _moveit_carry_yaw_step_deg(self) -> float:
        return _env_float(
            MOVEIT_CARRY_YAW_STEP_DEG_ENV_VAR,
            MOVEIT_CARRY_YAW_STEP_DEG,
            minimum=5.0,
            maximum=90.0,
        )

    def _moveit_carry_upright_path_constraint(
        self,
        rotation: np.ndarray,
        *,
        yaw_tolerance_rad: float | None = None,
    ) -> Dict[str, Any]:
        tolerance_rad = self._moveit_carry_upright_tolerance_rad()
        return {
            "frame_id": "world",
            "link_name": "link_tcp",
            "orientation_xyzw": _matrix_to_quat_xyzw(
                self._moveit_pose_link_rotation(rotation)
            ),
            "absolute_x_axis_tolerance": tolerance_rad,
            "absolute_y_axis_tolerance": tolerance_rad,
            "absolute_z_axis_tolerance": float(
                math.pi if yaw_tolerance_rad is None else yaw_tolerance_rad
            ),
            "weight": 1.0,
        }

    def _moveit_joint7_path_constraints(
        self,
        tolerance_rad: float,
    ) -> list[Dict[str, Any]]:
        if tolerance_rad <= 0.0 or "joint7" not in MOVEIT_JOINT_NAMES:
            return []
        joint_index = MOVEIT_JOINT_NAMES.index("joint7")
        return [
            {
                "joint_name": "joint7",
                "position": float(self.current_joint_target[joint_index]),
                "tolerance_above": float(tolerance_rad),
                "tolerance_below": float(tolerance_rad),
                "weight": 1.0,
            }
        ]

    def _target_yaw_from_rotation(self, rotation: np.ndarray) -> float:
        relative = np.asarray(rotation, dtype=float) @ self.home_tcp_rotation.T
        return math.degrees(math.atan2(relative[1, 0], relative[0, 0]))

    def _moveit_carry_yaw_rotations(
        self,
        source_rotation: np.ndarray,
        target_rotation: np.ndarray,
    ) -> list[np.ndarray]:
        source_yaw = self._target_yaw_from_rotation(source_rotation)
        target_yaw = self._target_yaw_from_rotation(target_rotation)
        delta = (target_yaw - source_yaw + 180.0) % 360.0 - 180.0
        if abs(delta) < 0.5:
            return []
        step_deg = self._moveit_carry_yaw_step_deg()
        step_count = max(1, int(math.ceil(abs(delta) / step_deg)))
        return [
            self._target_rotation(source_yaw + delta * (index / step_count))
            for index in range(1, step_count + 1)
        ]

    def _scaled_manual_duration_s(
        self,
        duration_s: float,
        *,
        minimum_s: float,
    ) -> float:
        """Apply the trajectory-time knob to non-MoveIt simulated motion phases."""

        return max(
            float(duration_s) * self._moveit_trajectory_time_scale(),
            float(minimum_s),
        )

    def _plan_moveit_stage(
        self,
        name: str,
        position_m: np.ndarray,
        rotation: np.ndarray,
        *,
        speed_mm_s: float,
        omit_tags: set[str] | None = None,
        attached_tag: str | None = None,
        orientation_tolerance_rad: float = 0.05,
        joint7_continuity_tolerance_rad: float = 0.0,
        path_orientation_constraint: Mapping[str, Any] | None = None,
        path_joint7_continuity: bool = False,
    ) -> MoveItStagePlan:
        if self.moveit is None:
            raise SimulatorError("MoveIt planner client is not configured")
        current_tcp = self.data.site("link_tcp").xpos.copy()
        distance_m = float(np.linalg.norm(np.asarray(position_m) - current_tcp))
        speed_m_s = max(float(speed_mm_s) / 1000.0, 0.04)
        fallback_duration_s = float(np.clip(distance_m / speed_m_s, 0.35, 2.8))
        attached = (
            [self._moveit_attached_object(attached_tag)]
            if attached_tag is not None
            else []
        )
        self._normalize_moveit_start_joints()
        path_joint_constraints = (
            self._moveit_joint7_path_constraints(joint7_continuity_tolerance_rad)
            if path_joint7_continuity
            else []
        )
        stage_request_id = (
            f"{self.active_request_id or 'no-request'}:{name}:{self.data.time:.3f}"
        )
        target_pose = self._moveit_pose_payload(
            self._moveit_pose_link_position(position_m, rotation),
            self._moveit_pose_link_rotation(rotation),
        )
        world_objects = self._moveit_world_objects(
            omit_tags=set(omit_tags or set())
        )
        known_collision_object_ids = self._moveit_known_collision_object_ids()
        velocity_scale = self._moveit_velocity_scale()
        acceleration_scale = self._moveit_acceleration_scale()
        allowed_planning_time_s = self._moveit_allowed_planning_time_s()
        planning_attempts = self._moveit_planning_attempts()
        self._log(
            "moveit_stage_request",
            stage=name,
            stage_request_id=stage_request_id,
            desired_mujoco_tcp_m=np.asarray(position_m, dtype=float),
            target_pose=target_pose,
            speed_mm_s=float(speed_mm_s),
            fallback_duration_s=fallback_duration_s,
            omitted_tags=sorted(set(omit_tags or set())),
            attached_tag=attached_tag,
            attached_objects=attached,
            known_collision_object_ids=known_collision_object_ids,
            max_velocity_scaling_factor=velocity_scale,
            max_acceleration_scaling_factor=acceleration_scale,
            allowed_planning_time_s=allowed_planning_time_s,
            planning_attempts=planning_attempts,
            orientation_tolerance_rad=float(orientation_tolerance_rad),
            joint7_continuity_tolerance_rad=float(joint7_continuity_tolerance_rad),
            path_orientation_constraint=(
                dict(path_orientation_constraint)
                if path_orientation_constraint is not None
                else None
            ),
            path_joint_constraints=path_joint_constraints,
            world_objects=world_objects,
            state=self._runtime_snapshot(),
        )
        progress_message = (
            self._moveit_plan_progress_message_override
            or f"MoveIt planning {name}."
        )
        if self._moveit_plan_progress_prefix:
            progress_message = (
                "MoveIt "
                + self._moveit_plan_progress_prefix
                + " "
                + progress_message[len("MoveIt ") :]
            )
        self._emit_progress(
            progress_message,
            phase="moveit_plan",
            stage=name,
            stage_request_id=stage_request_id,
            attached_tag=attached_tag,
            target_mujoco_tcp_m=np.asarray(position_m, dtype=float),
            world_object_count=len(world_objects),
            has_upright_constraint=path_orientation_constraint is not None,
            path_joint_constraint_count=len(path_joint_constraints),
        )
        try:
            plan = self.moveit.plan_pose(
                request_id=stage_request_id,
                joint_names=MOVEIT_JOINT_NAMES,
                start_joints=self.current_joint_target.tolist(),
                target_pose=target_pose,
                world_objects=world_objects,
                attached_objects=attached,
                known_collision_object_ids=known_collision_object_ids,
                allowed_planning_time_s=allowed_planning_time_s,
                planning_attempts=planning_attempts,
                orientation_tolerance_rad=orientation_tolerance_rad,
                joint7_continuity_tolerance_rad=joint7_continuity_tolerance_rad,
                path_orientation_constraint=path_orientation_constraint,
                path_joint_constraints=path_joint_constraints,
                max_velocity_scaling_factor=velocity_scale,
                max_acceleration_scaling_factor=acceleration_scale,
            )
        except MoveItPlannerError as exc:
            self._emit_progress(
                f"MoveIt failed while planning {name}.",
                phase="moveit_plan",
                stage=name,
                stage_request_id=stage_request_id,
                error=self._short_planning_error(exc),
            )
            self._log(
                "moveit_stage_plan_failure",
                stage=name,
                stage_request_id=stage_request_id,
                error=str(exc),
                moveit_details=getattr(exc, "details", None),
                state=self._runtime_snapshot(),
                log_path=self.diagnostic_log_path(),
            )
            raise SimulatorError(
                f"MoveIt failed during {name}: {exc}; "
                f"request_id={self.active_request_id}; "
                f"log={self.diagnostic_log_path()}"
            ) from exc
        trajectory = plan.get("joint_trajectory") or {}
        points = trajectory.get("points") if isinstance(trajectory, AbcMapping) else None
        joint_names = (
            trajectory.get("joint_names") if isinstance(trajectory, AbcMapping) else None
        )
        if not isinstance(points, AbcSequence) or not isinstance(joint_names, AbcSequence):
            raise SimulatorError("MoveIt response lacks a joint trajectory")
        joint_name_strings = tuple(str(name) for name in joint_names)
        _, positions, _ = self._moveit_trajectory_with_preflight(
            points,
            joint_names=joint_name_strings,
            fallback_duration_s=fallback_duration_s,
            allowed_tag=self.allowed_collision_tag,
        )
        progress_stem = progress_message[:-1] if progress_message.endswith(".") else progress_message
        if progress_stem.startswith("MoveIt planning "):
            planned_message = (
                "MoveIt planned "
                + progress_stem[len("MoveIt planning ") :]
                + f" ({len(points)} points)."
            )
        elif progress_stem.startswith("MoveIt ") and " planning " in progress_stem:
            planned_message = (
                progress_stem.replace(" planning ", " planned ", 1)
                + f" ({len(points)} points)."
            )
        else:
            planned_message = f"MoveIt planned {name} ({len(points)} points)."
        self._emit_progress(
            planned_message,
            phase="moveit_plan",
            stage=name,
            stage_request_id=stage_request_id,
            trajectory_point_count=len(points),
        )
        self._log(
            "moveit_stage_plan_success",
            stage=name,
            stage_request_id=stage_request_id,
            moveit_response={
                key: value
                for key, value in plan.items()
                if key != "joint_trajectory"
            },
            trajectory_point_count=len(points),
            trajectory_joint_names=list(joint_name_strings),
        )
        return MoveItStagePlan(
            name=name,
            stage_request_id=stage_request_id,
            position_m=np.asarray(position_m, dtype=float).copy(),
            rotation=np.asarray(rotation, dtype=float).copy(),
            points=points,
            joint_names=joint_name_strings,
            fallback_duration_s=fallback_duration_s,
            allowed_tag=self.allowed_collision_tag,
            final_joints=positions[-1].copy(),
        )

    def _execute_moveit_stage_plan(
        self,
        planned: MoveItStagePlan,
        *,
        minimum_s: float = 0.02,
    ) -> None:
        self.stage_trace.append(planned.name)
        self._emit_progress(
            f"Simulator executing {planned.name}.",
            phase="moveit_execute",
            stage=planned.name,
            stage_request_id=planned.stage_request_id,
        )
        try:
            self._execute_moveit_trajectory(
                planned.points,
                joint_names=planned.joint_names,
                fallback_duration_s=planned.fallback_duration_s,
                allowed_tag=planned.allowed_tag,
                minimum_s=minimum_s,
            )
        except Exception as exc:  # noqa: BLE001
            self._log(
                "moveit_stage_execute_failure",
                stage=planned.name,
                stage_request_id=planned.stage_request_id,
                error=str(exc),
                traceback=traceback.format_exc(),
                state=self._runtime_snapshot(),
            )
            raise
        self._log(
            "moveit_stage_execute_complete",
            stage=planned.name,
            stage_request_id=planned.stage_request_id,
            state=self._runtime_snapshot(),
        )
        self._emit_progress(
            f"Simulator completed {planned.name}.",
            phase="moveit_execute",
            stage=planned.name,
            stage_request_id=planned.stage_request_id,
        )
        self._calibrate_moveit_tcp_offset(planned.position_m, planned.rotation)

    def _moveit_move_stage(
        self,
        name: str,
        position_m: np.ndarray,
        rotation: np.ndarray,
        *,
        speed_mm_s: float,
        omit_tags: set[str] | None = None,
        attached_tag: str | None = None,
    ) -> None:
        planned = self._plan_moveit_stage(
            name,
            position_m,
            rotation,
            speed_mm_s=speed_mm_s,
            omit_tags=omit_tags,
            attached_tag=attached_tag,
        )
        self._execute_moveit_stage_plan(planned)

    def _moveit_prepick_feasibility_seed_count(self) -> int:
        raw = os.getenv(MOVEIT_PREPICK_FEASIBILITY_SEEDS_ENV_VAR, "").strip()
        if not raw:
            return MOVEIT_PREPICK_FEASIBILITY_SEEDS
        try:
            parsed = int(raw)
        except ValueError as exc:
            raise SimulatorError(
                f"{MOVEIT_PREPICK_FEASIBILITY_SEEDS_ENV_VAR} must be an integer"
            ) from exc
        return int(
            max(
                0,
                min(parsed, MOVEIT_PREPICK_FEASIBILITY_MAX_SEEDS),
            )
        )

    def _moveit_prepick_feasibility_timeout_s(self) -> float:
        return _env_float(
            MOVEIT_PREPICK_FEASIBILITY_TIMEOUT_ENV_VAR,
            MOVEIT_PREPICK_FEASIBILITY_TIMEOUT_S,
            minimum=0.0,
            maximum=240.0,
        )

    @staticmethod
    def _short_planning_error(error: Exception, *, limit: int = 700) -> str:
        text = " ".join(str(error).split())
        if len(text) <= limit:
            return text
        return text[: limit - 3] + "..."

    def _plan_moveit_pre_pick_feasibility(
        self,
        tag_id: str,
        *,
        source_xy: np.ndarray,
        target_xy: np.ndarray,
        source_rotation: np.ndarray,
        target_rotation: np.ndarray,
        approach_z: float,
        grasp_z: float,
        transfer_z: float,
        release_z: float,
        omitted_target: set[str],
    ) -> MoveItPrePickFeasibilityPlan | None:
        seed_total = self._moveit_prepick_feasibility_seed_count()
        if seed_total <= 0:
            self._emit_progress(
                "MoveIt pre-pick feasibility disabled.",
                phase="prepick_feasibility",
                seed_index=0,
                seed_total=0,
            )
            return None

        snapshot = self._motion_snapshot()
        seed_errors: list[str] = []
        timeout_s = self._moveit_prepick_feasibility_timeout_s()
        deadline_s = (
            time.perf_counter() + timeout_s
            if timeout_s > 0.0
            else None
        )
        attempted_seeds = 0
        self._log(
            "moveit_prepick_feasibility_start",
            tag_id=tag_id,
            seed_total=seed_total,
            timeout_s=timeout_s,
            source_xy_m=np.asarray(source_xy, dtype=float),
            target_xy_m=np.asarray(target_xy, dtype=float),
            state=self._runtime_snapshot(),
        )
        for seed_index in range(1, seed_total + 1):
            if deadline_s is not None and time.perf_counter() >= deadline_s:
                self._emit_progress(
                    (
                        "MoveIt pre-pick feasibility stopped because the "
                        f"{timeout_s:.0f}s planning budget expired."
                    ),
                    phase="prepick_feasibility",
                    seed_index=attempted_seeds,
                    seed_total=seed_total,
                    tag_id=tag_id,
                )
                break
            attempted_seeds = seed_index
            self._restore_motion_snapshot(snapshot)
            self.stage_trace = list(snapshot["stage_trace"])
            self.allowed_collision_tag = None
            self._gripper_position = GRIPPER_OPEN
            self.data.ctrl[ARM_DOF] = GRIPPER_OPEN
            self._emit_progress(
                f"MoveIt pre-pick feasibility seed {seed_index}/{seed_total}.",
                phase="prepick_feasibility",
                seed_index=seed_index,
                seed_total=seed_total,
                tag_id=tag_id,
            )
            previous_progress_prefix = self._moveit_plan_progress_prefix
            self._moveit_plan_progress_prefix = f"seed {seed_index}/{seed_total}"
            try:
                self._emit_progress(
                    f"MoveIt planning above-source approach for seed {seed_index}/{seed_total}.",
                    phase="prepick_feasibility",
                    seed_index=seed_index,
                    seed_total=seed_total,
                    tag_id=tag_id,
                )
                above_source = self._plan_moveit_stage(
                    "moveit move above source",
                    np.array((*source_xy, transfer_z), dtype=float),
                    source_rotation,
                    speed_mm_s=180.0,
                    omit_tags=omitted_target,
                )
                self._preview_moveit_stage_plan(above_source, None)
                self._emit_progress(
                    f"Simulator validating pickup descent/lift for seed {seed_index}/{seed_total}.",
                    phase="prepick_feasibility",
                    seed_index=seed_index,
                    seed_total=seed_total,
                    tag_id=tag_id,
                )
                manual_pickup_stages = self._preview_manual_pickup_to_lift(
                    tag_id,
                    source_xy=source_xy,
                    source_rotation=source_rotation,
                    approach_z=approach_z,
                    grasp_z=grasp_z,
                    lift_z=transfer_z,
                )
                self.allowed_collision_tag = tag_id
                carry = self._plan_moveit_carry_route(
                    "moveit translate",
                    source_xy=source_xy,
                    target_xy=target_xy,
                    transfer_z=transfer_z,
                    rotation=target_rotation,
                    speed_mm_s=170.0,
                    omit_tags=omitted_target,
                    attached_tag=tag_id,
                    source_rotation=source_rotation,
                    place_position_m=np.array((*target_xy, release_z), dtype=float),
                    place_speed_mm_s=80.0,
                    deadline_s=deadline_s,
                )
            except (SimulatorError, IKError) as exc:
                self._moveit_plan_progress_prefix = previous_progress_prefix
                error_text = self._short_planning_error(exc)
                seed_errors.append(f"seed {seed_index}: {error_text}")
                self._emit_progress(
                    (
                        f"MoveIt pre-pick feasibility seed "
                        f"{seed_index}/{seed_total} rejected."
                    ),
                    phase="prepick_feasibility",
                    seed_index=seed_index,
                    seed_total=seed_total,
                    tag_id=tag_id,
                    error=error_text,
                )
                self._log(
                    "moveit_prepick_feasibility_seed_rejected",
                    tag_id=tag_id,
                    seed_index=seed_index,
                    seed_total=seed_total,
                    error=error_text,
                    state=self._runtime_snapshot(),
                )
                continue
            self._moveit_plan_progress_prefix = previous_progress_prefix

            self._restore_motion_snapshot(snapshot)
            self.stage_trace = list(snapshot["stage_trace"])
            self.allowed_collision_tag = None
            self._emit_progress(
                (
                    f"MoveIt pre-pick feasibility accepted seed "
                    f"{seed_index}/{seed_total}."
                ),
                phase="prepick_feasibility",
                seed_index=seed_index,
                seed_total=seed_total,
                tag_id=tag_id,
                route_index=carry.route_index,
            )
            self._log(
                "moveit_prepick_feasibility_success",
                tag_id=tag_id,
                seed_index=seed_index,
                seed_total=seed_total,
                carry_route_index=carry.route_index,
                carry_stage_names=[stage.name for stage in carry.stages],
                state=self._runtime_snapshot(),
            )
            return MoveItPrePickFeasibilityPlan(
                seed_index=seed_index,
                seed_total=seed_total,
                above_source=above_source,
                carry=carry,
                manual_pickup_stages=tuple(manual_pickup_stages or ()),
            )

        self._restore_motion_snapshot(snapshot)
        self.stage_trace = list(snapshot["stage_trace"])
        self.allowed_collision_tag = None
        last_error = seed_errors[-1] if seed_errors else "no detailed planner error"
        attempted_label = (
            f"{attempted_seeds}/{seed_total}"
            if attempted_seeds != seed_total
            else f"{seed_total}"
        )
        self._emit_progress(
            (
                "MoveIt pre-pick feasibility failed after "
                f"{attempted_label} seeds."
            ),
            phase="prepick_feasibility",
            seed_index=attempted_seeds,
            seed_total=seed_total,
            tag_id=tag_id,
            error=last_error,
        )
        raise CollisionPlanError(
            "MoveIt pre-pick feasibility could not find a seed that worked "
            f"after {attempted_label} seeds; robot was not moved. Last error: "
            f"{last_error}; log={self.diagnostic_log_path()}"
        )

    def _moveit_carry_stage(
        self,
        name: str,
        *,
        source_xy: np.ndarray,
        target_xy: np.ndarray,
        transfer_z: float,
        rotation: np.ndarray,
        speed_mm_s: float,
        omit_tags: set[str],
        attached_tag: str,
        source_rotation: np.ndarray | None = None,
        place_position_m: np.ndarray | None = None,
        place_speed_mm_s: float = 80.0,
    ) -> None:
        carry_plan = self._plan_moveit_carry_route(
            name,
            source_xy=source_xy,
            target_xy=target_xy,
            transfer_z=transfer_z,
            rotation=rotation,
            speed_mm_s=speed_mm_s,
            omit_tags=omit_tags,
            attached_tag=attached_tag,
            source_rotation=source_rotation,
            place_position_m=place_position_m,
            place_speed_mm_s=place_speed_mm_s,
        )
        self._execute_moveit_carry_plan(carry_plan)

    def _plan_moveit_carry_route(
        self,
        name: str,
        *,
        source_xy: np.ndarray,
        target_xy: np.ndarray,
        transfer_z: float,
        rotation: np.ndarray,
        speed_mm_s: float,
        omit_tags: set[str],
        attached_tag: str,
        source_rotation: np.ndarray | None = None,
        place_position_m: np.ndarray | None = None,
        place_speed_mm_s: float = 80.0,
        deadline_s: float | None = None,
    ) -> MoveItCarryPlan:
        snapshot = self._motion_snapshot()
        translation_rotation = (
            np.asarray(source_rotation, dtype=float)
            if source_rotation is not None
            else self.data.site("link_tcp").xmat.reshape(3, 3).copy()
        )
        joint7_tolerance_rad = self._moveit_carry_joint7_continuity_tolerance_rad()

        if deadline_s is not None and time.perf_counter() >= deadline_s:
            self._emit_progress(
                "MoveIt skipped direct carry planning because the planning budget expired.",
                phase="carry_route",
                stage=name,
            )
            raise CollisionPlanError(
                "MoveIt planning budget expired before direct carry planning"
            )

        self._restore_motion_snapshot(snapshot)
        self.stage_trace = list(snapshot["stage_trace"])
        attached_pose = self._attached_component_relative_pose(attached_tag)
        planned_stages: list[MoveItStagePlan] = []
        post_carry_manual_stages: tuple[ManualCartesianStagePlan, ...] = ()
        target_xy = np.asarray(target_xy, dtype=float)
        source_xy = np.asarray(source_xy, dtype=float)
        should_translate = (
            float(np.linalg.norm(target_xy - source_xy))
            >= MOVEIT_CARRY_TRANSLATION_EPSILON_M
        )
        yaw_rotations = self._moveit_carry_yaw_rotations(
            translation_rotation,
            rotation,
        )
        carry_stage_total = (1 if should_translate else 0) + len(yaw_rotations)
        carry_stage_index = 0
        route_label = "direct carry"
        self._emit_progress(
            f"MoveIt trying {route_label}.",
            phase="carry_route",
            stage=name,
            route_index=0,
            target_z_m=float(transfer_z),
        )
        self._log(
            "moveit_carry_direct_attempt",
            stage=name,
            route_index=None,
            source_xy_m=source_xy,
            target_xy_m=target_xy,
            target_z_m=float(transfer_z),
            upright_path_constraint=self._moveit_carry_upright_path_constraint(
                translation_rotation,
                yaw_tolerance_rad=self._moveit_carry_upright_tolerance_rad(),
            ),
            joint7_continuity_tolerance_rad=joint7_tolerance_rad,
            state=self._runtime_snapshot(),
        )
        try:
            if should_translate:
                carry_stage_index += 1
                previous_progress_override = self._moveit_plan_progress_message_override
                self._moveit_plan_progress_message_override = (
                    f"MoveIt planning {carry_stage_index}/{carry_stage_total}: translate."
                )
                try:
                    planned = self._plan_moveit_stage(
                        name,
                        np.array((*target_xy, transfer_z), dtype=float),
                        translation_rotation,
                        speed_mm_s=speed_mm_s,
                        omit_tags=omit_tags,
                        attached_tag=attached_tag,
                        joint7_continuity_tolerance_rad=joint7_tolerance_rad,
                        path_orientation_constraint=(
                            self._moveit_carry_upright_path_constraint(
                                translation_rotation,
                                yaw_tolerance_rad=(
                                    self._moveit_carry_upright_tolerance_rad()
                                ),
                            )
                        ),
                        path_joint7_continuity=True,
                    )
                finally:
                    self._moveit_plan_progress_message_override = (
                        previous_progress_override
                    )
                planned_stages.append(planned)
                self._preview_moveit_stage_plan(planned, attached_pose)
            for yaw_index, yaw_rotation in enumerate(yaw_rotations, start=1):
                carry_stage_index += 1
                yaw_name = (
                    f"{name} yaw {yaw_index}/{len(yaw_rotations)}"
                )
                previous_progress_override = self._moveit_plan_progress_message_override
                self._moveit_plan_progress_message_override = (
                    f"MoveIt planning {carry_stage_index}/{carry_stage_total}: yaw."
                )
                try:
                    planned = self._plan_moveit_stage(
                        yaw_name,
                        np.array((*target_xy, transfer_z), dtype=float),
                        yaw_rotation,
                        speed_mm_s=speed_mm_s,
                        omit_tags=omit_tags,
                        attached_tag=attached_tag,
                        joint7_continuity_tolerance_rad=joint7_tolerance_rad,
                        # MoveIt's OMPL planner is brittle with orientation path
                        # constraints for fixed-position yaw goals. Keep yaw
                        # small and joint-continuous; translation owns the strict
                        # carried-upright path constraint.
                        path_orientation_constraint=None,
                        path_joint7_continuity=True,
                    )
                finally:
                    self._moveit_plan_progress_message_override = (
                        previous_progress_override
                    )
                planned_stages.append(planned)
                self._preview_moveit_stage_plan(planned, attached_pose)
            if place_position_m is not None:
                self._emit_progress(
                    f"Simulator validating place and retreat for {route_label}.",
                    phase="carry_route",
                    stage=name,
                    route_index=0,
                )
                post_carry_manual_stages = self._preflight_post_carry_place(
                    place_position_m,
                    rotation,
                    speed_mm_s=place_speed_mm_s,
                    retreat_position_m=np.array(
                        (*target_xy, transfer_z),
                        dtype=float,
                    ),
                    retreat_speed_mm_s=130.0,
                    released_tag=attached_tag,
                )
        except (SimulatorError, IKError) as route_error:
            self._restore_motion_snapshot(snapshot)
            self.stage_trace = list(snapshot["stage_trace"])
            self._emit_progress(
                f"MoveIt rejected {route_label}: {self._short_planning_error(route_error)}",
                phase="carry_route",
                stage=name,
                route_index=0,
                error=self._short_planning_error(route_error),
            )
            self._log(
                "moveit_carry_direct_rejected",
                stage=name,
                route_index=None,
                error=str(route_error),
                state=self._runtime_snapshot(),
            )
            raise CollisionPlanError(
                "MoveIt could not find a MuJoCo-validated direct held-object "
                f"carry plan: {route_error}"
            ) from route_error

        self._restore_motion_snapshot(snapshot)
        self.stage_trace = list(snapshot["stage_trace"])
        self._emit_progress(
            f"MoveIt accepted {route_label}.",
            phase="carry_route",
            stage=name,
            route_index=0,
            target_z_m=float(transfer_z),
        )
        self._log(
            "moveit_carry_direct_plan_success",
            stage=name,
            route_index=None,
            planned_stage_names=[stage.name for stage in planned_stages],
            state=self._runtime_snapshot(),
        )
        return MoveItCarryPlan(
            stage_name=name,
            route_index=0,
            stages=tuple(planned_stages),
            post_carry_manual_stages=tuple(post_carry_manual_stages or ()),
        )

    def _execute_moveit_carry_plan(self, carry_plan: MoveItCarryPlan) -> None:
        for planned in carry_plan.stages:
            self._execute_moveit_stage_plan(planned)
        self._log(
            "moveit_carry_direct_success",
            stage=carry_plan.stage_name,
            route_index=None,
            state=self._runtime_snapshot(),
        )

    def _preflight_post_carry_place(
        self,
        place_position_m: np.ndarray,
        rotation: np.ndarray,
        *,
        speed_mm_s: float,
        retreat_position_m: np.ndarray | None = None,
        retreat_speed_mm_s: float = 130.0,
        released_tag: str | None = None,
    ) -> tuple[ManualCartesianStagePlan, ...]:
        plans: list[ManualCartesianStagePlan] = [
            self._preview_cartesian_stage(
                "contact descend destination",
                place_position_m,
                rotation,
                speed_mm_s=speed_mm_s,
                allowed_tag=released_tag,
            )
        ]
        if retreat_position_m is None:
            return tuple(plans)
        self._gripper_position = GRIPPER_OPEN
        self.data.ctrl[ARM_DOF] = GRIPPER_OPEN
        if released_tag is not None and released_tag in self.scene.components:
            weld_id = self.model.equality(
                self.scene.components[released_tag].weld_name
            ).id
            self.data.eq_active[weld_id] = 0
            mujoco.mj_forward(self.model, self.data)
        plans.append(
            self._preview_cartesian_stage(
                "contact retreat",
                retreat_position_m,
                rotation,
                speed_mm_s=retreat_speed_mm_s,
                allowed_tag=None,
            )
        )
        return tuple(plans)

    def _attached_component_relative_pose(
        self,
        tag_id: str | None,
    ) -> tuple[int, np.ndarray, np.ndarray] | None:
        if tag_id is None or tag_id not in self.scene.components:
            return None
        spec = self.scene.components[tag_id]
        weld_id = self.model.equality(spec.weld_name).id
        if not bool(self.data.eq_active[weld_id]):
            return None
        mujoco.mj_forward(self.model, self.data)
        tcp_position = self.data.site("link_tcp").xpos.copy()
        tcp_rotation = self.data.site("link_tcp").xmat.reshape(3, 3).copy()
        object_position = self.data.body(spec.body_name).xpos.copy()
        object_rotation = self.data.body(spec.body_name).xmat.reshape(3, 3).copy()
        joint_id = self.model.joint(spec.joint_name).id
        return (
            int(self.model.jnt_qposadr[joint_id]),
            tcp_rotation.T @ (object_position - tcp_position),
            tcp_rotation.T @ object_rotation,
        )

    def _preview_moveit_stage_plan(
        self,
        planned: MoveItStagePlan,
        attached_pose: tuple[int, np.ndarray, np.ndarray] | None,
    ) -> None:
        self._preview_joint_target(planned.final_joints, attached_pose)

    def _preview_joint_target(
        self,
        joints: np.ndarray,
        attached_pose: tuple[int, np.ndarray, np.ndarray] | None = None,
    ) -> None:
        joints = np.asarray(joints, dtype=float)
        self.data.qpos[:ARM_DOF] = joints
        self.data.ctrl[:ARM_DOF] = joints
        self.current_joint_target = joints.copy()
        mujoco.mj_forward(self.model, self.data)
        if attached_pose is None:
            return
        qpos_addr, relative_position, relative_rotation = attached_pose
        tcp_position = self.data.site("link_tcp").xpos.copy()
        tcp_rotation = self.data.site("link_tcp").xmat.reshape(3, 3).copy()
        object_position = tcp_position + tcp_rotation @ relative_position
        object_rotation = tcp_rotation @ relative_rotation
        self.data.qpos[qpos_addr : qpos_addr + 3] = object_position
        self.data.qpos[qpos_addr + 3 : qpos_addr + 7] = _matrix_to_quat_wxyz(
            object_rotation
        )
        mujoco.mj_forward(self.model, self.data)

    def _preview_cartesian_stage(
        self,
        name: str,
        position_m: np.ndarray,
        rotation: np.ndarray,
        *,
        speed_mm_s: float,
        allowed_tag: str | None,
    ) -> ManualCartesianStagePlan:
        previous_allowed = self.allowed_collision_tag
        self.allowed_collision_tag = allowed_tag
        try:
            self._emit_progress(
                f"Simulator validating manual {name}.",
                phase="manual_validation",
                stage=name,
            )
            targets = self._cartesian_stage_targets(position_m, rotation)
            joint_waypoints, durations_s = self._plan_cartesian_path(
                targets,
                speed_mm_s=speed_mm_s,
            )
            attached_pose = self._attached_component_relative_pose(allowed_tag)
            self._preview_joint_target(joint_waypoints[-1], attached_pose)
            self._log(
                "manual_stage_preview_success",
                stage=name,
                allowed_tag=allowed_tag,
                target_mujoco_tcp_m=np.asarray(position_m, dtype=float),
                state=self._runtime_snapshot(),
            )
            return ManualCartesianStagePlan(
                name=name,
                position_m=np.asarray(position_m, dtype=float).copy(),
                rotation=np.asarray(rotation, dtype=float).copy(),
                joint_waypoints=tuple(
                    np.asarray(waypoint, dtype=float).copy()
                    for waypoint in joint_waypoints
                ),
                durations_s=tuple(float(duration) for duration in durations_s),
                speed_mm_s=float(speed_mm_s),
                allowed_tag=allowed_tag,
                gripper_position=float(self._gripper_position),
            )
        finally:
            self.allowed_collision_tag = previous_allowed

    def _preview_manual_pickup_to_lift(
        self,
        tag_id: str,
        *,
        source_xy: np.ndarray,
        source_rotation: np.ndarray,
        approach_z: float,
        grasp_z: float,
        lift_z: float,
    ) -> tuple[ManualCartesianStagePlan, ...]:
        spec = self.scene.components[tag_id]
        plans: list[ManualCartesianStagePlan] = [
            self._preview_cartesian_stage(
                "descend open",
                np.array((*source_xy, approach_z), dtype=float),
                source_rotation,
                speed_mm_s=120.0,
                allowed_tag=None,
            ),
            self._preview_cartesian_stage(
                "lower around box",
                np.array((*source_xy, grasp_z), dtype=float),
                source_rotation,
                speed_mm_s=70.0,
                allowed_tag=tag_id,
            ),
        ]
        self._gripper_position = GRIPPER_CLOSED
        self.data.ctrl[ARM_DOF] = GRIPPER_CLOSED
        self._validate_grasp_envelope(spec)
        weld_id = self.model.equality(spec.weld_name).id
        self.data.eq_active[weld_id] = 1
        mujoco.mj_forward(self.model, self.data)
        plans.append(
            self._preview_cartesian_stage(
                "pickup clearance lift",
                np.array((*source_xy, lift_z), dtype=float),
                source_rotation,
                speed_mm_s=70.0,
                allowed_tag=tag_id,
            )
        )
        return tuple(plans)

    def _motion_snapshot(self) -> Dict[str, Any]:
        return {
            "qpos": self.data.qpos.copy(),
            "ctrl": self.data.ctrl.copy(),
            "eq_active": self.data.eq_active.copy(),
            "current_joint_target": self.current_joint_target.copy(),
            "gripper_position": float(self._gripper_position),
            "stage_trace": list(self.stage_trace),
        }

    def _restore_motion_snapshot(self, snapshot: Mapping[str, Any]) -> None:
        self.data.qpos[:] = np.asarray(snapshot["qpos"], dtype=float)
        self.data.ctrl[:] = np.asarray(snapshot["ctrl"], dtype=float)
        self.data.eq_active[:] = np.asarray(snapshot["eq_active"], dtype=int)
        self.current_joint_target = np.asarray(
            snapshot["current_joint_target"],
            dtype=float,
        ).copy()
        self._gripper_position = float(snapshot["gripper_position"])
        mujoco.mj_forward(self.model, self.data)

    def _object_pose(self, tag_id: str) -> tuple[np.ndarray, float]:
        spec = self.scene.components[tag_id]
        body = self.data.body(spec.body_name)
        position = body.xpos.copy()
        rotation = body.xmat.reshape(3, 3).copy()
        yaw = math.degrees(math.atan2(rotation[1, 0], rotation[0, 0]))
        return position, yaw

    def _placement_result(
        self,
        tag_id: str,
        *,
        target_xy: np.ndarray,
        target_rotation_deg: float,
        position_tolerance_mm: float,
        allow_yaw_180_equivalence: bool,
        enforce_tolerance: bool,
        update_moveit_lab_yaw: bool = False,
    ) -> MoveResult:
        final_position, final_yaw = self._object_pose(tag_id)
        position_error_mm = float(
            np.linalg.norm(final_position[:2] - target_xy) * 1000.0
        )
        yaw_candidates = [float(final_yaw)]
        if allow_yaw_180_equivalence:
            yaw_candidates.extend((float(final_yaw) + 180.0, float(final_yaw) - 180.0))
        reported_yaw = min(
            yaw_candidates,
            key=lambda candidate: _angle_error_deg(candidate, target_rotation_deg),
        )
        yaw_error_deg = _angle_error_deg(reported_yaw, target_rotation_deg)
        if (
            position_error_mm > float(position_tolerance_mm)
            or yaw_error_deg > PLACEMENT_YAW_TOLERANCE_DEG
        ):
            message = (
                f"placement tolerance exceeded for {tag_id}: "
                f"position error={position_error_mm:.2f} mm, "
                f"yaw error={yaw_error_deg:.2f} deg"
            )
            if enforce_tolerance:
                raise SimulatorError(message.replace("exceeded", "failed"))
            self.stage_trace.append(message)
        if update_moveit_lab_yaw:
            self._moveit_lab_yaw_by_tag[tag_id] = float(reported_yaw)
        return MoveResult(
            tag_id=tag_id,
            x_mm=float(final_position[0] * 1000.0),
            y_mm=float(final_position[1] * 1000.0),
            rotation_deg=float(reported_yaw),
            stage_trace=tuple(self.stage_trace),
        )

    def pick_and_place(
        self,
        tag_id: str,
        *,
        target_x_mm: float,
        target_y_mm: float,
        target_rotation_deg: float,
    ) -> MoveResult:
        self._log(
            "pick_and_place_start",
            tag_id=tag_id,
            target={
                "x_mm": float(target_x_mm),
                "y_mm": float(target_y_mm),
                "rotation_deg": float(target_rotation_deg),
            },
            planner_backend=self.planner_backend,
            state=self._runtime_snapshot(),
        )
        try:
            if self.planner_backend == MUJOCO_PLANNER_MOVEIT:
                result = self._pick_and_place_moveit(
                    tag_id,
                    target_x_mm=target_x_mm,
                    target_y_mm=target_y_mm,
                    target_rotation_deg=target_rotation_deg,
                )
            else:
                result = self._pick_and_place_custom_ik(
                    tag_id,
                    target_x_mm=target_x_mm,
                    target_y_mm=target_y_mm,
                    target_rotation_deg=target_rotation_deg,
                )
        except Exception as exc:  # noqa: BLE001
            self._log(
                "pick_and_place_failure",
                tag_id=tag_id,
                error=str(exc),
                traceback=traceback.format_exc(),
                stage_trace=list(self.stage_trace),
                state=self._runtime_snapshot(),
                log_path=self.diagnostic_log_path(),
            )
            raise
        self._log(
            "pick_and_place_complete",
            tag_id=tag_id,
            result=result.as_dict(),
            state=self._runtime_snapshot(),
        )
        return result

    def _pick_and_place_custom_ik(
        self,
        tag_id: str,
        *,
        target_x_mm: float,
        target_y_mm: float,
        target_rotation_deg: float,
    ) -> MoveResult:
        if tag_id not in self.scene.components:
            raise SimulatorError(f"MuJoCo body not found for {tag_id}")
        self.stage_trace = []
        spec = self.scene.components[tag_id]
        object_position, source_yaw = self._object_pose(tag_id)
        source_xy = object_position[:2]
        target_xy = np.array((target_x_mm / 1000.0, target_y_mm / 1000.0))
        source_rotation = self._target_rotation(source_yaw)
        target_rotation = self._target_rotation(target_rotation_deg)
        grasp_z = spec.grasp_tcp_z_m
        release_z = grasp_z + RELEASE_CLEARANCE_M
        approach_z = max(grasp_z + APPROACH_CLEARANCE_M, 0.26)
        clearance_z = self._travel_clearance_z(spec)
        self._validate_destination(spec, target_xy)
        self._preflight_waypoints(
            (
                (np.array((*source_xy, clearance_z)), source_rotation),
                (np.array((*source_xy, approach_z)), source_rotation),
                (np.array((*source_xy, grasp_z)), source_rotation),
                (np.array((*source_xy, clearance_z)), source_rotation),
                (np.array((*target_xy, clearance_z)), target_rotation),
                (np.array((*target_xy, release_z)), target_rotation),
                (np.array((*target_xy, clearance_z)), target_rotation),
            ),
            allowed_tag=tag_id,
        )
        arm = XArmAPI(runtime=self)
        arm.motion_enable(True)
        arm.set_mode(0)
        arm.set_state(0)
        arm.set_gripper_enable(True)
        arm.set_gripper_speed(2000)

        self.allowed_collision_tag = tag_id
        self.stage_trace.append("open gripper")
        arm.set_gripper_position(GRIPPER_OPEN, wait=True)
        self._move_stage(
            "move above source",
            arm,
            np.array((*source_xy, clearance_z)),
            source_rotation,
            speed_mm_s=180.0,
        )
        self._move_stage(
            "descend open",
            arm,
            np.array((*source_xy, approach_z)),
            source_rotation,
            speed_mm_s=120.0,
        )
        self._move_stage(
            "lower around box",
            arm,
            np.array((*source_xy, grasp_z)),
            source_rotation,
            speed_mm_s=70.0,
        )
        self.stage_trace.append("close while stationary")
        arm.set_gripper_position(GRIPPER_CLOSED, wait=True)
        self._validate_grasp_envelope(spec)
        weld_id = self.model.equality(spec.weld_name).id
        self.data.eq_active[weld_id] = 1
        self.stage_trace.append("activate assisted weld")
        self._settle(0.08)
        self._move_stage(
            "lift",
            arm,
            np.array((*source_xy, clearance_z)),
            source_rotation,
            speed_mm_s=135.0,
        )
        self._move_stage(
            "translate",
            arm,
            np.array((*target_xy, clearance_z)),
            target_rotation,
            speed_mm_s=170.0,
        )
        self._move_stage(
            "descend destination",
            arm,
            np.array((*target_xy, release_z)),
            target_rotation,
            speed_mm_s=80.0,
        )
        self.stage_trace.append("open and release")
        arm.set_gripper_position(GRIPPER_OPEN, wait=True)
        self.stage_trace.append("deactivate assisted weld")
        self.data.eq_active[weld_id] = 0
        self._settle(0.35)
        self._move_stage(
            "retreat",
            arm,
            np.array((*target_xy, clearance_z)),
            target_rotation,
            speed_mm_s=130.0,
        )
        self.allowed_collision_tag = None
        self._settle(0.45)
        self._return_to_observation_home()
        return self._placement_result(
            tag_id,
            target_xy=target_xy,
            target_rotation_deg=target_rotation_deg,
            position_tolerance_mm=PLACEMENT_POSITION_TOLERANCE_MM,
            allow_yaw_180_equivalence=False,
            enforce_tolerance=True,
        )

    def _pick_and_place_moveit(
        self,
        tag_id: str,
        *,
        target_x_mm: float,
        target_y_mm: float,
        target_rotation_deg: float,
    ) -> MoveResult:
        if tag_id not in self.scene.components:
            raise SimulatorError(f"MuJoCo body not found for {tag_id}")
        self.stage_trace = []
        spec = self.scene.components[tag_id]
        object_position, source_yaw = self._object_pose(tag_id)
        source_yaw = float(self._moveit_lab_yaw_by_tag.get(tag_id, source_yaw))
        source_xy = object_position[:2]
        target_xy = np.array((target_x_mm / 1000.0, target_y_mm / 1000.0))
        source_rotation = self._target_rotation(source_yaw)
        target_rotation = self._moveit_equivalent_target_rotation(
            target_rotation_deg,
            reference_rotation=source_rotation,
        )
        grasp_z = spec.grasp_tcp_z_m
        release_z = grasp_z + RELEASE_CLEARANCE_M
        approach_z = max(grasp_z + APPROACH_CLEARANCE_M, 0.26)
        transfer_z = approach_z
        self._validate_destination(spec, target_xy)
        omitted_target = {tag_id}
        prepick_plan = (
            self._plan_moveit_pre_pick_feasibility(
                tag_id,
                source_xy=source_xy,
                target_xy=target_xy,
                source_rotation=source_rotation,
                target_rotation=target_rotation,
                approach_z=approach_z,
                grasp_z=grasp_z,
                transfer_z=transfer_z,
                release_z=release_z,
                omitted_target=omitted_target,
            )
            if self.moveit is not None
            else None
        )
        manual_pickup_plans = (
            {plan.name: plan for plan in prepick_plan.manual_pickup_stages}
            if prepick_plan is not None
            else {}
        )
        post_carry_plans = (
            {plan.name: plan for plan in prepick_plan.carry.post_carry_manual_stages}
            if prepick_plan is not None
            else {}
        )

        arm = XArmAPI(runtime=self)
        arm.motion_enable(True)
        arm.set_mode(0)
        arm.set_state(0)
        arm.set_gripper_enable(True)
        arm.set_gripper_speed(2000)

        self.allowed_collision_tag = None
        self.stage_trace.append("open gripper")
        arm.set_gripper_position(GRIPPER_OPEN, wait=True)
        if prepick_plan is not None:
            self._execute_moveit_stage_plan(prepick_plan.above_source)
        else:
            self._moveit_move_stage(
                "moveit move above source",
                np.array((*source_xy, transfer_z)),
                source_rotation,
                speed_mm_s=180.0,
                omit_tags=omitted_target,
            )
        cached_stage = manual_pickup_plans.get("descend open")
        if cached_stage is not None:
            self._execute_cartesian_stage_plan(cached_stage, arm)
        else:
            self._move_stage(
                "descend open",
                arm,
                np.array((*source_xy, approach_z)),
                source_rotation,
                speed_mm_s=120.0,
            )
        self.allowed_collision_tag = tag_id
        cached_stage = manual_pickup_plans.get("lower around box")
        if cached_stage is not None:
            self._execute_cartesian_stage_plan(cached_stage, arm)
        else:
            self._move_stage(
                "lower around box",
                arm,
                np.array((*source_xy, grasp_z)),
                source_rotation,
                speed_mm_s=70.0,
            )
        self.stage_trace.append("close while stationary")
        arm.set_gripper_position(GRIPPER_CLOSED, wait=True)
        self._validate_grasp_envelope(spec)
        weld_id = self.model.equality(spec.weld_name).id
        self.data.eq_active[weld_id] = 1
        self.stage_trace.append("activate assisted weld")
        self.stage_trace.append("settle contact grasp")
        self._settle(0.30)
        cached_stage = manual_pickup_plans.get("pickup clearance lift")
        if cached_stage is not None:
            self._execute_cartesian_stage_plan(cached_stage, arm)
        else:
            self._move_stage(
                "pickup clearance lift",
                arm,
                np.array((*source_xy, transfer_z)),
                source_rotation,
                speed_mm_s=70.0,
            )
        if prepick_plan is not None:
            self._execute_moveit_carry_plan(prepick_plan.carry)
        else:
            self._moveit_carry_stage(
                "moveit translate",
                source_xy=source_xy,
                target_xy=target_xy,
                transfer_z=transfer_z,
                rotation=target_rotation,
                speed_mm_s=170.0,
                omit_tags=omitted_target,
                attached_tag=tag_id,
                source_rotation=source_rotation,
                place_position_m=np.array((*target_xy, release_z)),
                place_speed_mm_s=80.0,
            )
        # Placing is a contact operation: MoveIt gets the arm/object to the
        # pre-place pose, then the controller performs the short vertical lower.
        # This keeps the simulator path aligned with the future physical path,
        # where gripper release and support contact stay outside the planner.
        cached_stage = post_carry_plans.get("contact descend destination")
        if cached_stage is not None:
            self._execute_cartesian_stage_plan(cached_stage, arm)
        else:
            self._move_stage(
                "contact descend destination",
                arm,
                np.array((*target_xy, release_z)),
                target_rotation,
                speed_mm_s=80.0,
            )
        self.stage_trace.append("open and release")
        arm.set_gripper_position(GRIPPER_OPEN, wait=True)
        self.stage_trace.append("deactivate assisted weld")
        self.data.eq_active[weld_id] = 0
        self._settle(0.35)
        self.allowed_collision_tag = None
        cached_stage = post_carry_plans.get("contact retreat")
        if cached_stage is not None:
            self._execute_cartesian_stage_plan(cached_stage, arm)
        else:
            self._move_stage(
                "contact retreat",
                arm,
                np.array((*target_xy, transfer_z)),
                target_rotation,
                speed_mm_s=130.0,
            )
        self._settle(0.45)
        self._return_to_observation_home()
        return self._placement_result(
            tag_id,
            target_xy=target_xy,
            target_rotation_deg=target_rotation_deg,
            position_tolerance_mm=MOVEIT_PLACEMENT_POSITION_TOLERANCE_MM,
            allow_yaw_180_equivalence=True,
            enforce_tolerance=False,
            update_moveit_lab_yaw=True,
        )

    def _validate_grasp_envelope(self, spec: ComponentSpec) -> None:
        mujoco.mj_forward(self.model, self.data)
        tcp = self.data.site("link_tcp").xpos.copy()
        object_site = self.data.site(spec.site_name).xpos.copy()
        lateral = float(np.linalg.norm((tcp - object_site)[:2]))
        vertical = abs(float(tcp[2] - object_site[2]))
        if lateral > 0.025 or vertical > 0.035:
            raise SimulatorError(
                f"grasp envelope failed for {spec.tag_id}: "
                f"lateral={lateral * 1000.0:.1f} mm, "
                f"vertical={vertical * 1000.0:.1f} mm"
            )


def simulation_process_main(
    scene: SceneSpec,
    command_queue: Any,
    result_queue: Any,
    *,
    show_viewer: bool,
    realtime: bool,
    planner_backend: str = MUJOCO_PLANNER_CUSTOM_IK,
) -> None:
    runtime: Optional[MuJoCoRobotRuntime] = None
    try:
        runtime = MuJoCoRobotRuntime(
            scene,
            show_viewer=show_viewer,
            realtime=realtime,
            planner_backend=planner_backend,
            progress_callback=lambda event: result_queue.put(dict(event)),
        )
        result_queue.put({"type": "ready", "log_path": runtime.diagnostic_log_path()})
        while runtime.viewer_running():
            try:
                command = command_queue.get_nowait()
            except queue.Empty:
                runtime._step()
                continue
            command_type = command.get("type")
            if command_type == "shutdown":
                result_queue.put({"type": "shutdown_complete"})
                return
            if command_type != "move_component":
                result_queue.put(
                    {
                        "type": "error",
                        "request_id": command.get("request_id"),
                        "error": f"unknown simulator command {command_type!r}",
                    }
                )
                continue
            request_id = command.get("request_id")
            runtime.active_request_id = str(request_id or "")
            runtime._log(
                "command_received",
                command={
                    key: value
                    for key, value in command.items()
                    if key != "type"
                },
            )
            try:
                result = runtime.pick_and_place(
                    str(command["tag_id"]),
                    target_x_mm=float(command["target_x_mm"]),
                    target_y_mm=float(command["target_y_mm"]),
                    target_rotation_deg=float(command["target_rotation_deg"]),
                )
                result_queue.put(
                    {
                        "type": "move_complete",
                        "request_id": request_id,
                        "log_path": runtime.diagnostic_log_path(),
                        "result": result.as_dict(),
                    }
                )
            except Exception as exc:  # noqa: BLE001
                result_queue.put(
                    {
                        "type": "error",
                        "request_id": request_id,
                        "error": str(exc),
                        "traceback": traceback.format_exc(),
                        "log_path": runtime.diagnostic_log_path(),
                        "stage_trace": list(runtime.stage_trace),
                    }
                )
            finally:
                runtime.active_request_id = None
        result_queue.put({"type": "viewer_closed", "error": "MuJoCo viewer was closed"})
    except Exception as exc:  # noqa: BLE001
        result_queue.put(
            {
                "type": "startup_error" if runtime is None else "runtime_error",
                "error": str(exc),
                "traceback": traceback.format_exc(),
            }
        )
    finally:
        if runtime is not None:
            runtime.close()
