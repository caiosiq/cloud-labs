"""Long-running MuJoCo runtime and process entrypoint."""

from __future__ import annotations

import heapq
import json
import math
import os
import queue
import time
import traceback
from collections.abc import Sequence as AbcSequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Sequence

import mujoco
import mujoco.viewer
import numpy as np

from simulation_edge.host.ik import ARM_DOF, DampedLeastSquaresIK, IKError
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
REAL_PICKUP_CAMERA_APPROACH_Z_M = 0.500
REAL_PICKUP_MIN_RETRACT_Z_M = 0.050
REAL_PICKUP_RETRACT_DELTA_M = 0.250
REAL_PICKUP_CAMERA_PREOFFSET_M = (-0.0700, -0.0350)
REAL_PICKUP_CAMERA_TO_GRIPPER_OFFSET_M = (0.0742, 0.0355)
REAL_PICKUP_EDGE_X_THRESHOLD_M = 0.300
REAL_PICKUP_EDGE_X_NUDGE_M = 0.010
# Put the gripper inward of the camera on the right side. The other three side
# approaches are exact quarter turns of this canonical pose.
REAL_PICKUP_CANONICAL_RIGHT_CAMERA_YAW_DEG = -25.6
STORAGE_PICKUP_CAMERA_YAW_DEG = -160.6
REAL_PICKUP_CALIB_Y_LOWER_MM = -345.884674
REAL_PICKUP_CALIB_Y_UPPER_MM = 88.222145
REAL_PICKUP_CALIB_FINE_ADJUST_COEFF = (
    -4.0 / (REAL_PICKUP_CALIB_Y_UPPER_MM - REAL_PICKUP_CALIB_Y_LOWER_MM) * 0.75
)
REAL_PICKUP_CALIB_FINE_ADJUST_COEFF_X = -0.01
REAL_PICKUP_CALIB_X_OFFSET_MM = 379.8
MOTION_TIME_SCALE_ENV_VAR = "CLOUDLAB_MOTION_TIME_SCALE"
MOTION_TIME_SCALE = 1.0
MUJOCO_VIEWER_SYNC_HZ_ENV_VAR = "CLOUDLAB_MUJOCO_VIEWER_SYNC_HZ"
MUJOCO_VIEWER_SYNC_HZ = 60.0
ARM_SERVO_STIFFNESS_SCALE = 2.0
GRIPPER_FORCE_LIMIT_N = 65.0
GRIPPER_PAD_SLIDING_FRICTION = 3.0
PLACEMENT_POSITION_TOLERANCE_MM = 5.0
RADIAL_PLACEMENT_POSITION_TOLERANCE_MM = 8.0
PLACEMENT_YAW_TOLERANCE_DEG = 2.0
ARM_JOINT_LIMIT_MARGIN_RAD = 0.010
ARM_JOINT_LIMITS_RAD = (
    (-math.pi * 0.99, math.pi * 0.99),
    (-2.18, 2.18),
    (-math.pi * 0.99, math.pi * 0.99),
    (-0.11, math.pi * 0.99),
    (-math.pi * 0.99, math.pi * 0.99),
    (-1.75, math.pi * 0.99),
    (-math.pi * 0.99, math.pi * 0.99),
)
MUJOCO_PLANNER_CUSTOM_IK = "custom_ik"
MUJOCO_PLANNER_RADIAL = "radial"
RADIAL_LIBRARY_VERSION = 3
RADIAL_LIBRARY_DIR_ENV_VAR = "CLOUDLAB_RADIAL_LIBRARY_DIR"
RADIAL_LIBRARY_FILE_ENV_VAR = "CLOUDLAB_RADIAL_LIBRARY_FILE"
RADIAL_MIN_RADIUS_MM_ENV_VAR = "CLOUDLAB_RADIAL_MIN_RADIUS_MM"
RADIAL_MAX_RADIUS_MM_ENV_VAR = "CLOUDLAB_RADIAL_MAX_RADIUS_MM"
RADIAL_STEP_MM_ENV_VAR = "CLOUDLAB_RADIAL_STEP_MM"
RADIAL_CARRY_Z_M_ENV_VAR = "CLOUDLAB_RADIAL_CARRY_Z_M"
RADIAL_HEIGHT_ZONE_MARGIN_M_ENV_VAR = "CLOUDLAB_RADIAL_HEIGHT_ZONE_MARGIN_M"
RADIAL_HEIGHT_ZONE_MIN_RADIUS_MM_ENV_VAR = "CLOUDLAB_RADIAL_HEIGHT_ZONE_MIN_RADIUS_MM"
RADIAL_DEFAULT_MIN_RADIUS_MM = 134.0
RADIAL_DEFAULT_MAX_RADIUS_MM = 513.0
RADIAL_DEFAULT_STEP_MM = 5.0
RADIAL_DEFAULT_CARRY_Z_M = 0.550
RADIAL_DEFAULT_HEIGHT_ZONE_MARGIN_M = 0.0
RADIAL_DEFAULT_GRASP_Z_M = 0.300
RADIAL_DEFAULT_MAX_VERTICAL_Z_M = 0.550
RADIAL_VERTICAL_STEP_MM = 10.0
RADIAL_BASE_ROTATION_STEP_DEG = 12.0
RADIAL_ROTATION_FINAL_DURATION_SCALE = 2.0
RADIAL_CARRIED_SMOOTH_DURATION_SCALE = 1.5
RADIAL_TRANSLATION_STEP_MM = 25.0
RADIAL_XY_IK_STEP_M = 0.025
RADIAL_OUTER_CARRY_Z_M = 0.416
RADIAL_OUTER_ROTATION_RADIUS_M = 0.330
RADIAL_NO_WELD_BOUNDARY_BUFFER_M = 0.020
RADIAL_OUTER_PORTAL_ANGLE_STEP_DEG = 5.0
RADIAL_OUTER_PORTAL_MAX_OFFSET_DEG = 45.0
RADIAL_OUTER_XY_STEP_MM = 20.0
RADIAL_OUTER_GRID_STEP_MM = 25.0
RADIAL_OUTER_SEARCH_MARGIN_M = 0.150
RADIAL_OUTER_MAX_SEARCH_EXPANSIONS = 500
RADIAL_OBSERVATION_HOME_RADIUS_M = 0.193
RADIAL_OBSERVATION_HOME_THETA_DEG = 0.0
RADIAL_OBSERVATION_HOME_Z_M = 0.550
RADIAL_OBSERVATION_HOME_YAW_DEG = 0.0
RADIAL_JOINT_PATH_MIN_SAMPLE_COUNT = 3
RADIAL_JOINT_PATH_MAX_SAMPLE_COUNT = 24
RADIAL_JOINT_PATH_MAX_STEP_RAD = math.radians(3.0)
RADIAL_JOINT_SPEED_RAD_PER_S = 0.65
RADIAL_MIN_SEGMENT_DURATION_S = 0.20
RADIAL_MAX_SEGMENT_DURATION_S = 2.0
RADIAL_TCP_POSITION_TOLERANCE_M = 0.0005
RADIAL_TCP_ROTATION_TOLERANCE_DEG = 0.25
RADIAL_FIXED_JOINT_TOLERANCE_RAD = 1e-8
RADIAL_MAX_WRIST_STAGE_TRAVEL_RAD = math.pi + 1e-6
LOG_ENV_VAR = "CLOUDLAB_MUJOCO_LOG_DIR"
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
RADIAL_HEIGHT_ZONE_EXEMPT_BODIES = GRIPPER_COMPONENT_TOUCH_BODIES | {
    "link7",
    "link_base",
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


@dataclass(frozen=True)
class PickupQuarterFrame:
    name: str
    rotation_deg: float
    inward_xy: tuple[float, float]


def _pickup_quarter_frame(xy_m: np.ndarray) -> PickupQuarterFrame:
    x_m, y_m = np.asarray(xy_m, dtype=float)[:2]
    if x_m >= abs(y_m):
        return PickupQuarterFrame("right", 0.0, (-1.0, 0.0))
    if y_m >= abs(x_m):
        return PickupQuarterFrame("top", 90.0, (0.0, -1.0))
    if -x_m >= abs(y_m):
        return PickupQuarterFrame("left", 180.0, (1.0, 0.0))
    return PickupQuarterFrame("bottom", -90.0, (0.0, 1.0))


def _rotate_xy_deg(vector: Sequence[float], yaw_deg: float) -> np.ndarray:
    yaw_rad = math.radians(float(yaw_deg))
    c, s = math.cos(yaw_rad), math.sin(yaw_rad)
    x_value, y_value = np.asarray(vector, dtype=float)[:2]
    return np.array(
        (c * x_value - s * y_value, s * x_value + c * y_value),
        dtype=float,
    )


def _short_edge_grasp_yaw(
    source_yaw_deg: float,
    preferred_yaw_deg: float,
) -> tuple[float, float]:
    """Close the jaws along the component's longer local X dimension.

    The fingers therefore contact the grooved short end faces. The two
    orientations separated by 180 degrees are physically equivalent; choose
    the one requiring less wrist rotation from the camera-approach pose.
    """
    candidates = [
        float(source_yaw_deg) - 90.0,
        float(source_yaw_deg) + 90.0,
    ]
    chosen = min(
        candidates,
        key=lambda yaw: (
            _angle_error_deg(yaw, preferred_yaw_deg),
            abs(yaw - float(source_yaw_deg)),
        ),
    )
    offset = (chosen - float(source_yaw_deg) + 180.0) % 360.0 - 180.0
    return float(chosen), float(offset)


def _manual_motion_workspace_allows_xy(
    xy_mm: np.ndarray,
    corner_cutoff_mm: float,
) -> bool:
    cutoff = float(corner_cutoff_mm)
    if cutoff <= 0.0:
        return True
    x_mm, y_mm = np.asarray(xy_mm, dtype=float)[:2]
    return bool(abs(float(x_mm)) <= cutoff or abs(float(y_mm)) <= cutoff)


def _radial_joint_path_sample_count(
    start: np.ndarray,
    end: np.ndarray,
) -> int:
    max_delta = float(
        np.max(np.abs(np.asarray(end, dtype=float) - np.asarray(start, dtype=float)))
    )
    required = int(math.ceil(max_delta / RADIAL_JOINT_PATH_MAX_STEP_RAD)) + 1
    return int(
        np.clip(
            required,
            RADIAL_JOINT_PATH_MIN_SAMPLE_COUNT,
            RADIAL_JOINT_PATH_MAX_SAMPLE_COUNT,
        )
    )


def _canonical_equivalent_angle_rad(
    value: float,
    *,
    lower: float | None = None,
    upper: float | None = None,
) -> float:
    canonical = math.atan2(math.sin(float(value)), math.cos(float(value)))
    if lower is None or upper is None:
        return float(canonical)
    full_turn = 2.0 * math.pi
    min_turns = math.ceil((float(lower) - canonical - 1e-9) / full_turn)
    max_turns = math.floor((float(upper) - canonical + 1e-9) / full_turn)
    if min_turns > max_turns:
        raise CollisionPlanError(
            f"angle {value:.6f} rad has no equivalent representation inside "
            f"limits [{float(lower):.6f}, {float(upper):.6f}] rad"
        )
    return float(
        min(
            (canonical + turns * full_turn for turns in range(min_turns, max_turns + 1)),
            key=abs,
        )
    )


def _radial_joint_targets_equivalent(
    first: np.ndarray,
    second: np.ndarray,
    *,
    atol: float,
) -> bool:
    delta = np.asarray(first, dtype=float) - np.asarray(second, dtype=float)
    delta = delta.copy()
    for joint_index in (0, 6):
        delta[joint_index] = math.atan2(
            math.sin(float(delta[joint_index])),
            math.cos(float(delta[joint_index])),
        )
    return bool(np.all(np.abs(delta) <= float(atol)))


def _radial_joint_path_travel_rad(
    waypoints: Sequence[np.ndarray],
    joint_index: int,
) -> float:
    return float(
        sum(
            abs(float(end[joint_index]) - float(start[joint_index]))
            for start, end in zip(waypoints, waypoints[1:])
        )
    )


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
class RealPickupAdapterTargets:
    camera_xy: np.ndarray
    aligned_camera_xy: np.ndarray
    gripper_xy: np.ndarray
    camera_rotation: np.ndarray
    grasp_rotation: np.ndarray
    base_joint_rad: float
    approach_z: float
    grasp_z: float
    retract_z: float
    x_adjust_m: float
    grasp_yaw_offset_deg: float
    workspace_quarter: str = "center"
    camera_yaw_deg: float = 0.0
    quarter_oriented: bool = False
    base_first: bool = False
    radial_inner_flip: bool = False


@dataclass(frozen=True)
class ManualPickupStageSpec:
    name: str
    position_m: np.ndarray
    rotation: np.ndarray
    speed_mm_s: float
    allowed_tag: str | None


@dataclass(frozen=True)
class RadialHeightZoneReport:
    ok: bool
    min_clearance_m: float
    body: str | None = None
    geom: str | None = None
    point_m: np.ndarray | None = None
    z_limit_m: float = 0.0
    min_radius_m: float = 0.0


@dataclass(frozen=True)
class RadialFrameBoundaryReport:
    ok: bool
    min_clearance_m: float
    body: str | None = None
    geom: str | None = None
    side: str | None = None
    point_m: np.ndarray | None = None


@dataclass(frozen=True)
class RadialVerticalPose:
    z_m: float
    joints: np.ndarray
    min_clearance_m: float
    tcp_error_m: float


@dataclass(frozen=True)
class RadialPoseSample:
    radius_m: float
    joints: np.ndarray
    min_clearance_m: float
    tcp_error_m: float
    vertical_poses: tuple[RadialVerticalPose, ...]


@dataclass(frozen=True)
class RadialMotionLibrary:
    version: int
    profile_id: str
    carry_z_m: float
    grasp_z_m: float
    max_vertical_z_m: float
    min_radius_m: float
    max_radius_m: float
    step_m: float
    component_height_limit_m: float
    height_zone_min_radius_m: float
    height_zone_margin_m: float
    samples: tuple[RadialPoseSample, ...]
    unsafe: tuple[Mapping[str, Any], ...] = ()

    @property
    def radius_bounds_m(self) -> tuple[float, float]:
        radii = [sample.radius_m for sample in self.samples]
        return (float(min(radii)), float(max(radii)))


@dataclass(frozen=True)
class RadialJointStagePlan:
    name: str
    waypoints: tuple[np.ndarray, ...]
    allowed_tag: str | None


class MuJoCoRobotRuntime:
    """Owns one compiled model, physics state, viewer, and IK solver."""

    def __init__(
        self,
        scene: SceneSpec,
        *,
        show_viewer: bool,
        realtime: bool,
        planner_backend: str = MUJOCO_PLANNER_RADIAL,
        progress_callback: Callable[[Mapping[str, Any]], None] | None = None,
    ) -> None:
        self.scene = scene
        self.planner_backend = str(planner_backend or MUJOCO_PLANNER_RADIAL)
        self._progress_callback = progress_callback
        self.diagnostics = JsonlDiagnostics(
            scene=scene,
            planner_backend=self.planner_backend,
        )
        self.active_request_id: str | None = None
        if self.planner_backend not in {
            MUJOCO_PLANNER_CUSTOM_IK,
            MUJOCO_PLANNER_RADIAL,
        }:
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
        self.model.actuator_forcerange[ARM_DOF] = (
            -GRIPPER_FORCE_LIMIT_N,
            GRIPPER_FORCE_LIMIT_N,
        )
        for geom_name in (
            "left_finger_pad_1",
            "left_finger_pad_2",
            "right_finger_pad_1",
            "right_finger_pad_2",
        ):
            geom_id = self.model.geom(geom_name).id
            self.model.geom_friction[geom_id, 0] = GRIPPER_PAD_SLIDING_FRICTION
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
        self.current_joint_target = initial_joint_target.copy()
        self.ik = DampedLeastSquaresIK()
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
        self._radial_height_zone_geom_ids = self._robot_height_zone_geom_ids()
        self._radial_frame_boundary_geom_ids = self._robot_geom_ids()
        self._radial_frame_boundary_local_points = {
            geom_id: self._geom_local_boundary_points(geom_id)
            for geom_id in self._radial_frame_boundary_geom_ids
        }
        self._radial_motion_library: RadialMotionLibrary | None = None
        if self.planner_backend == MUJOCO_PLANNER_RADIAL:
            initial_joint_target = self._radial_observation_home_joints(
                seed=self.current_joint_target,
            )
            self.current_joint_target = initial_joint_target.copy()
            self.data.qpos[:ARM_DOF] = initial_joint_target
            self.data.ctrl[:ARM_DOF] = initial_joint_target
            mujoco.mj_forward(self.model, self.data)
            self._log(
                "radial_observation_home_selected",
                keyframe_home_joints=self.home,
                observation_home_joints=initial_joint_target,
                observation_home_tcp_m=self.data.site("link_tcp").xpos.copy(),
            )
        self._settle(0.25)
        self._log(
            "runtime_ready",
            keyframe_home_joints=self.home.tolist(),
            observation_home_joints=self.current_joint_target.tolist(),
            initial_joint_target=self.current_joint_target.tolist(),
            initial_tcp_m=self.data.site("link_tcp").xpos.copy(),
            component_ids=sorted(self.scene.components),
            gripper_force_limit_n=GRIPPER_FORCE_LIMIT_N,
            gripper_pad_sliding_friction=GRIPPER_PAD_SLIDING_FRICTION,
            gripper_force_range_n=self.model.actuator_forcerange[ARM_DOF].copy(),
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
        if self.planner_backend == MUJOCO_PLANNER_RADIAL:
            self._preflight_radial_joint_path(
                self.current_joint_target,
                target_joints,
                allowed_tag=self.allowed_collision_tag,
                stage="manual cartesian pose",
            )
        else:
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
            except IKError:
                raise
            if self.planner_backend == MUJOCO_PLANNER_RADIAL:
                self._preflight_radial_joint_path(
                    actual_joints,
                    correction_joints,
                    allowed_tag=self.allowed_collision_tag,
                    stage="manual cartesian correction",
                )
            else:
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

    def _radial_min_radius_m(self) -> float:
        return _env_float(
            RADIAL_MIN_RADIUS_MM_ENV_VAR,
            RADIAL_DEFAULT_MIN_RADIUS_MM,
            minimum=1.0,
            maximum=1000.0,
        ) / 1000.0

    def _radial_max_radius_m(self) -> float:
        return _env_float(
            RADIAL_MAX_RADIUS_MM_ENV_VAR,
            RADIAL_DEFAULT_MAX_RADIUS_MM,
            minimum=1.0,
            maximum=1000.0,
        ) / 1000.0

    def _radial_step_m(self) -> float:
        return _env_float(
            RADIAL_STEP_MM_ENV_VAR,
            RADIAL_DEFAULT_STEP_MM,
            minimum=0.5,
            maximum=50.0,
        ) / 1000.0

    def _radial_carry_z_m(self) -> float:
        return _env_float(
            RADIAL_CARRY_Z_M_ENV_VAR,
            RADIAL_DEFAULT_CARRY_Z_M,
            minimum=0.10,
            maximum=1.00,
        )

    def _radial_height_zone_margin_m(self) -> float:
        return _env_float(
            RADIAL_HEIGHT_ZONE_MARGIN_M_ENV_VAR,
            RADIAL_DEFAULT_HEIGHT_ZONE_MARGIN_M,
            minimum=0.0,
            maximum=0.10,
        )

    def _radial_height_zone_min_radius_m(self) -> float:
        return _env_float(
            RADIAL_HEIGHT_ZONE_MIN_RADIUS_MM_ENV_VAR,
            RADIAL_DEFAULT_MIN_RADIUS_MM,
            minimum=1.0,
            maximum=1000.0,
        ) / 1000.0

    def _component_top_z_m(self) -> float:
        return float(
            max(
                component.center_z_m + component.height_m / 2.0
                for component in self.scene.components.values()
            )
        )

    def _radial_height_zone_limit_m(self) -> float:
        return self._component_top_z_m() + self._radial_height_zone_margin_m()

    def _robot_height_zone_geom_ids(self) -> tuple[int, ...]:
        geom_ids: list[int] = []
        for geom_id in range(self.model.ngeom):
            body_name = self.model.body(int(self.model.geom_bodyid[geom_id])).name
            if (
                not body_name
                or body_name == "world"
                or body_name.startswith("component_")
                or body_name in RADIAL_HEIGHT_ZONE_EXEMPT_BODIES
            ):
                continue
            if (
                body_name.startswith("link")
                or body_name.startswith("xarm")
                or "finger" in body_name
                or "knuckle" in body_name
            ):
                geom_ids.append(int(geom_id))
        return tuple(geom_ids)

    def _robot_geom_ids(self) -> tuple[int, ...]:
        base_body_id = int(self.model.body("link_base").id)
        geom_ids: list[int] = []
        for geom_id in range(self.model.ngeom):
            body_id = int(self.model.geom_bodyid[geom_id])
            ancestor_id = body_id
            while ancestor_id > 0:
                if ancestor_id == base_body_id:
                    geom_ids.append(int(geom_id))
                    break
                ancestor_id = int(self.model.body_parentid[ancestor_id])
        return tuple(geom_ids)

    def _geom_world_points(
        self,
        data: mujoco.MjData,
        geom_id: int,
    ) -> np.ndarray:
        geom_type = int(self.model.geom_type[geom_id])
        position = data.geom_xpos[geom_id].copy()
        rotation = data.geom_xmat[geom_id].reshape(3, 3).copy()
        size = self.model.geom_size[geom_id].copy()

        if geom_type == int(mujoco.mjtGeom.mjGEOM_MESH):
            mesh_id = int(self.model.geom_dataid[geom_id])
            vertex_start = int(self.model.mesh_vertadr[mesh_id])
            vertex_count = int(self.model.mesh_vertnum[mesh_id])
            vertices = self.model.mesh_vert[
                vertex_start : vertex_start + vertex_count
            ]
            return position + vertices @ rotation.T

        if geom_type == int(mujoco.mjtGeom.mjGEOM_BOX):
            sx, sy, sz = size[:3]
            corners = np.array(
                [
                    (x, y, z)
                    for x in (-sx, sx)
                    for y in (-sy, sy)
                    for z in (-sz, sz)
                ],
                dtype=float,
            )
            return position + corners @ rotation.T

        radius = float(self.model.geom_rbound[geom_id])
        offsets = np.array(
            (
                (0.0, 0.0, 0.0),
                (radius, 0.0, 0.0),
                (-radius, 0.0, 0.0),
                (0.0, radius, 0.0),
                (0.0, -radius, 0.0),
                (0.0, 0.0, radius),
                (0.0, 0.0, -radius),
            ),
            dtype=float,
        )
        return position + offsets

    def _geom_local_boundary_points(self, geom_id: int) -> np.ndarray:
        geom_type = int(self.model.geom_type[geom_id])
        if geom_type == int(mujoco.mjtGeom.mjGEOM_MESH):
            mesh_id = int(self.model.geom_dataid[geom_id])
            vertex_start = int(self.model.mesh_vertadr[mesh_id])
            vertex_count = int(self.model.mesh_vertnum[mesh_id])
            vertices = self.model.mesh_vert[
                vertex_start : vertex_start + vertex_count
            ]
            lower = np.min(vertices, axis=0)
            upper = np.max(vertices, axis=0)
            return np.array(
                [
                    (x, y, z)
                    for x in (lower[0], upper[0])
                    for y in (lower[1], upper[1])
                    for z in (lower[2], upper[2])
                ],
                dtype=float,
            )
        if geom_type == int(mujoco.mjtGeom.mjGEOM_BOX):
            sx, sy, sz = self.model.geom_size[geom_id, :3]
            return np.array(
                [
                    (x, y, z)
                    for x in (-sx, sx)
                    for y in (-sy, sy)
                    for z in (-sz, sz)
                ],
                dtype=float,
            )

        radius = float(self.model.geom_rbound[geom_id])
        return np.array(
            (
                (0.0, 0.0, 0.0),
                (radius, 0.0, 0.0),
                (-radius, 0.0, 0.0),
                (0.0, radius, 0.0),
                (0.0, -radius, 0.0),
            ),
            dtype=float,
        )

    def _geom_frame_boundary_world_points(
        self,
        data: mujoco.MjData,
        geom_id: int,
    ) -> np.ndarray:
        position = data.geom_xpos[geom_id].copy()
        rotation = data.geom_xmat[geom_id].reshape(3, 3).copy()
        local_points = self._radial_frame_boundary_local_points[geom_id]
        return position + local_points @ rotation.T

    def _radial_height_zone_report(
        self,
        data: mujoco.MjData,
    ) -> RadialHeightZoneReport:
        z_limit = self._radial_height_zone_limit_m()
        min_radius = self._radial_height_zone_min_radius_m()
        best_clearance = math.inf
        best_body: str | None = None
        best_geom: str | None = None
        best_point: np.ndarray | None = None

        for geom_id in self._radial_height_zone_geom_ids:
            body_name = self.model.body(int(self.model.geom_bodyid[geom_id])).name
            points = self._geom_world_points(data, geom_id)
            radial_distance = np.linalg.norm(points[:, :2], axis=1)
            in_zone = radial_distance >= min_radius
            if not bool(np.any(in_zone)):
                continue
            clearances = points[in_zone, 2] - z_limit
            index = int(np.argmin(clearances))
            clearance = float(clearances[index])
            if clearance < best_clearance:
                best_clearance = clearance
                best_body = str(body_name)
                best_geom = str(self.model.geom(geom_id).name)
                best_point = points[in_zone][index].copy()

        if not math.isfinite(best_clearance):
            best_clearance = math.inf
        return RadialHeightZoneReport(
            ok=best_clearance >= 0.0,
            min_clearance_m=float(best_clearance),
            body=best_body,
            geom=best_geom,
            point_m=best_point,
            z_limit_m=float(z_limit),
            min_radius_m=float(min_radius),
        )

    def _radial_frame_boundary_report(
        self,
        data: mujoco.MjData,
    ) -> RadialFrameBoundaryReport:
        bounds = self.scene.lab_bounds_mm
        margin_m = float(self.scene.frame_safety_clearance_mm) / 1000.0
        limits = {
            "left": bounds["x_min"] / 1000.0 + margin_m,
            "right": bounds["x_max"] / 1000.0 - margin_m,
            "front": bounds["y_min"] / 1000.0 + margin_m,
            "back": bounds["y_max"] / 1000.0 - margin_m,
        }
        best_clearance = math.inf
        best_body: str | None = None
        best_geom: str | None = None
        best_side: str | None = None
        best_point: np.ndarray | None = None

        for geom_id in self._radial_frame_boundary_geom_ids:
            points = self._geom_frame_boundary_world_points(data, geom_id)
            candidates = (
                (points[:, 0] - limits["left"], "left"),
                (limits["right"] - points[:, 0], "right"),
                (points[:, 1] - limits["front"], "front"),
                (limits["back"] - points[:, 1], "back"),
            )
            for clearances, side in candidates:
                index = int(np.argmin(clearances))
                clearance = float(clearances[index])
                if clearance < best_clearance:
                    best_clearance = clearance
                    best_body = str(
                        self.model.body(
                            int(self.model.geom_bodyid[geom_id])
                        ).name
                    )
                    best_geom = str(self.model.geom(geom_id).name)
                    best_side = side
                    best_point = points[index].copy()

        return RadialFrameBoundaryReport(
            ok=best_clearance >= 0.0,
            min_clearance_m=float(best_clearance),
            body=best_body,
            geom=best_geom,
            side=best_side,
            point_m=best_point,
        )

    def _radial_frame_boundary_error(
        self,
        report: RadialFrameBoundaryReport,
        *,
        stage: str,
    ) -> str:
        point = (
            np.round(report.point_m, 4).tolist()
            if report.point_m is not None
            else None
        )
        margin_mm = float(self.scene.frame_safety_clearance_mm)
        return (
            f"{stage} enters the {margin_mm / 25.4:.2f} in "
            f"({margin_mm:.1f} mm) frame safety boundary: "
            f"{report.body or 'unknown link'} at {report.side or 'unknown'} "
            f"boundary, clearance={report.min_clearance_m * 1000.0:.1f} mm, "
            f"point={point}"
        )

    def _radial_height_zone_error(
        self,
        report: RadialHeightZoneReport,
        *,
        stage: str,
    ) -> str:
        point = (
            np.round(report.point_m, 4).tolist()
            if report.point_m is not None
            else None
        )
        return (
            f"{stage} enters component-height zone: "
            f"{report.body or 'unknown link'} clearance="
            f"{report.min_clearance_m * 1000.0:.1f} mm below/above limit, "
            f"z_limit={report.z_limit_m:.3f} m, "
            f"zone_min_radius={report.min_radius_m:.3f} m, point={point}"
        )

    def _validate_radial_height_zone_for_joints(
        self,
        joints: np.ndarray,
        *,
        stage: str,
    ) -> RadialHeightZoneReport:
        probe = mujoco.MjData(self.model)
        probe.qpos[:] = self.data.qpos
        probe.ctrl[:] = self.data.ctrl
        probe.eq_active[:] = self.data.eq_active
        probe.qpos[:ARM_DOF] = np.asarray(joints, dtype=float)
        probe.ctrl[:ARM_DOF] = np.asarray(joints, dtype=float)
        mujoco.mj_forward(self.model, probe)
        report = self._radial_height_zone_report(probe)
        if not report.ok:
            raise CollisionPlanError(
                self._radial_height_zone_error(report, stage=stage)
            )
        frame_report = self._radial_frame_boundary_report(probe)
        if not frame_report.ok:
            raise CollisionPlanError(
                self._radial_frame_boundary_error(frame_report, stage=stage)
            )
        return report

    def _preflight_radial_joint_path(
        self,
        start: np.ndarray,
        end: np.ndarray,
        *,
        allowed_tag: Optional[str],
        stage: str,
        attached_pose: tuple[int, np.ndarray, np.ndarray] | None = None,
    ) -> RadialHeightZoneReport:
        worst_report = RadialHeightZoneReport(
            ok=True,
            min_clearance_m=math.inf,
            z_limit_m=self._radial_height_zone_limit_m(),
            min_radius_m=self._radial_height_zone_min_radius_m(),
        )

        def validate_radial_sample(
            probe: mujoco.MjData,
            fraction: float,
        ) -> None:
            nonlocal worst_report
            report = self._radial_height_zone_report(probe)
            if report.min_clearance_m < worst_report.min_clearance_m:
                worst_report = report
            if not report.ok:
                raise CollisionPlanError(
                    self._radial_height_zone_error(
                        report,
                        stage=f"{stage} at path fraction {float(fraction):.2f}",
                    )
                )
            frame_report = self._radial_frame_boundary_report(probe)
            if not frame_report.ok:
                raise CollisionPlanError(
                    self._radial_frame_boundary_error(
                        frame_report,
                        stage=(
                            f"{stage} at path fraction "
                            f"{float(fraction):.2f}"
                        ),
                    )
                )

        self._preflight_joint_path(
            start,
            end,
            allowed_tag=allowed_tag,
            attached_pose=attached_pose,
            sample_count=_radial_joint_path_sample_count(start, end),
            sample_callback=validate_radial_sample,
        )
        return worst_report

    def _radial_library_path(self) -> Path:
        override = os.getenv(RADIAL_LIBRARY_FILE_ENV_VAR, "").strip()
        if override:
            return Path(override).expanduser().resolve()
        root_override = os.getenv(RADIAL_LIBRARY_DIR_ENV_VAR, "").strip()
        root = (
            Path(root_override).expanduser().resolve()
            if root_override
            else _cloud_labs_root() / "radial_motion_libraries"
        )
        return root / f"{self.scene.profile_id}.json"

    @staticmethod
    def _radial_sample_to_dict(sample: RadialPoseSample) -> Dict[str, Any]:
        return {
            "radius_m": float(sample.radius_m),
            "joints": [float(value) for value in sample.joints],
            "min_clearance_m": float(sample.min_clearance_m),
            "tcp_error_m": float(sample.tcp_error_m),
            "vertical_poses": [
                {
                    "z_m": float(pose.z_m),
                    "joints": [float(value) for value in pose.joints],
                    "min_clearance_m": float(pose.min_clearance_m),
                    "tcp_error_m": float(pose.tcp_error_m),
                }
                for pose in sample.vertical_poses
            ],
        }

    def _radial_library_to_dict(
        self,
        library: RadialMotionLibrary,
    ) -> Dict[str, Any]:
        return {
            "version": int(library.version),
            "profile_id": library.profile_id,
            "carry_z_m": float(library.carry_z_m),
            "grasp_z_m": float(library.grasp_z_m),
            "max_vertical_z_m": float(library.max_vertical_z_m),
            "min_radius_m": float(library.min_radius_m),
            "max_radius_m": float(library.max_radius_m),
            "step_m": float(library.step_m),
            "component_height_limit_m": float(library.component_height_limit_m),
            "height_zone_min_radius_m": float(library.height_zone_min_radius_m),
            "height_zone_margin_m": float(library.height_zone_margin_m),
            "samples": [
                self._radial_sample_to_dict(sample)
                for sample in library.samples
            ],
            "unsafe": list(library.unsafe),
        }

    def _radial_library_from_dict(
        self,
        raw: Mapping[str, Any],
    ) -> RadialMotionLibrary:
        samples_raw = raw.get("samples")
        if not isinstance(samples_raw, AbcSequence):
            raise SimulatorError("radial motion library lacks samples")
        samples = tuple(
            RadialPoseSample(
                radius_m=float(item["radius_m"]),
                joints=np.asarray(item["joints"], dtype=float),
                min_clearance_m=float(item.get("min_clearance_m", math.inf)),
                tcp_error_m=float(item.get("tcp_error_m", 0.0)),
                vertical_poses=tuple(
                    RadialVerticalPose(
                        z_m=float(pose["z_m"]),
                        joints=np.asarray(pose["joints"], dtype=float),
                        min_clearance_m=float(
                            pose.get("min_clearance_m", math.inf)
                        ),
                        tcp_error_m=float(pose.get("tcp_error_m", 0.0)),
                    )
                    for pose in item.get("vertical_poses", ())
                    if isinstance(pose, Mapping)
                ),
            )
            for item in samples_raw
            if isinstance(item, Mapping)
        )
        if not samples:
            raise SimulatorError("radial motion library has no usable samples")
        samples = tuple(sorted(samples, key=lambda sample: sample.radius_m))
        unsafe_raw = raw.get("unsafe") or ()
        unsafe: tuple[Mapping[str, Any], ...] = tuple(
            dict(item) for item in unsafe_raw if isinstance(item, Mapping)
        )
        return RadialMotionLibrary(
            version=int(raw.get("version", 0)),
            profile_id=str(raw.get("profile_id") or ""),
            carry_z_m=float(raw.get("carry_z_m")),
            grasp_z_m=float(raw.get("grasp_z_m", RADIAL_DEFAULT_GRASP_Z_M)),
            max_vertical_z_m=float(
                raw.get("max_vertical_z_m", RADIAL_DEFAULT_MAX_VERTICAL_Z_M)
            ),
            min_radius_m=float(raw.get("min_radius_m")),
            max_radius_m=float(raw.get("max_radius_m")),
            step_m=float(raw.get("step_m")),
            component_height_limit_m=float(raw.get("component_height_limit_m")),
            height_zone_min_radius_m=float(raw.get("height_zone_min_radius_m")),
            height_zone_margin_m=float(raw.get("height_zone_margin_m")),
            samples=samples,
            unsafe=unsafe,
        )

    def _load_radial_motion_library(self) -> RadialMotionLibrary:
        if self._radial_motion_library is not None:
            return self._radial_motion_library
        path = self._radial_library_path()
        if path.is_file():
            try:
                library = self._radial_library_from_dict(
                    json.loads(path.read_text(encoding="utf-8"))
                )
            except (OSError, ValueError, KeyError, TypeError) as exc:
                raise SimulatorError(
                    f"failed to read radial motion library {path}: {exc}"
                ) from exc
            if library.version != RADIAL_LIBRARY_VERSION:
                raise SimulatorError(
                    f"radial motion library {path} has unsupported version "
                    f"{library.version}"
                )
            if library.profile_id != self.scene.profile_id:
                raise SimulatorError(
                    f"radial motion library {path} is for profile "
                    f"{library.profile_id!r}, not {self.scene.profile_id!r}"
                )
            self._radial_motion_library = library
            return library

        library = self.generate_radial_motion_library(write=True)
        self._radial_motion_library = library
        return library

    def _radial_sample_radii_desc(self) -> list[float]:
        min_radius = self._radial_min_radius_m()
        max_radius = self._radial_max_radius_m()
        if min_radius >= max_radius:
            raise SimulatorError("radial minimum radius must be below maximum radius")
        step = self._radial_step_m()
        radii: list[float] = []
        radius = max_radius
        while radius > min_radius + 1e-9:
            radii.append(float(radius))
            radius -= step
        radii.append(float(min_radius))
        return radii

    def _radial_grasp_z_m(self) -> float:
        grasp_heights = {
            round(float(component.grasp_tcp_z_m), 6)
            for component in self.scene.components.values()
        }
        if len(grasp_heights) != 1:
            raise SimulatorError(
                "radial library requires one shared component grasp height"
            )
        return float(next(iter(grasp_heights)))

    def _radial_vertical_levels_desc(self) -> tuple[float, ...]:
        grasp_z = self._radial_grasp_z_m()
        max_z = max(
            RADIAL_DEFAULT_MAX_VERTICAL_Z_M,
            self._radial_carry_z_m(),
            self._real_pickup_retract_z(grasp_z),
        )
        count = max(
            1,
            int(
                math.ceil(
                    (max_z - grasp_z) / (RADIAL_VERTICAL_STEP_MM / 1000.0)
                )
            ),
        )
        levels = {
            float(value)
            for value in np.linspace(max_z, grasp_z, count + 1)
        }
        levels.update(
            {
                float(max_z),
                float(self._radial_carry_z_m()),
                float(REAL_PICKUP_CAMERA_APPROACH_Z_M),
                float(grasp_z + RELEASE_CLEARANCE_M),
                float(grasp_z),
            }
        )
        return tuple(sorted(levels, reverse=True))

    def generate_radial_motion_library(
        self,
        *,
        write: bool = False,
    ) -> RadialMotionLibrary:
        snapshot = self._motion_snapshot()
        samples_desc: list[RadialPoseSample] = []
        unsafe: list[Mapping[str, Any]] = []
        carry_z = self._radial_carry_z_m()
        grasp_z = self._radial_grasp_z_m()
        vertical_levels = self._radial_vertical_levels_desc()
        max_vertical_z = float(vertical_levels[0])
        target_rotation = self._target_rotation(0.0)
        seed = np.asarray(self.home, dtype=float).copy()
        previous_sample: RadialPoseSample | None = None
        try:
            for radius_m in self._radial_sample_radii_desc():
                try:
                    def solve_vertical_pose(
                        z_m: float,
                        pose_seed: np.ndarray,
                    ) -> RadialVerticalPose:
                        target_position = np.array(
                            (radius_m, 0.0, float(z_m)),
                            dtype=float,
                        )
                        joints = self.ik.solve(
                            self.model,
                            target_position,
                            target_rotation,
                            pose_seed,
                            pose_seed,
                        )
                        report = self._validate_radial_height_zone_for_joints(
                            joints,
                            stage=(
                                "radial library pose "
                                f"r={radius_m * 1000.0:.1f} mm "
                                f"z={z_m * 1000.0:.1f} mm"
                            ),
                        )
                        self.data.qpos[:ARM_DOF] = joints
                        self.data.ctrl[:ARM_DOF] = joints
                        self.current_joint_target = joints.copy()
                        mujoco.mj_forward(self.model, self.data)
                        tcp_error = float(
                            np.linalg.norm(
                                self.data.site("link_tcp").xpos.copy()
                                - target_position
                            )
                        )
                        return RadialVerticalPose(
                            z_m=float(z_m),
                            joints=np.asarray(joints, dtype=float).copy(),
                            min_clearance_m=float(report.min_clearance_m),
                            tcp_error_m=tcp_error,
                        )

                    carry_pose = solve_vertical_pose(carry_z, seed.copy())
                    poses_by_z = {float(carry_z): carry_pose}
                    pose_seed = carry_pose.joints.copy()
                    for z_m in sorted(
                        (level for level in vertical_levels if level > carry_z),
                    ):
                        pose = solve_vertical_pose(z_m, pose_seed)
                        poses_by_z[float(z_m)] = pose
                        pose_seed = pose.joints
                    pose_seed = carry_pose.joints.copy()
                    for z_m in sorted(
                        (level for level in vertical_levels if level < carry_z),
                        reverse=True,
                    ):
                        pose = solve_vertical_pose(z_m, pose_seed)
                        poses_by_z[float(z_m)] = pose
                        pose_seed = pose.joints

                    vertical_poses = [
                        poses_by_z[float(z_m)] for z_m in vertical_levels
                    ]
                    for upper_pose, lower_pose in zip(
                        vertical_poses,
                        vertical_poses[1:],
                    ):
                        self._preflight_radial_joint_path(
                            upper_pose.joints,
                            lower_pose.joints,
                            allowed_tag=None,
                            stage=(
                                "radial library vertical segment "
                                f"r={radius_m * 1000.0:.1f} mm"
                            ),
                        )

                    if previous_sample is not None:
                        for previous_pose, pose in zip(
                            previous_sample.vertical_poses,
                            vertical_poses,
                        ):
                            self._preflight_radial_joint_path(
                                previous_pose.joints,
                                pose.joints,
                                allowed_tag=None,
                                stage=(
                                    "radial library horizontal segment "
                                    f"z={pose.z_m * 1000.0:.1f} mm "
                                    f"r={previous_sample.radius_m * 1000.0:.1f}->"
                                    f"{radius_m * 1000.0:.1f} mm"
                                ),
                            )

                    sample = RadialPoseSample(
                        radius_m=float(radius_m),
                        joints=carry_pose.joints.copy(),
                        min_clearance_m=min(
                            pose.min_clearance_m for pose in vertical_poses
                        ),
                        tcp_error_m=max(
                            pose.tcp_error_m for pose in vertical_poses
                        ),
                        vertical_poses=tuple(vertical_poses),
                    )
                    samples_desc.append(sample)
                    previous_sample = sample
                    seed = carry_pose.joints.copy()
                except Exception as exc:  # noqa: BLE001
                    unsafe.append(
                        {
                            "radius_m": float(radius_m),
                            "error": str(exc),
                        }
                    )
            if not samples_desc:
                raise CollisionPlanError(
                    "radial motion library generation found no safe carry poses"
                )
            samples = tuple(
                sorted(samples_desc, key=lambda sample: sample.radius_m)
            )
            library = RadialMotionLibrary(
                version=RADIAL_LIBRARY_VERSION,
                profile_id=self.scene.profile_id,
                carry_z_m=float(carry_z),
                grasp_z_m=float(grasp_z),
                max_vertical_z_m=max_vertical_z,
                min_radius_m=float(self._radial_min_radius_m()),
                max_radius_m=float(self._radial_max_radius_m()),
                step_m=float(self._radial_step_m()),
                component_height_limit_m=float(self._radial_height_zone_limit_m()),
                height_zone_min_radius_m=float(
                    self._radial_height_zone_min_radius_m()
                ),
                height_zone_margin_m=float(self._radial_height_zone_margin_m()),
                samples=samples,
                unsafe=tuple(unsafe),
            )
            if write:
                path = self._radial_library_path()
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    json.dumps(
                        self._radial_library_to_dict(library),
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                self._log(
                    "radial_motion_library_written",
                    path=str(path),
                    sample_count=len(samples),
                    unsafe_count=len(unsafe),
                    min_radius_m=library.radius_bounds_m[0],
                    max_radius_m=library.radius_bounds_m[1],
                )
            self._radial_motion_library = library
            return library
        finally:
            self._restore_motion_snapshot(snapshot)

    def _interpolate_radial_library_joints(
        self,
        radius_m: float,
    ) -> np.ndarray:
        library = self._load_radial_motion_library()
        lower_bound, upper_bound = library.radius_bounds_m
        if radius_m < lower_bound - 1e-6 or radius_m > upper_bound + 1e-6:
            raise CollisionPlanError(
                "radial target radius "
                f"{radius_m * 1000.0:.1f} mm is outside library range "
                f"{lower_bound * 1000.0:.1f}-"
                f"{upper_bound * 1000.0:.1f} mm"
            )
        samples = library.samples
        for sample in samples:
            if abs(sample.radius_m - radius_m) <= 1e-9:
                return sample.joints.copy()
        for lower, upper in zip(samples, samples[1:]):
            if lower.radius_m <= radius_m <= upper.radius_m:
                gap = upper.radius_m - lower.radius_m
                if gap > max(library.step_m * 1.75, 0.002):
                    raise CollisionPlanError(
                        "radial target radius falls in an unsafe/unvalidated "
                        f"library gap {lower.radius_m * 1000.0:.1f}-"
                        f"{upper.radius_m * 1000.0:.1f} mm"
                    )
                phase = (radius_m - lower.radius_m) / max(gap, 1e-9)
                upper_joints = self._nearest_equivalent_joints(
                    upper.joints,
                    lower.joints,
                )
                return lower.joints + float(phase) * (upper_joints - lower.joints)
        raise CollisionPlanError(
            f"radial target radius {radius_m * 1000.0:.1f} mm is not covered"
        )

    def _interpolate_vertical_pose_joints(
        self,
        sample: RadialPoseSample,
        z_m: float,
    ) -> np.ndarray:
        poses = tuple(sorted(sample.vertical_poses, key=lambda pose: pose.z_m))
        if not poses:
            raise SimulatorError(
                f"radial sample {sample.radius_m * 1000.0:.1f} mm has no "
                "vertical poses"
            )
        if z_m < poses[0].z_m - 1e-6 or z_m > poses[-1].z_m + 1e-6:
            raise CollisionPlanError(
                f"radial target height {z_m * 1000.0:.1f} mm is outside "
                f"library range {poses[0].z_m * 1000.0:.1f}-"
                f"{poses[-1].z_m * 1000.0:.1f} mm"
            )
        for pose in poses:
            if abs(pose.z_m - z_m) <= 1e-9:
                return pose.joints.copy()
        for lower, upper in zip(poses, poses[1:]):
            if lower.z_m <= z_m <= upper.z_m:
                phase = (z_m - lower.z_m) / max(upper.z_m - lower.z_m, 1e-9)
                upper_joints = self._nearest_equivalent_joints(
                    upper.joints,
                    lower.joints,
                )
                return lower.joints + float(phase) * (
                    upper_joints - lower.joints
                )
        raise CollisionPlanError(
            f"radial target height {z_m * 1000.0:.1f} mm is not covered"
        )

    def _interpolate_radial_vertical_joints(
        self,
        radius_m: float,
        z_m: float,
    ) -> np.ndarray:
        library = self._load_radial_motion_library()
        lower_bound, upper_bound = library.radius_bounds_m
        if radius_m < lower_bound - 1e-6 or radius_m > upper_bound + 1e-6:
            raise CollisionPlanError(
                "radial target radius "
                f"{radius_m * 1000.0:.1f} mm is outside library range "
                f"{lower_bound * 1000.0:.1f}-"
                f"{upper_bound * 1000.0:.1f} mm"
            )
        samples = library.samples
        for sample in samples:
            if abs(sample.radius_m - radius_m) <= 1e-9:
                return self._interpolate_vertical_pose_joints(sample, z_m)
        for lower, upper in zip(samples, samples[1:]):
            if lower.radius_m <= radius_m <= upper.radius_m:
                gap = upper.radius_m - lower.radius_m
                if gap > max(library.step_m * 1.75, 0.002):
                    raise CollisionPlanError(
                        "radial target radius falls in an unsafe/unvalidated "
                        f"library gap {lower.radius_m * 1000.0:.1f}-"
                        f"{upper.radius_m * 1000.0:.1f} mm"
                    )
                lower_joints = self._interpolate_vertical_pose_joints(lower, z_m)
                upper_joints = self._nearest_equivalent_joints(
                    self._interpolate_vertical_pose_joints(upper, z_m),
                    lower_joints,
                )
                phase = (radius_m - lower.radius_m) / max(gap, 1e-9)
                return lower_joints + float(phase) * (
                    upper_joints - lower_joints
                )
        raise CollisionPlanError(
            f"radial target radius {radius_m * 1000.0:.1f} mm is not covered"
        )

    def _radial_carry_pose_joints(
        self,
        radius_m: float,
        theta_rad: float,
        rotation: np.ndarray,
        *,
        seed: np.ndarray | None = None,
        stage: str,
    ) -> np.ndarray:
        library = self._load_radial_motion_library()
        return self._radial_pose_joints(
            radius_m,
            theta_rad,
            library.carry_z_m,
            rotation,
            seed=seed,
            stage=stage,
        )

    def _radial_pose_joints(
        self,
        radius_m: float,
        theta_rad: float,
        z_m: float,
        rotation: np.ndarray,
        *,
        seed: np.ndarray | None = None,
        stage: str,
    ) -> np.ndarray:
        canonical = self._interpolate_radial_vertical_joints(radius_m, z_m)
        target_yaw_rad = math.radians(
            self._target_yaw_from_rotation(np.asarray(rotation, dtype=float))
        )
        joints = canonical.copy()
        joints[0] = canonical[0] + float(theta_rad)
        # With a downward TCP, joint 1 adds world yaw and joint 7 subtracts it.
        # This preserves the canonical radial posture while selecting the requested
        # world yaw without asking IK to choose a different elbow/shoulder branch.
        joints[6] = canonical[6] + float(theta_rad) - target_yaw_rad
        if seed is not None:
            joints = self._nearest_equivalent_joints(joints, seed)
        DampedLeastSquaresIK._clip_joint_limits(self.model, joints)

        self._validate_radial_pose_target(
            joints,
            radius_m=radius_m,
            theta_rad=theta_rad,
            z_m=z_m,
            rotation=rotation,
            stage=stage,
        )
        return np.asarray(joints, dtype=float).copy()

    def _validate_radial_pose_target(
        self,
        joints: np.ndarray,
        *,
        radius_m: float,
        theta_rad: float,
        z_m: float,
        rotation: np.ndarray,
        stage: str,
    ) -> None:
        joints = np.asarray(joints, dtype=float)

        target_position = np.array(
            (
                float(radius_m) * math.cos(float(theta_rad)),
                float(radius_m) * math.sin(float(theta_rad)),
                float(z_m),
            ),
            dtype=float,
        )
        self._validate_radial_height_zone_for_joints(joints, stage=stage)

        probe = mujoco.MjData(self.model)
        probe.qpos[:] = self.data.qpos
        probe.ctrl[:] = self.data.ctrl
        probe.eq_active[:] = self.data.eq_active
        probe.qpos[:ARM_DOF] = joints
        probe.ctrl[:ARM_DOF] = joints
        mujoco.mj_forward(self.model, probe)
        tcp_position = probe.site("link_tcp").xpos.copy()
        tcp_rotation = probe.site("link_tcp").xmat.reshape(3, 3).copy()
        position_error_m = float(np.linalg.norm(tcp_position - target_position))
        rotation_error_deg = _rotation_distance_deg(
            tcp_rotation,
            np.asarray(rotation, dtype=float),
        )
        if position_error_m > RADIAL_TCP_POSITION_TOLERANCE_M:
            raise CollisionPlanError(
                f"{stage} radial pose position error is "
                f"{position_error_m * 1000.0:.2f} mm"
            )
        if rotation_error_deg > RADIAL_TCP_ROTATION_TOLERANCE_DEG:
            raise CollisionPlanError(
                f"{stage} radial pose rotation error is "
                f"{rotation_error_deg:.2f} deg"
            )

    def _radial_joint_duration_s(
        self,
        start: np.ndarray,
        end: np.ndarray,
    ) -> float:
        max_delta = float(np.max(np.abs(np.asarray(end) - np.asarray(start))))
        return float(
            np.clip(
                max_delta / RADIAL_JOINT_SPEED_RAD_PER_S,
                RADIAL_MIN_SEGMENT_DURATION_S,
                RADIAL_MAX_SEGMENT_DURATION_S,
            )
        )

    def _validate_radial_wrist_travel(
        self,
        name: str,
        waypoints: Sequence[np.ndarray],
    ) -> None:
        travel_rad = _radial_joint_path_travel_rad(waypoints, 6)
        if travel_rad <= RADIAL_MAX_WRIST_STAGE_TRAVEL_RAD:
            return
        raise CollisionPlanError(
            f"{name} would rotate joint7 through "
            f"{math.degrees(travel_rad):.1f} deg; radial stages are limited "
            "to 180 deg of wrist travel"
        )

    def _rebase_radial_periodic_joint_branches(self, *, reason: str) -> None:
        previous = self.current_joint_target.copy()
        rebased = previous.copy()
        shifts = np.zeros(ARM_DOF, dtype=float)
        for joint_index in (0, 6):
            joint_id = self.model.joint(f"joint{joint_index + 1}").id
            lower: float | None = None
            upper: float | None = None
            if self.model.jnt_limited[joint_id]:
                lower, upper = (
                    float(limit) for limit in self.model.jnt_range[joint_id]
                )
            canonical = _canonical_equivalent_angle_rad(
                float(previous[joint_index]),
                lower=lower,
                upper=upper,
            )
            shift = canonical - float(previous[joint_index])
            shifted_qpos = float(self.data.qpos[joint_index]) + shift
            if lower is not None and upper is not None and not (
                lower - 1e-8 <= shifted_qpos <= upper + 1e-8
            ):
                raise SimulatorError(
                    f"cannot rebase joint{joint_index + 1} inside limits "
                    f"during {reason}"
                )
            rebased[joint_index] = canonical
            shifts[joint_index] = shift

        if np.allclose(shifts, 0.0, atol=1e-9, rtol=0.0):
            return
        self.current_joint_target = rebased
        self.data.qpos[:ARM_DOF] += shifts
        self.data.ctrl[:ARM_DOF] = rebased
        mujoco.mj_forward(self.model, self.data)
        self._log(
            "radial_periodic_joint_branches_rebased",
            reason=reason,
            previous_joints=previous,
            rebased_joints=rebased,
            shifts_rad=shifts,
        )

    def _execute_radial_joint_waypoints(
        self,
        name: str,
        waypoints: Sequence[np.ndarray],
        *,
        allowed_tag: Optional[str],
        attached_pose: tuple[int, np.ndarray, np.ndarray] | None = None,
        prevalidated: bool = False,
    ) -> None:
        if len(waypoints) < 2:
            self.stage_trace.append(name)
            self._emit_progress(
                f"Simulator executing radial {name}.",
                phase="radial_execute",
                stage=name,
            )
            self._log(
                "radial_stage_complete",
                stage=name,
                waypoint_count=len(waypoints),
                state=self._runtime_snapshot(),
            )
            return
        checked_waypoints = [
            np.asarray(waypoint, dtype=float).copy()
            for waypoint in waypoints
        ]
        self._validate_radial_wrist_travel(name, checked_waypoints)
        worst_report: RadialHeightZoneReport | None = None
        if not prevalidated:
            for start, end in zip(checked_waypoints, checked_waypoints[1:]):
                report = self._preflight_radial_joint_path(
                    start,
                    end,
                    allowed_tag=allowed_tag,
                    stage=name,
                    attached_pose=attached_pose,
                )
                if (
                    worst_report is None
                    or report.min_clearance_m < worst_report.min_clearance_m
                ):
                    worst_report = report
        durations = [
            self._radial_joint_duration_s(start, end)
            for start, end in zip(checked_waypoints, checked_waypoints[1:])
        ]
        gentle_rotation_finish = name in {
            "radial coordinated rotate",
            "home coordinated rotate",
        }
        smooth_carried_motion = (
            allowed_tag is not None
            and self._gripper_position >= GRIPPER_CLOSED * 0.5
        )
        if gentle_rotation_finish and durations and not smooth_carried_motion:
            durations[-1] *= RADIAL_ROTATION_FINAL_DURATION_SCALE
        self.stage_trace.append(name)
        self._emit_progress(
            f"Simulator executing radial {name}.",
            phase="radial_execute",
            stage=name,
        )
        self._log(
            "radial_stage_request",
            stage=name,
            waypoint_count=len(checked_waypoints),
            allowed_tag=allowed_tag,
            prevalidated=prevalidated,
            smooth_carried_motion=smooth_carried_motion,
            playback_duration_s=(
                sum(durations) * RADIAL_CARRIED_SMOOTH_DURATION_SCALE
                if smooth_carried_motion
                else sum(durations)
            ),
            worst_clearance_m=(
                worst_report.min_clearance_m if worst_report is not None else None
            ),
            state=self._runtime_snapshot(),
        )
        previous_allowed = self.allowed_collision_tag
        self.allowed_collision_tag = allowed_tag
        try:
            self._drive_joint_waypoints(
                checked_waypoints,
                durations,
                gentle_final=gentle_rotation_finish,
                smooth_stage=smooth_carried_motion,
            )
            self.current_joint_target = checked_waypoints[-1].copy()
        finally:
            self.allowed_collision_tag = previous_allowed
        self._log(
            "radial_stage_complete",
            stage=name,
            state=self._runtime_snapshot(),
        )

    def _radial_xy_pose_joints(
        self,
        xy_m: np.ndarray,
        z_m: float,
        rotation: np.ndarray,
        *,
        seed: np.ndarray,
        stage: str,
    ) -> np.ndarray:
        xy = np.asarray(xy_m, dtype=float)[:2]
        radius = float(np.linalg.norm(xy))
        if radius <= 1e-6:
            raise CollisionPlanError("radial planner cannot use a zero-radius pose")
        theta = math.atan2(float(xy[1]), float(xy[0]))
        return self._radial_pose_joints(
            radius,
            theta,
            float(z_m),
            rotation,
            seed=seed,
            stage=stage,
        )

    def _radial_vertical_waypoints(
        self,
        xy_m: np.ndarray,
        start_z_m: float,
        end_z_m: float,
        rotation: np.ndarray,
        *,
        seed: np.ndarray,
        stage: str,
    ) -> tuple[np.ndarray, ...]:
        waypoints = [np.asarray(seed, dtype=float).copy()]
        for z_m in self._intermediate_scalar_values(
            float(start_z_m),
            float(end_z_m),
            max_step=RADIAL_VERTICAL_STEP_MM / 1000.0,
        ):
            waypoints.append(
                self._radial_xy_pose_joints(
                    xy_m,
                    z_m,
                    rotation,
                    seed=waypoints[-1],
                    stage=stage,
                )
            )
        return tuple(waypoints)

    def _radial_xy_waypoints(
        self,
        start_xy_m: np.ndarray,
        end_xy_m: np.ndarray,
        z_m: float,
        rotation: np.ndarray,
        *,
        seed: np.ndarray,
        stage: str,
    ) -> tuple[np.ndarray, ...]:
        start_xy = np.asarray(start_xy_m, dtype=float)[:2]
        end_xy = np.asarray(end_xy_m, dtype=float)[:2]
        distance = float(np.linalg.norm(end_xy - start_xy))
        count = max(
            1,
            int(math.ceil(distance / RADIAL_XY_IK_STEP_M)),
        )
        waypoints = [np.asarray(seed, dtype=float).copy()]
        for index in range(1, count + 1):
            phase = index / count
            xy_m = start_xy + phase * (end_xy - start_xy)
            waypoints.append(
                self._radial_xy_pose_joints(
                    xy_m,
                    z_m,
                    rotation,
                    seed=waypoints[-1],
                    stage=stage,
                )
            )
        return tuple(waypoints)

    def _validate_outer_pose_target(
        self,
        joints: np.ndarray,
        position_m: np.ndarray,
        rotation: np.ndarray,
        *,
        stage: str,
    ) -> None:
        self._validate_radial_height_zone_for_joints(joints, stage=stage)
        probe = mujoco.MjData(self.model)
        probe.qpos[:] = self.data.qpos
        probe.ctrl[:] = self.data.ctrl
        probe.eq_active[:] = self.data.eq_active
        probe.qpos[:ARM_DOF] = np.asarray(joints, dtype=float)
        probe.ctrl[:ARM_DOF] = np.asarray(joints, dtype=float)
        mujoco.mj_forward(self.model, probe)
        tcp_position = probe.site("link_tcp").xpos.copy()
        tcp_rotation = probe.site("link_tcp").xmat.reshape(3, 3).copy()
        position_error_m = float(
            np.linalg.norm(tcp_position - np.asarray(position_m, dtype=float))
        )
        rotation_error_deg = _rotation_distance_deg(
            tcp_rotation,
            np.asarray(rotation, dtype=float),
        )
        if position_error_m > RADIAL_TCP_POSITION_TOLERANCE_M:
            raise CollisionPlanError(
                f"{stage} outer pose position error is "
                f"{position_error_m * 1000.0:.2f} mm"
            )
        if rotation_error_deg > RADIAL_TCP_ROTATION_TOLERANCE_DEG:
            raise CollisionPlanError(
                f"{stage} outer pose rotation error is "
                f"{rotation_error_deg:.2f} deg"
            )

    def _outer_pose_joints(
        self,
        xy_m: np.ndarray,
        z_m: float,
        rotation: np.ndarray,
        *,
        seed: np.ndarray,
        stage: str,
    ) -> np.ndarray:
        target_position = np.array(
            (*np.asarray(xy_m, dtype=float)[:2], float(z_m)),
            dtype=float,
        )
        seed = np.asarray(seed, dtype=float)
        joints = self.ik.solve(
            self.model,
            target_position,
            np.asarray(rotation, dtype=float),
            seed,
            seed,
        )
        joints = self._nearest_equivalent_joints(joints, seed)
        DampedLeastSquaresIK._clip_joint_limits(self.model, joints)
        self._validate_outer_pose_target(
            joints,
            target_position,
            rotation,
            stage=stage,
        )
        return np.asarray(joints, dtype=float).copy()

    def _outer_vertical_waypoints(
        self,
        xy_m: np.ndarray,
        start_z_m: float,
        end_z_m: float,
        rotation: np.ndarray,
        *,
        seed: np.ndarray,
        stage: str,
    ) -> tuple[np.ndarray, ...]:
        waypoints = [np.asarray(seed, dtype=float).copy()]
        for z_m in self._intermediate_scalar_values(
            float(start_z_m),
            float(end_z_m),
            max_step=RADIAL_VERTICAL_STEP_MM / 1000.0,
        ):
            waypoints.append(
                self._outer_pose_joints(
                    xy_m,
                    z_m,
                    rotation,
                    seed=waypoints[-1],
                    stage=stage,
                )
            )
        return tuple(waypoints)

    def _outer_route_point_allowed(
        self,
        xy_m: np.ndarray,
        z_m: float,
        rotation: np.ndarray,
        *,
        allowed_tag: str | None,
        attached_pose: tuple[int, np.ndarray, np.ndarray] | None,
    ) -> bool:
        xy = np.asarray(xy_m, dtype=float)[:2]
        bounds = self.scene.lab_bounds_mm
        if not (
            bounds["x_min"] / 1000.0 - 1e-6
            <= float(xy[0])
            <= bounds["x_max"] / 1000.0 + 1e-6
            and bounds["y_min"] / 1000.0 - 1e-6
            <= float(xy[1])
            <= bounds["y_max"] / 1000.0 + 1e-6
        ):
            return False
        if attached_pose is None or allowed_tag not in self.scene.components:
            return True

        _, relative_position, relative_rotation = attached_pose
        tcp_position = np.array((*xy, float(z_m)), dtype=float)
        tcp_rotation = np.asarray(rotation, dtype=float)
        object_position = tcp_position + tcp_rotation @ relative_position
        object_rotation = tcp_rotation @ relative_rotation
        object_yaw_deg = math.degrees(
            math.atan2(object_rotation[1, 0], object_rotation[0, 0])
        )
        spec = self.scene.components[allowed_tag]
        half_x_m, half_y_m = spec.footprint_half_extents_m(object_yaw_deg)
        frame_m = float(self.scene.frame_safety_clearance_mm) / 1000.0
        return bool(
            bounds["x_min"] / 1000.0 + frame_m + half_x_m - 1e-6
            <= float(object_position[0])
            <= bounds["x_max"] / 1000.0 - frame_m - half_x_m + 1e-6
            and bounds["y_min"] / 1000.0 + frame_m + half_y_m - 1e-6
            <= float(object_position[1])
            <= bounds["y_max"] / 1000.0 - frame_m - half_y_m + 1e-6
        )

    def _outer_polyline_waypoints(
        self,
        points_xy_m: Sequence[np.ndarray],
        z_m: float,
        rotation: np.ndarray,
        *,
        seed: np.ndarray,
        stage: str,
        allowed_tag: str | None,
        attached_pose: tuple[int, np.ndarray, np.ndarray] | None,
    ) -> tuple[np.ndarray, ...]:
        points = [np.asarray(point, dtype=float)[:2] for point in points_xy_m]
        if len(points) < 2:
            return (np.asarray(seed, dtype=float).copy(),)
        waypoints = [np.asarray(seed, dtype=float).copy()]
        previous_xy = points[0]
        for end_xy in points[1:]:
            distance = float(np.linalg.norm(end_xy - previous_xy))
            count = max(
                1,
                int(
                    math.ceil(
                        distance / (RADIAL_OUTER_XY_STEP_MM / 1000.0)
                    )
                ),
            )
            for index in range(1, count + 1):
                phase = index / count
                xy_m = previous_xy + phase * (end_xy - previous_xy)
                if not self._outer_route_point_allowed(
                    xy_m,
                    z_m,
                    rotation,
                    allowed_tag=allowed_tag,
                    attached_pose=attached_pose,
                ):
                    raise CollisionPlanError(
                        f"{stage} leaves the permitted table/frame area"
                    )
                target = self._outer_pose_joints(
                    xy_m,
                    z_m,
                    rotation,
                    seed=waypoints[-1],
                    stage=stage,
                )
                self._preflight_radial_joint_path(
                    waypoints[-1],
                    target,
                    allowed_tag=allowed_tag,
                    stage=stage,
                    attached_pose=attached_pose,
                )
                waypoints.append(target)
            previous_xy = end_xy
        return tuple(waypoints)

    def _outer_xy_route_waypoints(
        self,
        start_xy_m: np.ndarray,
        end_xy_m: np.ndarray,
        z_m: float,
        rotation: np.ndarray,
        *,
        seed: np.ndarray,
        stage: str,
        allowed_tag: str | None,
        attached_pose: tuple[int, np.ndarray, np.ndarray] | None,
    ) -> tuple[np.ndarray, ...]:
        start_xy = np.asarray(start_xy_m, dtype=float)[:2]
        end_xy = np.asarray(end_xy_m, dtype=float)[:2]
        if float(np.linalg.norm(end_xy - start_xy)) <= 1e-9:
            return (np.asarray(seed, dtype=float).copy(),)
        direct_error: Exception | None = None
        try:
            direct = self._outer_polyline_waypoints(
                (start_xy, end_xy),
                z_m,
                rotation,
                seed=seed,
                stage=stage,
                allowed_tag=allowed_tag,
                attached_pose=attached_pose,
            )
            self._log(
                "radial_outer_xy_route_success",
                stage=stage,
                route="direct",
                start_xy_m=start_xy,
                end_xy_m=end_xy,
                z_m=float(z_m),
                waypoint_count=len(direct),
            )
            return direct
        except Exception as exc:  # noqa: BLE001
            direct_error = exc

        step_m = RADIAL_OUTER_GRID_STEP_MM / 1000.0
        margin_m = RADIAL_OUTER_SEARCH_MARGIN_M
        bounds = self.scene.lab_bounds_mm
        x_min = max(
            min(float(start_xy[0]), float(end_xy[0])) - margin_m,
            bounds["x_min"] / 1000.0,
        )
        x_max = min(
            max(float(start_xy[0]), float(end_xy[0])) + margin_m,
            bounds["x_max"] / 1000.0,
        )
        y_min = max(
            min(float(start_xy[1]), float(end_xy[1])) - margin_m,
            bounds["y_min"] / 1000.0,
        )
        y_max = min(
            max(float(start_xy[1]), float(end_xy[1])) + margin_m,
            bounds["y_max"] / 1000.0,
        )

        start_key = (0, 0)
        queue: list[tuple[float, float, int, tuple[int, int]]] = []
        sequence = 0
        heapq.heappush(
            queue,
            (float(np.linalg.norm(end_xy - start_xy)), 0.0, sequence, start_key),
        )
        best_cost = {start_key: 0.0}
        parent: dict[tuple[int, int], tuple[int, int] | None] = {
            start_key: None
        }
        joints_by_key = {start_key: np.asarray(seed, dtype=float).copy()}
        xy_by_key = {start_key: start_xy.copy()}
        rejected: list[str] = []
        expansions = 0
        neighbor_offsets = tuple(
            (dx, dy)
            for dx in (-1, 0, 1)
            for dy in (-1, 0, 1)
            if dx or dy
        )

        while queue and expansions < RADIAL_OUTER_MAX_SEARCH_EXPANSIONS:
            _, cost, _, key = heapq.heappop(queue)
            if cost > best_cost.get(key, math.inf) + 1e-9:
                continue
            expansions += 1
            current_xy = xy_by_key[key]
            current_joints = joints_by_key[key]

            if float(np.linalg.norm(end_xy - current_xy)) <= step_m * 1.75:
                try:
                    goal_joints = self._outer_pose_joints(
                        end_xy,
                        z_m,
                        rotation,
                        seed=current_joints,
                        stage=stage,
                    )
                    self._preflight_radial_joint_path(
                        current_joints,
                        goal_joints,
                        allowed_tag=allowed_tag,
                        stage=stage,
                        attached_pose=attached_pose,
                    )
                    keys: list[tuple[int, int]] = []
                    cursor: tuple[int, int] | None = key
                    while cursor is not None:
                        keys.append(cursor)
                        cursor = parent[cursor]
                    keys.reverse()
                    route = tuple(
                        [joints_by_key[item].copy() for item in keys]
                        + [goal_joints.copy()]
                    )
                    self._log(
                        "radial_outer_xy_route_success",
                        stage=stage,
                        route="grid",
                        start_xy_m=start_xy,
                        end_xy_m=end_xy,
                        z_m=float(z_m),
                        expansions=expansions,
                        waypoint_count=len(route),
                        direct_error=str(direct_error),
                    )
                    return route
                except Exception as exc:  # noqa: BLE001
                    if len(rejected) < 8:
                        rejected.append(str(exc))

            for dx, dy in neighbor_offsets:
                neighbor_key = (key[0] + dx, key[1] + dy)
                neighbor_xy = start_xy + step_m * np.array(neighbor_key)
                if not (
                    x_min - 1e-9 <= float(neighbor_xy[0]) <= x_max + 1e-9
                    and y_min - 1e-9
                    <= float(neighbor_xy[1])
                    <= y_max + 1e-9
                ):
                    continue
                if not self._outer_route_point_allowed(
                    neighbor_xy,
                    z_m,
                    rotation,
                    allowed_tag=allowed_tag,
                    attached_pose=attached_pose,
                ):
                    continue
                edge_cost = float(np.linalg.norm(neighbor_xy - current_xy))
                next_cost = cost + edge_cost
                if next_cost >= best_cost.get(neighbor_key, math.inf) - 1e-9:
                    continue
                try:
                    neighbor_joints = self._outer_pose_joints(
                        neighbor_xy,
                        z_m,
                        rotation,
                        seed=current_joints,
                        stage=stage,
                    )
                    self._preflight_radial_joint_path(
                        current_joints,
                        neighbor_joints,
                        allowed_tag=allowed_tag,
                        stage=stage,
                        attached_pose=attached_pose,
                    )
                except Exception as exc:  # noqa: BLE001
                    if len(rejected) < 8:
                        rejected.append(str(exc))
                    continue
                best_cost[neighbor_key] = next_cost
                parent[neighbor_key] = key
                joints_by_key[neighbor_key] = neighbor_joints
                xy_by_key[neighbor_key] = neighbor_xy
                sequence += 1
                heuristic = float(np.linalg.norm(end_xy - neighbor_xy))
                heapq.heappush(
                    queue,
                    (
                        next_cost + heuristic,
                        next_cost,
                        sequence,
                        neighbor_key,
                    ),
                )

        details = "; ".join(rejected[:3]) or "no collision-free grid route"
        raise CollisionPlanError(
            f"{stage} could not find a validated outer XY route after "
            f"{expansions} grid expansions; direct route: {direct_error}; "
            f"grid: {details}"
        )

    def _preflight_radial_stage(
        self,
        stage: RadialJointStagePlan,
        *,
        attached_pose: tuple[int, np.ndarray, np.ndarray] | None = None,
    ) -> None:
        self._validate_radial_wrist_travel(stage.name, stage.waypoints)
        for start, end in zip(stage.waypoints, stage.waypoints[1:]):
            self._preflight_radial_joint_path(
                start,
                end,
                allowed_tag=stage.allowed_tag,
                stage=stage.name,
                attached_pose=attached_pose,
            )
        self._preview_joint_target(stage.waypoints[-1], attached_pose)

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
        *,
        gentle_final: bool = False,
        smooth_stage: bool = False,
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
        path_duration = cumulative[-1]
        total_duration = path_duration
        if smooth_stage:
            total_duration *= RADIAL_CARRIED_SMOOTH_DURATION_SCALE
        started = float(self.data.time)
        segment_index = 0
        while self.data.time - started < total_duration:
            elapsed = self.data.time - started
            path_elapsed = elapsed
            if smooth_stage:
                path_elapsed = _smoothstep(elapsed / total_duration) * path_duration
            while (
                segment_index < len(cumulative) - 2
                and path_elapsed > cumulative[segment_index + 1]
            ):
                segment_index += 1
            start_time = cumulative[segment_index]
            end_time = cumulative[segment_index + 1]
            phase = float(
                np.clip(
                    (path_elapsed - start_time)
                    / max(end_time - start_time, 1e-6),
                    0.0,
                    1.0,
                )
            )
            if (
                gentle_final
                and not smooth_stage
                and segment_index == len(waypoints) - 2
            ):
                # Doubling this segment's duration and using this ease-out keeps
                # its initial commanded velocity continuous, then reaches zero
                # velocity smoothly at the final rotation target.
                phase = 1.0 - (1.0 - phase) ** 2
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


    def _nearest_equivalent_joints(
        self,
        target: np.ndarray,
        reference: np.ndarray,
    ) -> np.ndarray:
        target = np.asarray(target, dtype=float).copy()
        reference = np.asarray(reference, dtype=float)
        full_turn = 2.0 * math.pi

        for index in range(len(target)):
            value = float(target[index])
            joint_id = self.model.joint(f"joint{index + 1}").id
            if not self.model.jnt_limited[joint_id]:
                turns = round((float(reference[index]) - value) / full_turn)
                target[index] = value + turns * full_turn
                continue

            lower, upper = (
                float(limit) for limit in self.model.jnt_range[joint_id]
            )
            # Choose among equivalent revolute representations that are
            # genuinely inside the joint range. Selecting the numerically
            # nearest representation first and clipping afterward can turn a
            # valid pose into a different physical pose at a +/-2*pi limit.
            tolerance = 1e-9
            min_turns = math.ceil(
                (lower - value - tolerance) / full_turn
            )
            max_turns = math.floor(
                (upper - value + tolerance) / full_turn
            )
            if min_turns > max_turns:
                raise CollisionPlanError(
                    f"joint{index + 1} target {value:.6f} rad has no "
                    f"equivalent representation inside limits "
                    f"[{lower:.6f}, {upper:.6f}] rad"
                )
            preferred_turns = round(
                (float(reference[index]) - value) / full_turn
            )
            turns = int(np.clip(preferred_turns, min_turns, max_turns))
            target[index] = value + turns * full_turn

        return target

    def _observation_home_joints(self) -> np.ndarray:
        if self.planner_backend == MUJOCO_PLANNER_RADIAL:
            return self._radial_observation_home_joints(
                seed=self.current_joint_target,
            )
        return np.asarray(self.home, dtype=float).copy()

    def _radial_observation_home_joints(
        self,
        *,
        seed: np.ndarray,
    ) -> np.ndarray:
        library = self._load_radial_motion_library()
        low_radius, high_radius = library.radius_bounds_m
        radius_m = float(
            np.clip(
                RADIAL_OBSERVATION_HOME_RADIUS_M,
                low_radius,
                high_radius,
            )
        )
        z_m = float(
            np.clip(
                RADIAL_OBSERVATION_HOME_Z_M,
                library.grasp_z_m,
                library.max_vertical_z_m,
            )
        )
        return self._radial_pose_joints(
            radius_m,
            math.radians(RADIAL_OBSERVATION_HOME_THETA_DEG),
            z_m,
            self._target_rotation(RADIAL_OBSERVATION_HOME_YAW_DEG),
            seed=np.asarray(seed, dtype=float),
            stage="observation home",
        )


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
        if self.planner_backend == MUJOCO_PLANNER_RADIAL:
            self._preflight_radial_joint_path(
                start_joints,
                target_joints,
                allowed_tag=None,
                stage="return home",
            )
        else:
            self._preflight_joint_path(
                start_joints,
                target_joints,
                allowed_tag=None,
            )
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
            motion_time_scale=self._motion_time_scale(),
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
    def _preflight_joint_path(
        self,
        start: np.ndarray,
        end: np.ndarray,
        *,
        allowed_tag: Optional[str],
        attached_pose: tuple[int, np.ndarray, np.ndarray] | None = None,
        sample_count: int = 18,
        sample_callback: Callable[[mujoco.MjData, float], None] | None = None,
    ) -> None:
        probe = mujoco.MjData(self.model)
        probe.qpos[:] = self.data.qpos
        probe.eq_active[:] = self.data.eq_active
        allowed_body = self._component_body_ids.get(allowed_tag or "")
        attached_probe_state = attached_pose
        if (
            attached_probe_state is None
            and allowed_tag is not None
            and allowed_tag in self.scene.components
        ):
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
        for fraction in np.linspace(0.0, 1.0, max(2, int(sample_count))):
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
            if sample_callback is not None:
                sample_callback(probe, float(fraction))
            for contact_index in range(probe.ncon):
                contact = probe.contact[contact_index]
                geom1, geom2 = int(contact.geom1), int(contact.geom2)
                body1 = int(self.model.geom_bodyid[geom1])
                body2 = int(self.model.geom_bodyid[geom2])
                name1 = self.model.body(body1).name
                name2 = self.model.body(body2).name

                if (
                    attached_probe_state is not None
                    and body1 in component_bodies
                    and body2 in component_bodies
                    and body1 != body2
                    and allowed_body in {body1, body2}
                ):
                    other_body = body2 if body1 == allowed_body else body1
                    hit_tag = next(
                        tag
                        for tag, body_id in self._component_body_ids.items()
                        if body_id == other_body
                    )
                    raise CollisionPlanError(
                        f"planned held {allowed_tag} path contacts {hit_tag}"
                    )

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


    def _target_rotation(self, yaw_deg: float) -> np.ndarray:
        return _rotation_z(math.radians(float(yaw_deg))) @ self.home_tcp_rotation

    def _real_pickup_x_adjust_m(self, source_xy: np.ndarray) -> float:
        """Mirror lab_automation CalibrationManager.calculate_x_adjust in meters."""
        x_mm, y_mm = np.asarray(source_xy, dtype=float)[:2] * 1000.0
        x_adjust_mm = (
            (float(y_mm) - REAL_PICKUP_CALIB_Y_LOWER_MM)
            * REAL_PICKUP_CALIB_FINE_ADJUST_COEFF
            + (float(x_mm) - REAL_PICKUP_CALIB_X_OFFSET_MM)
            * REAL_PICKUP_CALIB_FINE_ADJUST_COEFF_X
        )
        return float(x_adjust_mm) / 1000.0

    def _real_pickup_retract_z(self, grasp_z: float) -> float:
        return float(
            min(
                max(
                    float(grasp_z) + REAL_PICKUP_RETRACT_DELTA_M,
                    REAL_PICKUP_MIN_RETRACT_Z_M,
                ),
                MAX_TRAVEL_CLEARANCE_Z_M,
            )
        )

    def _real_pickup_base_joint_rad(self, source_xy: np.ndarray) -> float:
        x_m, y_m = np.asarray(source_xy, dtype=float)[:2]
        angle_deg = math.degrees(math.atan2(float(y_m), float(x_m))) + 180.0
        if x_m < 0.0 <= y_m:
            angle_deg = 280.0
        angle_rad = math.radians(angle_deg)
        angle_rad = (angle_rad + math.pi) % (2.0 * math.pi) - math.pi
        lower, upper = ARM_JOINT_LIMITS_RAD[0]
        return float(
            np.clip(
                angle_rad,
                lower + ARM_JOINT_LIMIT_MARGIN_RAD,
                upper - ARM_JOINT_LIMIT_MARGIN_RAD,
            )
        )

    def _real_pickup_adapter_targets(
        self,
        source_xy: np.ndarray,
        source_rotation: np.ndarray,
        *,
        grasp_z: float,
        grasp_policy: str = "short_edges",
        pickup_context: str = "table",
    ) -> RealPickupAdapterTargets:
        source_xy = np.asarray(source_xy, dtype=float)[:2]
        x_adjust_m = self._real_pickup_x_adjust_m(source_xy)
        local_camera_to_gripper_offset = (
            np.asarray(REAL_PICKUP_CAMERA_TO_GRIPPER_OFFSET_M, dtype=float)
            + np.array((x_adjust_m, 0.0), dtype=float)
        )
        quarter = _pickup_quarter_frame(source_xy)
        radial_inner_flip = False
        if pickup_context == "storage":
            camera_yaw_deg = STORAGE_PICKUP_CAMERA_YAW_DEG
            inward_xy = -source_xy / max(float(np.linalg.norm(source_xy)), 1e-9)
            workspace_quarter = "storage_bottom_left"
            quarter_oriented = False
            base_first = True
        elif pickup_context == "table":
            camera_yaw_deg = (
                REAL_PICKUP_CANONICAL_RIGHT_CAMERA_YAW_DEG
                + quarter.rotation_deg
            )
            inward_xy = np.asarray(quarter.inward_xy, dtype=float)
            workspace_quarter = quarter.name
            quarter_oriented = True
            base_first = False
        else:
            raise SimulatorError(f"unknown pickup context {pickup_context!r}")

        def camera_geometry(yaw_deg: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
            camera_xy_value = (
                source_xy
                + REAL_PICKUP_EDGE_X_NUDGE_M * inward_xy
                + _rotate_xy_deg(
                    REAL_PICKUP_CAMERA_PREOFFSET_M,
                    yaw_deg,
                )
            )
            camera_offset_value = _rotate_xy_deg(
                local_camera_to_gripper_offset,
                yaw_deg,
            )
            aligned_camera_xy_value = source_xy - camera_offset_value
            return (
                camera_xy_value,
                camera_offset_value,
                aligned_camera_xy_value,
            )

        camera_xy, camera_to_gripper_offset, aligned_camera_xy = camera_geometry(
            camera_yaw_deg
        )
        if pickup_context == "table":
            radial_min_m = self._radial_min_radius_m()
            primary_min_radius = min(
                float(np.linalg.norm(camera_xy)),
                float(np.linalg.norm(aligned_camera_xy)),
            )
            if primary_min_radius < radial_min_m - 1e-6:
                flipped_yaw_deg = (
                    float(camera_yaw_deg) + 180.0 + 180.0
                ) % 360.0 - 180.0
                (
                    flipped_camera_xy,
                    flipped_camera_offset,
                    flipped_aligned_camera_xy,
                ) = camera_geometry(flipped_yaw_deg)
                flipped_min_radius = min(
                    float(np.linalg.norm(flipped_camera_xy)),
                    float(np.linalg.norm(flipped_aligned_camera_xy)),
                )
                if flipped_min_radius > primary_min_radius:
                    camera_yaw_deg = flipped_yaw_deg
                    camera_xy = flipped_camera_xy
                    camera_to_gripper_offset = flipped_camera_offset
                    aligned_camera_xy = flipped_aligned_camera_xy
                    radial_inner_flip = True

        camera_rotation = self._target_rotation(camera_yaw_deg)
        source_yaw_deg = self._target_yaw_from_rotation(source_rotation)
        if grasp_policy == "short_edges":
            grasp_yaw_deg, grasp_yaw_offset_deg = _short_edge_grasp_yaw(
                source_yaw_deg,
                camera_yaw_deg,
            )
        else:
            raise SimulatorError(f"unknown grasp policy {grasp_policy!r}")
        grasp_rotation = self._target_rotation(grasp_yaw_deg)
        # The real fine-adjust loop moves until the tag is centered in the
        # gripper camera. At that point applying the calibrated camera-to-
        # gripper offset puts the TCP over the component center. Keep the rough
        # camera pose as the first stage, but model that centering result
        # explicitly instead of treating fine adjustment as a no-op.
        gripper_xy = (
            aligned_camera_xy + camera_to_gripper_offset
        )
        approach_z = max(
            float(grasp_z) + APPROACH_CLEARANCE_M,
            REAL_PICKUP_CAMERA_APPROACH_Z_M,
        )
        return RealPickupAdapterTargets(
            camera_xy=camera_xy,
            aligned_camera_xy=aligned_camera_xy,
            gripper_xy=gripper_xy,
            camera_rotation=camera_rotation,
            grasp_rotation=grasp_rotation,
            base_joint_rad=self._real_pickup_base_joint_rad(source_xy),
            approach_z=float(approach_z),
            grasp_z=float(grasp_z),
            retract_z=self._real_pickup_retract_z(grasp_z),
            x_adjust_m=float(x_adjust_m),
            grasp_yaw_offset_deg=grasp_yaw_offset_deg,
            workspace_quarter=workspace_quarter,
            camera_yaw_deg=float(camera_yaw_deg),
            quarter_oriented=quarter_oriented,
            base_first=base_first,
            radial_inner_flip=radial_inner_flip,
        )

    def _log_real_pickup_adapter(
        self,
        tag_id: str,
        adapter: RealPickupAdapterTargets,
    ) -> None:
        self._log(
            "real_pickup_adapter_targets",
            tag_id=tag_id,
            camera_xy_m=adapter.camera_xy,
            aligned_camera_xy_m=adapter.aligned_camera_xy,
            gripper_xy_m=adapter.gripper_xy,
            approach_z_m=adapter.approach_z,
            grasp_z_m=adapter.grasp_z,
            retract_z_m=adapter.retract_z,
            x_adjust_m=adapter.x_adjust_m,
            base_joint_rad=adapter.base_joint_rad,
            grasp_yaw_deg=self._target_yaw_from_rotation(
                adapter.grasp_rotation
            ),
            grasp_yaw_offset_deg=adapter.grasp_yaw_offset_deg,
            workspace_quarter=adapter.workspace_quarter,
            camera_yaw_deg=adapter.camera_yaw_deg,
            quarter_oriented=adapter.quarter_oriented,
            base_first=adapter.base_first,
            radial_inner_flip=adapter.radial_inner_flip,
        )

    def _real_pickup_pregrasp_stage_specs(
        self,
        tag_id: str,
        adapter: RealPickupAdapterTargets,
    ) -> tuple[ManualPickupStageSpec, ...]:
        return (
            ManualPickupStageSpec(
                "pickup camera hover",
                np.array((*adapter.camera_xy, adapter.approach_z), dtype=float),
                adapter.camera_rotation,
                120.0,
                None,
            ),
            ManualPickupStageSpec(
                "pickup vision fine adjust",
                np.array(
                    (*adapter.aligned_camera_xy, adapter.approach_z),
                    dtype=float,
                ),
                adapter.camera_rotation,
                120.0,
                None,
            ),
            ManualPickupStageSpec(
                "pickup camera-to-gripper offset",
                np.array((*adapter.gripper_xy, adapter.approach_z), dtype=float),
                adapter.camera_rotation,
                50.0,
                None,
            ),
            ManualPickupStageSpec(
                "pickup wrist align",
                np.array((*adapter.gripper_xy, adapter.approach_z), dtype=float),
                adapter.grasp_rotation,
                50.0,
                None,
            ),
            ManualPickupStageSpec(
                "lower around box",
                np.array((*adapter.gripper_xy, adapter.grasp_z), dtype=float),
                adapter.grasp_rotation,
                70.0,
                tag_id,
            ),
        )

    def _real_pickup_lift_stage_spec(
        self,
        tag_id: str,
        adapter: RealPickupAdapterTargets,
    ) -> ManualPickupStageSpec:
        return ManualPickupStageSpec(
            "pickup clearance lift",
            np.array((*adapter.gripper_xy, adapter.retract_z), dtype=float),
            adapter.grasp_rotation,
            70.0,
            tag_id,
        )


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
            self._preflight_joint_path(seed, target, allowed_tag=allowed_tag)
            seed = target

    def _validate_destination(
        self,
        spec: ComponentSpec,
        target_xy: np.ndarray,
        target_rotation_deg: float = 0.0,
    ) -> None:
        bounds = self.scene.lab_bounds_mm
        x_mm, y_mm = target_xy * 1000.0
        half_width_m, half_depth_m = spec.footprint_half_extents_m(
            target_rotation_deg
        )
        half_width_mm = half_width_m * 1000.0
        half_depth_mm = half_depth_m * 1000.0
        frame_clearance_mm = float(self.scene.frame_safety_clearance_mm)
        if not (
            bounds["x_min"] + frame_clearance_mm + half_width_mm
            <= x_mm
            <= bounds["x_max"] - frame_clearance_mm - half_width_mm
            and bounds["y_min"] + frame_clearance_mm + half_depth_mm
            <= y_mm
            <= bounds["y_max"] - frame_clearance_mm - half_depth_mm
        ):
            if frame_clearance_mm > 0:
                raise SimulatorError(
                    f"destination for {spec.tag_id} enters the 3 in "
                    f"({frame_clearance_mm:.1f} mm) frame safety boundary; "
                    "the entire component must remain inside the safe area"
                )
            raise SimulatorError(
                f"destination for {spec.tag_id} places part outside table bounds"
            )

        manual_corner_cutoff_mm = float(
            self.scene.manual_motion_corner_cutoff_mm
        )
        if (
            self.planner_backend == MUJOCO_PLANNER_RADIAL
            and not _manual_motion_workspace_allows_xy(
                np.array((x_mm, y_mm), dtype=float),
                manual_corner_cutoff_mm,
            )
        ):
            raise SimulatorError(
                f"destination for {spec.tag_id} enters an unvalidated corner "
                f"of the manual pickup/place workspace; at least one component-"
                f"center coordinate must stay within +/-"
                f"{manual_corner_cutoff_mm:.1f} mm"
            )

        for other_tag, other_spec in self.scene.components.items():
            if other_tag == spec.tag_id:
                continue
            other_position, _ = self._object_pose(other_tag)
            separation = np.abs(target_xy - other_position[:2])
            required = np.array(
                (
                    (spec.footprint_width_m + other_spec.footprint_width_m) / 2.0,
                    (spec.footprint_depth_m + other_spec.footprint_depth_m) / 2.0,
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
        try:
            self._emit_progress(
                f"Simulator executing {name}.",
                phase="manual_execute",
                stage=name,
            )
            arm.set_position_aa(
                self._pose_for(position_m, rotation),
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

    def _execute_manual_cartesian_stage(
        self,
        name: str,
        arm: XArmAPI,
        position_m: np.ndarray,
        rotation: np.ndarray,
        *,
        speed_mm_s: float,
        allowed_tag: str | None,
    ) -> None:
        previous_allowed = self.allowed_collision_tag
        self.allowed_collision_tag = allowed_tag
        try:
            self._move_stage(
                name,
                arm,
                np.asarray(position_m, dtype=float),
                np.asarray(rotation, dtype=float),
                speed_mm_s=speed_mm_s,
            )
        finally:
            self.allowed_collision_tag = previous_allowed


    def _motion_time_scale(self) -> float:
        return _env_float(
            MOTION_TIME_SCALE_ENV_VAR,
            MOTION_TIME_SCALE,
            minimum=0.25,
            maximum=2.0,
        )


    def _target_yaw_from_rotation(self, rotation: np.ndarray) -> float:
        relative = np.asarray(rotation, dtype=float) @ self.home_tcp_rotation.T
        return math.degrees(math.atan2(relative[1, 0], relative[0, 0]))


    def _scaled_manual_duration_s(
        self,
        duration_s: float,
        *,
        minimum_s: float,
    ) -> float:
        """Apply the configured speed scale to simulated motion phases."""

        return max(
            float(duration_s) * self._motion_time_scale(),
            float(minimum_s),
        )
    def _radial_outer_portal_xy(self, xy_m: np.ndarray) -> np.ndarray:
        xy = np.asarray(xy_m, dtype=float)[:2]
        radius = float(np.linalg.norm(xy))
        if radius <= 1e-9:
            raise CollisionPlanError("outer radial route cannot use zero radius")
        portal_radius = self._load_radial_motion_library().radius_bounds_m[1]
        return xy * (portal_radius / radius)

    def _radial_outer_portal_candidates(
        self,
        xy_m: np.ndarray,
    ) -> tuple[np.ndarray, ...]:
        xy = np.asarray(xy_m, dtype=float)[:2]
        if float(np.linalg.norm(xy)) <= 1e-9:
            raise CollisionPlanError("outer portal search cannot use zero radius")
        portal_radius = self._load_radial_motion_library().radius_bounds_m[1]
        base_theta = math.atan2(float(xy[1]), float(xy[0]))
        offsets_deg = [0.0]
        offset_deg = RADIAL_OUTER_PORTAL_ANGLE_STEP_DEG
        while offset_deg <= RADIAL_OUTER_PORTAL_MAX_OFFSET_DEG + 1e-9:
            offsets_deg.extend((-offset_deg, offset_deg))
            offset_deg += RADIAL_OUTER_PORTAL_ANGLE_STEP_DEG
        return tuple(
            np.array(
                (
                    portal_radius
                    * math.cos(base_theta + math.radians(offset)),
                    portal_radius
                    * math.sin(base_theta + math.radians(offset)),
                ),
                dtype=float,
            )
            for offset in offsets_deg
        )

    def _radial_no_weld_safe_target_xy(
        self,
        spec: ComponentSpec,
        target_xy: np.ndarray,
        target_rotation_deg: float,
    ) -> np.ndarray:
        target = np.asarray(target_xy, dtype=float)[:2].copy()
        half_x_m, half_y_m = spec.footprint_half_extents_m(target_rotation_deg)
        bounds = self.scene.lab_bounds_mm
        frame_m = float(self.scene.frame_safety_clearance_mm) / 1000.0
        buffer_m = RADIAL_NO_WELD_BOUNDARY_BUFFER_M
        adjusted = np.array(
            (
                float(
                    np.clip(
                        target[0],
                        bounds["x_min"] / 1000.0
                        + frame_m
                        + half_x_m
                        + buffer_m,
                        bounds["x_max"] / 1000.0
                        - frame_m
                        - half_x_m
                        - buffer_m,
                    )
                ),
                float(
                    np.clip(
                        target[1],
                        bounds["y_min"] / 1000.0
                        + frame_m
                        + half_y_m
                        + buffer_m,
                        bounds["y_max"] / 1000.0
                        - frame_m
                        - half_y_m
                        - buffer_m,
                    )
                ),
            ),
            dtype=float,
        )
        if not np.allclose(adjusted, target, atol=1e-9, rtol=0.0):
            self._emit_progress(
                "Simulator applying a 20 mm no-weld settling buffer at the "
                "frame boundary.",
                phase="radial_preflight",
                stage="frame settling buffer",
                requested_xy_mm=target * 1000.0,
                planned_xy_mm=adjusted * 1000.0,
            )
            self._log(
                "radial_no_weld_boundary_buffer_applied",
                tag_id=spec.tag_id,
                requested_xy_m=target,
                planned_xy_m=adjusted,
                buffer_m=buffer_m,
            )
        return adjusted

    def _outer_wrist_target_joints(
        self,
        current: np.ndarray,
        xy_m: np.ndarray,
        z_m: float,
        source_rotation: np.ndarray,
        target_rotation: np.ndarray,
        *,
        stage: str,
    ) -> np.ndarray:
        source_yaw = math.radians(
            self._target_yaw_from_rotation(source_rotation)
        )
        target_yaw = math.radians(
            self._target_yaw_from_rotation(target_rotation)
        )
        shortest_delta = self._shortest_angle_delta_rad(source_yaw, target_yaw)
        candidates: list[tuple[float, np.ndarray]] = []
        for turns in range(-1, 2):
            yaw_delta = shortest_delta + turns * 2.0 * math.pi
            target = np.asarray(current, dtype=float).copy()
            target[6] -= yaw_delta
            joint_id = self.model.joint("joint7").id
            if self.model.jnt_limited[joint_id]:
                lower, upper = self.model.jnt_range[joint_id]
                if not float(lower) <= float(target[6]) <= float(upper):
                    continue
            try:
                self._validate_outer_pose_target(
                    target,
                    np.array((*np.asarray(xy_m, dtype=float)[:2], float(z_m))),
                    target_rotation,
                    stage=stage,
                )
            except Exception:
                continue
            candidates.append((abs(yaw_delta), target))
        if not candidates:
            raise CollisionPlanError(
                f"{stage} has no wrist-only rotation inside joint limits"
            )
        return min(candidates, key=lambda item: item[0])[1].copy()

    def _plan_radial_outer_manual_pickup(
        self,
        tag_id: str,
        adapter: RealPickupAdapterTargets,
    ) -> tuple[
        tuple[RadialJointStagePlan, ...],
        RadialJointStagePlan,
    ]:
        library = self._load_radial_motion_library()
        portal_radius = library.radius_bounds_m[1]
        camera_radius = float(np.linalg.norm(adapter.camera_xy))
        aligned_camera_radius = float(
            np.linalg.norm(adapter.aligned_camera_xy)
        )
        gripper_radius = float(np.linalg.norm(adapter.gripper_xy))
        current = self.current_joint_target.copy()
        pregrasp: list[RadialJointStagePlan] = []

        if adapter.base_first:
            base_aligned = current.copy()
            base_aligned[0] = adapter.base_joint_rad
            base_stage = RadialJointStagePlan(
                "storage pickup base align",
                (current.copy(), base_aligned.copy()),
                None,
            )
            self._preflight_radial_stage(base_stage)
            pregrasp.append(base_stage)
            current = base_aligned

        camera_portal = (
            self._radial_outer_portal_xy(adapter.camera_xy)
            if camera_radius > portal_radius + 1e-6
            else np.asarray(adapter.camera_xy, dtype=float).copy()
        )
        camera_carry = self._radial_xy_pose_joints(
            camera_portal,
            library.carry_z_m,
            adapter.camera_rotation,
            seed=current,
            stage="pickup camera portal",
        )
        camera_portal_stage = RadialJointStagePlan(
            "pickup camera portal",
            (current.copy(), camera_carry.copy()),
            None,
        )
        self._preflight_radial_stage(camera_portal_stage)
        pregrasp.append(camera_portal_stage)
        current = camera_carry

        if camera_radius > portal_radius + 1e-6:
            portal_lower = RadialJointStagePlan(
                "pickup camera portal lower",
                self._radial_vertical_waypoints(
                    camera_portal,
                    library.carry_z_m,
                    RADIAL_OUTER_CARRY_Z_M,
                    adapter.camera_rotation,
                    seed=current,
                    stage="pickup camera portal lower",
                ),
                None,
            )
            self._preflight_radial_stage(portal_lower)
            pregrasp.append(portal_lower)
            current = portal_lower.waypoints[-1].copy()

            camera_outer = RadialJointStagePlan(
                "pickup camera outer approach",
                self._outer_xy_route_waypoints(
                    camera_portal,
                    adapter.camera_xy,
                    RADIAL_OUTER_CARRY_Z_M,
                    adapter.camera_rotation,
                    seed=current,
                    stage="pickup camera outer approach",
                    allowed_tag=None,
                    attached_pose=None,
                ),
                None,
            )
            self._preflight_radial_stage(camera_outer)
            pregrasp.append(camera_outer)
            current = camera_outer.waypoints[-1].copy()

            camera_raise = RadialJointStagePlan(
                "pickup camera hover",
                self._outer_vertical_waypoints(
                    adapter.camera_xy,
                    RADIAL_OUTER_CARRY_Z_M,
                    adapter.approach_z,
                    adapter.camera_rotation,
                    seed=current,
                    stage="pickup camera hover",
                ),
                None,
            )
        else:
            camera_raise = RadialJointStagePlan(
                "pickup camera hover",
                self._radial_vertical_waypoints(
                    adapter.camera_xy,
                    library.carry_z_m,
                    adapter.approach_z,
                    adapter.camera_rotation,
                    seed=current,
                    stage="pickup camera hover",
                ),
                None,
            )
        self._preflight_radial_stage(camera_raise)
        pregrasp.append(camera_raise)
        current = camera_raise.waypoints[-1].copy()

        fine_adjust = RadialJointStagePlan(
            "pickup vision fine adjust",
            self._outer_xy_route_waypoints(
                adapter.camera_xy,
                adapter.aligned_camera_xy,
                adapter.approach_z,
                adapter.camera_rotation,
                seed=current,
                stage="pickup vision fine adjust",
                allowed_tag=None,
                attached_pose=None,
            ),
            None,
        )
        self._preflight_radial_stage(fine_adjust)
        pregrasp.append(fine_adjust)
        current = fine_adjust.waypoints[-1].copy()

        offset = RadialJointStagePlan(
            "pickup camera-to-gripper offset",
            self._outer_xy_route_waypoints(
                adapter.aligned_camera_xy,
                adapter.gripper_xy,
                adapter.approach_z,
                adapter.camera_rotation,
                seed=current,
                stage="pickup camera-to-gripper offset",
                allowed_tag=None,
                attached_pose=None,
            ),
            None,
        )
        self._preflight_radial_stage(offset)
        pregrasp.append(offset)
        current = offset.waypoints[-1].copy()

        wrist_target = self._outer_wrist_target_joints(
            current,
            adapter.gripper_xy,
            adapter.approach_z,
            adapter.camera_rotation,
            adapter.grasp_rotation,
            stage="pickup wrist align",
        )
        wrist = RadialJointStagePlan(
            "pickup wrist align",
            (current.copy(), wrist_target.copy()),
            None,
        )
        self._preflight_radial_stage(wrist)
        pregrasp.append(wrist)
        current = wrist_target

        lower = RadialJointStagePlan(
            "lower around box",
            self._outer_vertical_waypoints(
                adapter.gripper_xy,
                adapter.approach_z,
                adapter.grasp_z,
                adapter.grasp_rotation,
                seed=current,
                stage="lower around box",
            ),
            tag_id,
        )
        self._preflight_radial_stage(lower)
        pregrasp.append(lower)

        spec = self.scene.components[tag_id]
        self._gripper_position = GRIPPER_CLOSED
        self.data.ctrl[ARM_DOF] = GRIPPER_CLOSED
        self._validate_grasp_envelope(spec)
        mujoco.mj_forward(self.model, self.data)
        attached_pose = self._component_relative_pose(tag_id)
        lift_z = (
            RADIAL_OUTER_CARRY_Z_M
            if gripper_radius > portal_radius + 1e-6
            else adapter.retract_z
        )
        vertical_builder = (
            self._outer_vertical_waypoints
            if gripper_radius > portal_radius + 1e-6
            else self._radial_vertical_waypoints
        )
        lift = RadialJointStagePlan(
            "pickup clearance lift",
            vertical_builder(
                adapter.gripper_xy,
                adapter.grasp_z,
                lift_z,
                adapter.grasp_rotation,
                seed=lower.waypoints[-1],
                stage="pickup clearance lift",
            ),
            tag_id,
        )
        self._preflight_radial_stage(lift, attached_pose=attached_pose)
        self._log(
            "radial_outer_pickup_plan_success",
            tag_id=tag_id,
            camera_radius_m=camera_radius,
            aligned_camera_radius_m=aligned_camera_radius,
            gripper_radius_m=gripper_radius,
            portal_radius_m=portal_radius,
            pickup_lift_z_m=lift_z,
        )
        return tuple(pregrasp), lift

    def _plan_radial_outer_source_egress(
        self,
        tag_id: str,
        start_xy: np.ndarray,
        rotation: np.ndarray,
    ) -> tuple[tuple[RadialJointStagePlan, ...], np.ndarray]:
        library = self._load_radial_motion_library()
        snapshot = self._motion_snapshot()
        errors: list[str] = []
        for index, portal_xy in enumerate(
            self._radial_outer_portal_candidates(start_xy)
        ):
            self._restore_motion_snapshot(snapshot)
            attached_pose = self._component_relative_pose(tag_id)
            try:
                route = RadialJointStagePlan(
                    "outer pickup egress",
                    self._outer_xy_route_waypoints(
                        start_xy,
                        portal_xy,
                        RADIAL_OUTER_CARRY_Z_M,
                        rotation,
                        seed=self.current_joint_target,
                        stage="outer pickup egress",
                        allowed_tag=tag_id,
                        attached_pose=attached_pose,
                    ),
                    tag_id,
                )
                self._preflight_radial_stage(route, attached_pose=attached_pose)

                lift = RadialJointStagePlan(
                    "outer pickup portal lift",
                    self._radial_vertical_waypoints(
                        portal_xy,
                        RADIAL_OUTER_CARRY_Z_M,
                        library.carry_z_m,
                        rotation,
                        seed=route.waypoints[-1],
                        stage="outer pickup portal lift",
                    ),
                    tag_id,
                )
                self._preflight_radial_stage(lift, attached_pose=attached_pose)
                self._log(
                    "radial_outer_source_portal_selected",
                    tag_id=tag_id,
                    candidate_index=index,
                    portal_xy_m=portal_xy,
                )
                if index:
                    self._emit_progress(
                        "Simulator selected an alternate pickup portal to "
                        "avoid obstacles.",
                        phase="radial_preflight",
                        stage="outer pickup portal selection",
                        candidate_index=index,
                        portal_xy_mm=portal_xy * 1000.0,
                    )
                return (route, lift), portal_xy.copy()
            except Exception as exc:  # noqa: BLE001
                errors.append(f"portal {index + 1}: {exc}")
        self._restore_motion_snapshot(snapshot)
        raise CollisionPlanError(
            "outer pickup could not find a validated portal; "
            + "; ".join(errors[:4])
        )

    def _plan_radial_outer_target_ingress(
        self,
        tag_id: str,
        target_xy: np.ndarray,
        rotation: np.ndarray,
        *,
        portal_xy: np.ndarray | None = None,
    ) -> tuple[RadialJointStagePlan, ...]:
        library = self._load_radial_motion_library()
        if portal_xy is None:
            portal_xy = self._radial_outer_portal_xy(target_xy)
        else:
            portal_xy = np.asarray(portal_xy, dtype=float)[:2]
        attached_pose = self._component_relative_pose(tag_id)
        lower = RadialJointStagePlan(
            "outer placement portal lower",
            self._radial_vertical_waypoints(
                portal_xy,
                library.carry_z_m,
                RADIAL_OUTER_CARRY_Z_M,
                rotation,
                seed=self.current_joint_target,
                stage="outer placement portal lower",
            ),
            tag_id,
        )
        self._preflight_radial_stage(lower, attached_pose=attached_pose)

        route = RadialJointStagePlan(
            "outer placement ingress",
            self._outer_xy_route_waypoints(
                portal_xy,
                target_xy,
                RADIAL_OUTER_CARRY_Z_M,
                rotation,
                seed=lower.waypoints[-1],
                stage="outer placement ingress",
                allowed_tag=tag_id,
                attached_pose=attached_pose,
            ),
            tag_id,
        )
        self._preflight_radial_stage(route, attached_pose=attached_pose)
        return (lower, route)

    def _plan_radial_outer_target_approach(
        self,
        tag_id: str,
        *,
        source_xy: np.ndarray,
        target_xy: np.ndarray,
        source_rotation: np.ndarray,
        target_rotation: np.ndarray,
    ) -> tuple[
        tuple[RadialJointStagePlan, ...],
        tuple[RadialJointStagePlan, ...],
        np.ndarray,
    ]:
        snapshot = self._motion_snapshot()
        errors: list[str] = []
        for index, portal_xy in enumerate(
            self._radial_outer_portal_candidates(target_xy)
        ):
            self._restore_motion_snapshot(snapshot)
            try:
                transfer = self._plan_radial_transfer(
                    tag_id,
                    source_xy=source_xy,
                    target_xy=portal_xy,
                    source_rotation=source_rotation,
                    target_rotation=target_rotation,
                )
                attached_pose = self._component_relative_pose(tag_id)
                for stage in transfer:
                    self._preflight_radial_stage(
                        stage,
                        attached_pose=attached_pose,
                    )
                ingress = self._plan_radial_outer_target_ingress(
                    tag_id,
                    target_xy,
                    target_rotation,
                    portal_xy=portal_xy,
                )
                self._log(
                    "radial_outer_target_portal_selected",
                    tag_id=tag_id,
                    candidate_index=index,
                    portal_xy_m=portal_xy,
                    target_xy_m=target_xy,
                )
                if index:
                    self._emit_progress(
                        "Simulator selected an alternate placement portal to "
                        "avoid obstacles.",
                        phase="radial_preflight",
                        stage="outer placement portal selection",
                        candidate_index=index,
                        portal_xy_mm=portal_xy * 1000.0,
                    )
                return transfer, ingress, portal_xy.copy()
            except Exception as exc:  # noqa: BLE001
                errors.append(f"portal {index + 1}: {exc}")
        self._restore_motion_snapshot(snapshot)
        raise CollisionPlanError(
            "outer placement could not find a validated portal and XY route; "
            + "; ".join(errors[:4])
        )

    def _plan_radial_outer_place(
        self,
        tag_id: str,
        target_xy: np.ndarray,
        target_rotation: np.ndarray,
        *,
        release_z: float,
    ) -> tuple[RadialJointStagePlan, RadialJointStagePlan]:
        current = self.current_joint_target.copy()
        attached_pose = self._component_relative_pose(tag_id)
        descend = RadialJointStagePlan(
            "contact descend destination",
            self._outer_vertical_waypoints(
                target_xy,
                RADIAL_OUTER_CARRY_Z_M,
                release_z,
                target_rotation,
                seed=current,
                stage="contact descend destination",
            ),
            tag_id,
        )
        self._preflight_radial_stage(descend, attached_pose=attached_pose)

        self._gripper_position = GRIPPER_OPEN
        self.data.ctrl[ARM_DOF] = GRIPPER_OPEN
        mujoco.mj_forward(self.model, self.data)
        retreat = RadialJointStagePlan(
            "contact retreat",
            self._outer_vertical_waypoints(
                target_xy,
                release_z,
                RADIAL_OUTER_CARRY_Z_M,
                target_rotation,
                seed=descend.waypoints[-1],
                stage="contact retreat",
            ),
            None,
        )
        self._preflight_radial_stage(retreat)
        return descend, retreat

    def _plan_radial_outer_postplace_egress(
        self,
        target_xy: np.ndarray,
        target_rotation: np.ndarray,
        *,
        portal_xy: np.ndarray | None = None,
    ) -> tuple[RadialJointStagePlan, ...]:
        library = self._load_radial_motion_library()
        if portal_xy is None:
            portal_xy = self._radial_outer_portal_xy(target_xy)
        else:
            portal_xy = np.asarray(portal_xy, dtype=float)[:2]
        route = RadialJointStagePlan(
            "outer post-place egress",
            self._outer_xy_route_waypoints(
                target_xy,
                portal_xy,
                RADIAL_OUTER_CARRY_Z_M,
                target_rotation,
                seed=self.current_joint_target,
                stage="outer post-place egress",
                allowed_tag=None,
                attached_pose=None,
            ),
            None,
        )
        self._preflight_radial_stage(route)

        lift = RadialJointStagePlan(
            "outer post-place portal lift",
            self._radial_vertical_waypoints(
                portal_xy,
                RADIAL_OUTER_CARRY_Z_M,
                library.carry_z_m,
                target_rotation,
                seed=route.waypoints[-1],
                stage="outer post-place portal lift",
            ),
            None,
        )
        self._preflight_radial_stage(lift)
        return (route, lift)

    def _plan_radial_manual_pickup(
        self,
        tag_id: str,
        adapter: RealPickupAdapterTargets,
    ) -> tuple[
        tuple[RadialJointStagePlan, ...],
        RadialJointStagePlan,
    ]:
        library = self._load_radial_motion_library()
        portal_radius = library.radius_bounds_m[1]
        if max(
            float(np.linalg.norm(adapter.camera_xy)),
            float(np.linalg.norm(adapter.aligned_camera_xy)),
            float(np.linalg.norm(adapter.gripper_xy)),
        ) > portal_radius + 1e-6:
            return self._plan_radial_outer_manual_pickup(tag_id, adapter)
        current = self.current_joint_target.copy()
        pregrasp: list[RadialJointStagePlan] = []

        if adapter.base_first:
            base_aligned = current.copy()
            base_aligned[0] = adapter.base_joint_rad
            base_stage = RadialJointStagePlan(
                "storage pickup base align",
                (current.copy(), base_aligned.copy()),
                None,
            )
            self._preflight_radial_stage(base_stage)
            pregrasp.append(base_stage)
            current = base_aligned

        camera_carry = self._radial_xy_pose_joints(
            adapter.camera_xy,
            library.carry_z_m,
            adapter.camera_rotation,
            seed=current,
            stage="pickup camera hover",
        )
        camera_descent = self._radial_vertical_waypoints(
            adapter.camera_xy,
            library.carry_z_m,
            adapter.approach_z,
            adapter.camera_rotation,
            seed=camera_carry,
            stage="pickup camera hover",
        )
        camera_stage = RadialJointStagePlan(
            "pickup camera hover",
            (current.copy(), camera_carry.copy(), *camera_descent[1:]),
            None,
        )
        self._preflight_radial_stage(camera_stage)
        pregrasp.append(camera_stage)
        current = camera_stage.waypoints[-1].copy()

        fine_adjust_stage = RadialJointStagePlan(
            "pickup vision fine adjust",
            self._radial_xy_waypoints(
                adapter.camera_xy,
                adapter.aligned_camera_xy,
                adapter.approach_z,
                adapter.camera_rotation,
                seed=current,
                stage="pickup vision fine adjust",
            ),
            None,
        )
        self._preflight_radial_stage(fine_adjust_stage)
        pregrasp.append(fine_adjust_stage)
        current = fine_adjust_stage.waypoints[-1].copy()

        offset_stage = RadialJointStagePlan(
            "pickup camera-to-gripper offset",
            self._radial_xy_waypoints(
                adapter.aligned_camera_xy,
                adapter.gripper_xy,
                adapter.approach_z,
                adapter.camera_rotation,
                seed=current,
                stage="pickup camera-to-gripper offset",
            ),
            None,
        )
        self._preflight_radial_stage(offset_stage)
        pregrasp.append(offset_stage)
        current = offset_stage.waypoints[-1].copy()

        wrist_target = self._radial_xy_pose_joints(
            adapter.gripper_xy,
            adapter.approach_z,
            adapter.grasp_rotation,
            seed=current,
            stage="pickup wrist align",
        )
        if not np.allclose(
            wrist_target[:6],
            current[:6],
            atol=RADIAL_FIXED_JOINT_TOLERANCE_RAD,
            rtol=0.0,
        ):
            raise SimulatorError(
                "radial pickup wrist alignment changed a non-wrist joint"
            )
        wrist_stage = RadialJointStagePlan(
            "pickup wrist align",
            (current.copy(), wrist_target.copy()),
            None,
        )
        self._preflight_radial_stage(wrist_stage)
        pregrasp.append(wrist_stage)
        current = wrist_target

        lower_stage = RadialJointStagePlan(
            "lower around box",
            self._radial_vertical_waypoints(
                adapter.gripper_xy,
                adapter.approach_z,
                adapter.grasp_z,
                adapter.grasp_rotation,
                seed=current,
                stage="lower around box",
            ),
            tag_id,
        )
        self._preflight_radial_stage(lower_stage)
        pregrasp.append(lower_stage)

        spec = self.scene.components[tag_id]
        self._gripper_position = GRIPPER_CLOSED
        self.data.ctrl[ARM_DOF] = GRIPPER_CLOSED
        self._validate_grasp_envelope(spec)
        mujoco.mj_forward(self.model, self.data)
        attached_pose = self._component_relative_pose(tag_id)

        lift_stage = RadialJointStagePlan(
            "pickup clearance lift",
            self._radial_vertical_waypoints(
                adapter.gripper_xy,
                adapter.grasp_z,
                adapter.retract_z,
                adapter.grasp_rotation,
                seed=lower_stage.waypoints[-1],
                stage="pickup clearance lift",
            ),
            tag_id,
        )
        self._preflight_radial_stage(lift_stage, attached_pose=attached_pose)
        return tuple(pregrasp), lift_stage

    def _plan_radial_place(
        self,
        tag_id: str,
        target_xy: np.ndarray,
        target_rotation: np.ndarray,
        *,
        release_z: float,
    ) -> tuple[RadialJointStagePlan, RadialJointStagePlan]:
        library = self._load_radial_motion_library()
        current = self.current_joint_target.copy()
        descend = RadialJointStagePlan(
            "contact descend destination",
            self._radial_vertical_waypoints(
                target_xy,
                library.carry_z_m,
                release_z,
                target_rotation,
                seed=current,
                stage="contact descend destination",
            ),
            tag_id,
        )
        attached_pose = self._component_relative_pose(tag_id)
        self._preflight_radial_stage(descend, attached_pose=attached_pose)

        self._gripper_position = GRIPPER_OPEN
        self.data.ctrl[ARM_DOF] = GRIPPER_OPEN
        mujoco.mj_forward(self.model, self.data)
        retreat = RadialJointStagePlan(
            "contact retreat",
            self._radial_vertical_waypoints(
                target_xy,
                release_z,
                library.carry_z_m,
                target_rotation,
                seed=descend.waypoints[-1],
                stage="contact retreat",
            ),
            None,
        )
        self._preflight_radial_stage(retreat)
        return descend, retreat

    @staticmethod
    def _shortest_angle_delta_rad(start: float, end: float) -> float:
        return math.atan2(math.sin(float(end) - float(start)), math.cos(float(end) - float(start)))

    @staticmethod
    def _intermediate_scalar_values(
        start: float,
        end: float,
        *,
        max_step: float,
    ) -> list[float]:
        distance = abs(float(end) - float(start))
        if distance <= 1e-9:
            return []
        count = max(1, int(math.ceil(distance / max(float(max_step), 1e-9))))
        return [
            float(start) + (float(end) - float(start)) * index / count
            for index in range(1, count + 1)
        ]

    def _plan_radial_coordinated_rotation(
        self,
        *,
        name: str,
        tag_id: str | None,
        radius_m: float,
        source_theta: float,
        target_theta: float,
        source_rotation: np.ndarray,
        target_rotation: np.ndarray,
        current: np.ndarray,
    ) -> RadialJointStagePlan | None:
        shortest_theta_delta = self._shortest_angle_delta_rad(
            source_theta,
            target_theta,
        )
        source_yaw = math.radians(
            self._target_yaw_from_rotation(source_rotation)
        )
        target_yaw = math.radians(
            self._target_yaw_from_rotation(target_rotation)
        )
        shortest_yaw_delta = self._shortest_angle_delta_rad(
            source_yaw,
            target_yaw,
        )

        # Joint 1 and joint 7 permit multiple equivalent +/- 2*pi
        # representations. Continue from the representation already in use and
        # select an equivalent endpoint that stays inside both joint limits.
        # Re-solving each waypoint and clipping it can otherwise distort the TCP
        # near a wrap boundary even though the physical rotation is valid.
        route_candidates: list[tuple[tuple[float, ...], float, float]] = []
        for theta_turns in range(-1, 2):
            theta_delta = shortest_theta_delta + theta_turns * 2.0 * math.pi
            for yaw_turns in range(-1, 2):
                yaw_delta = shortest_yaw_delta + yaw_turns * 2.0 * math.pi
                endpoint = np.asarray(current, dtype=float).copy()
                endpoint[0] += theta_delta
                endpoint[6] += theta_delta - yaw_delta
                valid = True
                for joint_index in (0, 6):
                    joint_id = self.model.joint(
                        f"joint{joint_index + 1}"
                    ).id
                    if not self.model.jnt_limited[joint_id]:
                        continue
                    lower, upper = self.model.jnt_range[joint_id]
                    if not (
                        float(lower) <= float(endpoint[joint_index]) <= float(upper)
                    ):
                        valid = False
                        break
                if valid:
                    wrist_delta = theta_delta - yaw_delta
                    score = (
                        abs(theta_delta),
                        abs(wrist_delta),
                        abs(yaw_delta),
                        abs(theta_turns) + abs(yaw_turns),
                    )
                    route_candidates.append((score, theta_delta, yaw_delta))

        if not route_candidates:
            raise CollisionPlanError(
                f"{name} has no equivalent base/wrist rotation inside joint limits"
            )
        _, theta_delta, yaw_delta = min(
            route_candidates,
            key=lambda candidate: candidate[0],
        )
        step_rad = math.radians(RADIAL_BASE_ROTATION_STEP_DEG)
        count = max(
            int(math.ceil(abs(theta_delta) / step_rad)),
            int(math.ceil(abs(yaw_delta) / step_rad)),
        )
        if count <= 0:
            return None

        waypoints = [np.asarray(current, dtype=float).copy()]
        fixed_middle = waypoints[0][1:6].copy()
        base_start = float(waypoints[0][0])
        wrist_start = float(waypoints[0][6])
        for index in range(1, count + 1):
            phase = index / count
            theta = source_theta + phase * theta_delta
            yaw = source_yaw + phase * yaw_delta
            rotation = self._target_rotation(math.degrees(yaw))
            waypoint = waypoints[0].copy()
            waypoint[0] = base_start + phase * theta_delta
            waypoint[6] = wrist_start + phase * (theta_delta - yaw_delta)
            self._validate_radial_pose_target(
                waypoint,
                radius_m=radius_m,
                theta_rad=theta,
                z_m=self._load_radial_motion_library().carry_z_m,
                rotation=rotation,
                stage=name,
            )
            if not np.allclose(
                waypoint[1:6],
                fixed_middle,
                atol=RADIAL_FIXED_JOINT_TOLERANCE_RAD,
                rtol=0.0,
            ):
                raise SimulatorError(
                    f"{name} attempted to change a middle arm joint"
                )
            waypoints.append(waypoint.copy())

        max_middle_delta = max(
            float(np.max(np.abs(waypoint[1:6] - fixed_middle)))
            for waypoint in waypoints
        )
        self._log(
            "radial_coordinated_rotation_verified",
            tag_id=tag_id,
            stage=name,
            source_theta_deg=math.degrees(source_theta),
            target_theta_deg=math.degrees(target_theta),
            base_delta_deg=math.degrees(theta_delta),
            shortest_base_delta_deg=math.degrees(shortest_theta_delta),
            source_world_yaw_deg=math.degrees(source_yaw),
            target_world_yaw_deg=math.degrees(target_yaw),
            world_yaw_delta_deg=math.degrees(yaw_delta),
            shortest_world_yaw_delta_deg=math.degrees(shortest_yaw_delta),
            max_middle_joint_delta_rad=max_middle_delta,
            final_duration_scale=RADIAL_ROTATION_FINAL_DURATION_SCALE,
        )
        return RadialJointStagePlan(
            name,
            tuple(waypoints),
            tag_id,
        )

    def _plan_radial_transfer(
        self,
        tag_id: str,
        *,
        source_xy: np.ndarray,
        target_xy: np.ndarray,
        source_rotation: np.ndarray,
        target_rotation: np.ndarray,
    ) -> tuple[RadialJointStagePlan, ...]:
        self._load_radial_motion_library()
        source_radius = float(np.linalg.norm(np.asarray(source_xy, dtype=float)))
        target_radius = float(np.linalg.norm(np.asarray(target_xy, dtype=float)))
        if source_radius <= 1e-6 or target_radius <= 1e-6:
            raise CollisionPlanError("radial planner cannot use zero-radius targets")
        source_theta = math.atan2(float(source_xy[1]), float(source_xy[0]))
        target_theta = math.atan2(float(target_xy[1]), float(target_xy[0]))
        stages: list[RadialJointStagePlan] = []
        current = self.current_joint_target.copy()

        source_carry = self._radial_carry_pose_joints(
            source_radius,
            source_theta,
            source_rotation,
            seed=current,
            stage="radial source carry",
        )
        stages.append(
            RadialJointStagePlan(
                "radial source carry",
                (current.copy(), source_carry.copy()),
                tag_id,
            )
        )
        current = source_carry

        library = self._load_radial_motion_library()
        source_yaw = math.radians(
            self._target_yaw_from_rotation(source_rotation)
        )
        target_yaw = math.radians(
            self._target_yaw_from_rotation(target_rotation)
        )
        needs_rotation = bool(
            abs(self._shortest_angle_delta_rad(source_theta, target_theta))
            > 1e-8
            or abs(self._shortest_angle_delta_rad(source_yaw, target_yaw))
            > 1e-8
        )
        safe_radius_transfer = bool(
            needs_rotation
            and max(source_radius, target_radius)
            > RADIAL_OUTER_ROTATION_RADIUS_M + 1e-6
        )
        if safe_radius_transfer:
            rotation_radius = float(
                np.clip(
                    min(
                        source_radius,
                        target_radius,
                        RADIAL_OUTER_ROTATION_RADIUS_M,
                    ),
                    *library.radius_bounds_m,
                )
            )
            retract_radii = self._intermediate_scalar_values(
                source_radius,
                rotation_radius,
                max_step=RADIAL_TRANSLATION_STEP_MM / 1000.0,
            )
            if retract_radii:
                waypoints = [current.copy()]
                for radius in retract_radii:
                    current = self._radial_carry_pose_joints(
                        radius,
                        source_theta,
                        source_rotation,
                        seed=current,
                        stage="outer transfer radial retract",
                    )
                    waypoints.append(current.copy())
                stages.append(
                    RadialJointStagePlan(
                        "outer transfer radial retract",
                        tuple(waypoints),
                        tag_id,
                    )
                )

            coordinated_rotation = self._plan_radial_coordinated_rotation(
                name="radial coordinated rotate",
                tag_id=tag_id,
                radius_m=rotation_radius,
                source_theta=source_theta,
                target_theta=target_theta,
                source_rotation=source_rotation,
                target_rotation=target_rotation,
                current=current,
            )
            if coordinated_rotation is not None:
                stages.append(coordinated_rotation)
                current = coordinated_rotation.waypoints[-1].copy()

            extend_radii = self._intermediate_scalar_values(
                rotation_radius,
                target_radius,
                max_step=RADIAL_TRANSLATION_STEP_MM / 1000.0,
            )
            if extend_radii:
                waypoints = [current.copy()]
                for radius in extend_radii:
                    current = self._radial_carry_pose_joints(
                        radius,
                        target_theta,
                        target_rotation,
                        seed=current,
                        stage="outer transfer radial extend",
                    )
                    waypoints.append(current.copy())
                stages.append(
                    RadialJointStagePlan(
                        "outer transfer radial extend",
                        tuple(waypoints),
                        tag_id,
                    )
                )
            self._log(
                "radial_transfer_plan_success",
                tag_id=tag_id,
                source_radius_m=source_radius,
                target_radius_m=target_radius,
                rotation_radius_m=rotation_radius,
                source_theta_deg=math.degrees(source_theta),
                target_theta_deg=math.degrees(target_theta),
                safe_radius_transfer=True,
                stages=[
                    {"name": stage.name, "waypoints": len(stage.waypoints)}
                    for stage in stages
                ],
            )
            return tuple(stages)

        radius_waypoints = self._intermediate_scalar_values(
            source_radius,
            target_radius,
            max_step=RADIAL_TRANSLATION_STEP_MM / 1000.0,
        )
        if radius_waypoints:
            waypoints = [current.copy()]
            for radius in radius_waypoints:
                current = self._radial_carry_pose_joints(
                    radius,
                    source_theta,
                    source_rotation,
                    seed=current,
                    stage="radial translate",
                )
                waypoints.append(current.copy())
            stages.append(
                RadialJointStagePlan(
                    "radial translate",
                    tuple(waypoints),
                    tag_id,
                )
            )

        coordinated_rotation = self._plan_radial_coordinated_rotation(
            name="radial coordinated rotate",
            tag_id=tag_id,
            radius_m=target_radius,
            source_theta=source_theta,
            target_theta=target_theta,
            source_rotation=source_rotation,
            target_rotation=target_rotation,
            current=current,
        )
        if coordinated_rotation is not None:
            stages.append(coordinated_rotation)
            current = coordinated_rotation.waypoints[-1].copy()

        self._log(
            "radial_transfer_plan_success",
            tag_id=tag_id,
            source_radius_m=source_radius,
            target_radius_m=target_radius,
            source_theta_deg=math.degrees(source_theta),
            target_theta_deg=math.degrees(target_theta),
            stages=[
                {"name": stage.name, "waypoints": len(stage.waypoints)}
                for stage in stages
            ],
        )
        return tuple(stages)

    def _plan_radial_observation_home(
        self,
        *,
        start_xy: np.ndarray,
        start_rotation: np.ndarray,
    ) -> tuple[RadialJointStagePlan, ...]:
        library = self._load_radial_motion_library()
        start_xy = np.asarray(start_xy, dtype=float)[:2]
        start_radius = float(np.linalg.norm(start_xy))
        if start_radius <= 1e-6:
            raise CollisionPlanError(
                "radial home route cannot start at zero radius"
            )
        start_theta = math.atan2(float(start_xy[1]), float(start_xy[0]))
        home_radius = float(
            np.clip(
                RADIAL_OBSERVATION_HOME_RADIUS_M,
                *library.radius_bounds_m,
            )
        )
        home_theta = math.radians(RADIAL_OBSERVATION_HOME_THETA_DEG)
        home_rotation = self._target_rotation(
            RADIAL_OBSERVATION_HOME_YAW_DEG
        )
        home_xy = np.array(
            (
                home_radius * math.cos(home_theta),
                home_radius * math.sin(home_theta),
            ),
            dtype=float,
        )
        current = self.current_joint_target.copy()
        stages: list[RadialJointStagePlan] = []

        radius_waypoints = self._intermediate_scalar_values(
            start_radius,
            home_radius,
            max_step=RADIAL_TRANSLATION_STEP_MM / 1000.0,
        )
        if radius_waypoints:
            waypoints = [current.copy()]
            for radius_m in radius_waypoints:
                current = self._radial_carry_pose_joints(
                    radius_m,
                    start_theta,
                    start_rotation,
                    seed=current,
                    stage="post-place radial egress",
                )
                waypoints.append(current.copy())
            stages.append(
                RadialJointStagePlan(
                    "post-place radial egress",
                    tuple(waypoints),
                    None,
                )
            )

        coordinated_rotation = self._plan_radial_coordinated_rotation(
            name="home coordinated rotate",
            tag_id=None,
            radius_m=home_radius,
            source_theta=start_theta,
            target_theta=home_theta,
            source_rotation=start_rotation,
            target_rotation=home_rotation,
            current=current,
        )
        if coordinated_rotation is not None:
            stages.append(coordinated_rotation)
            current = coordinated_rotation.waypoints[-1].copy()

        home_z = float(
            np.clip(
                RADIAL_OBSERVATION_HOME_Z_M,
                library.grasp_z_m,
                library.max_vertical_z_m,
            )
        )
        home_vertical = RadialJointStagePlan(
            "home vertical stow",
            self._radial_vertical_waypoints(
                home_xy,
                library.carry_z_m,
                home_z,
                home_rotation,
                seed=current,
                stage="home vertical stow",
            ),
            None,
        )
        stages.append(home_vertical)

        for stage in stages:
            self._preflight_radial_stage(stage)
        expected_home = self._radial_observation_home_joints(
            seed=self.current_joint_target,
        )
        raw_home_match = bool(
            np.allclose(
                self.current_joint_target,
                expected_home,
                atol=RETURN_HOME_JOINT_TOLERANCE_RAD,
                rtol=0.0,
            )
        )
        if not _radial_joint_targets_equivalent(
            self.current_joint_target,
            expected_home,
            atol=RETURN_HOME_JOINT_TOLERANCE_RAD,
        ):
            raise SimulatorError(
                "radial home route did not finish at observation home"
            )
        if not raw_home_match:
            self._log(
                "radial_observation_home_periodic_endpoint",
                planned_endpoint=self.current_joint_target,
                canonical_home=expected_home,
                joint_delta_rad=self.current_joint_target - expected_home,
            )
        self._log(
            "radial_observation_home_plan_success",
            start_radius_m=start_radius,
            start_theta_deg=math.degrees(start_theta),
            home_radius_m=home_radius,
            home_theta_deg=RADIAL_OBSERVATION_HOME_THETA_DEG,
            home_z_m=home_z,
            stages=[
                {"name": stage.name, "waypoints": len(stage.waypoints)}
                for stage in stages
            ],
        )
        return tuple(stages)

    def _component_relative_pose(
        self,
        tag_id: str | None,
    ) -> tuple[int, np.ndarray, np.ndarray] | None:
        if tag_id is None or tag_id not in self.scene.components:
            return None
        spec = self.scene.components[tag_id]
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
        grasp_policy: str = "short_edges",
        pickup_context: str = "table",
    ) -> MoveResult:
        if self.planner_backend == MUJOCO_PLANNER_RADIAL:
            self._rebase_radial_periodic_joint_branches(
                reason="radial transaction start",
            )
        self._log(
            "pick_and_place_start",
            tag_id=tag_id,
            target={
                "x_mm": float(target_x_mm),
                "y_mm": float(target_y_mm),
                "rotation_deg": float(target_rotation_deg),
            },
            planner_backend=self.planner_backend,
            grasp_policy=grasp_policy,
            pickup_context=pickup_context,
            state=self._runtime_snapshot(),
        )
        try:
            if self.planner_backend == MUJOCO_PLANNER_RADIAL:
                result = self._pick_and_place_radial(
                    tag_id,
                    target_x_mm=target_x_mm,
                    target_y_mm=target_y_mm,
                    target_rotation_deg=target_rotation_deg,
                    grasp_policy=grasp_policy,
                    pickup_context=pickup_context,
                )
            else:
                result = self._pick_and_place_custom_ik(
                    tag_id,
                    target_x_mm=target_x_mm,
                    target_y_mm=target_y_mm,
                    target_rotation_deg=target_rotation_deg,
                    grasp_policy=grasp_policy,
                    pickup_context=pickup_context,
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

    def _pick_and_place_radial(
        self,
        tag_id: str,
        *,
        target_x_mm: float,
        target_y_mm: float,
        target_rotation_deg: float,
        grasp_policy: str = "short_edges",
        pickup_context: str = "table",
    ) -> MoveResult:
        if tag_id not in self.scene.components:
            raise SimulatorError(f"MuJoCo body not found for {tag_id}")
        self.stage_trace = []
        spec = self.scene.components[tag_id]
        object_position, source_yaw = self._object_pose(tag_id)
        source_xy = object_position[:2]
        target_xy = np.array((target_x_mm / 1000.0, target_y_mm / 1000.0))
        source_rotation = self._target_rotation(source_yaw)
        grasp_z = spec.grasp_tcp_z_m
        release_z = grasp_z + RELEASE_CLEARANCE_M
        pickup_adapter = self._real_pickup_adapter_targets(
            source_xy,
            source_rotation,
            grasp_z=grasp_z,
            grasp_policy=grasp_policy,
            pickup_context=pickup_context,
        )
        pickup_rotation = pickup_adapter.grasp_rotation
        target_rotation = self._target_rotation(
            target_rotation_deg + pickup_adapter.grasp_yaw_offset_deg
        )
        self._log_real_pickup_adapter(tag_id, pickup_adapter)
        self._validate_destination(spec, target_xy, target_rotation_deg)
        planned_target_xy = self._radial_no_weld_safe_target_xy(
            spec,
            target_xy,
            target_rotation_deg,
        )
        self._validate_destination(
            spec,
            planned_target_xy,
            target_rotation_deg,
        )
        library = self._load_radial_motion_library()
        portal_radius = library.radius_bounds_m[1]
        source_outer = (
            float(np.linalg.norm(pickup_adapter.gripper_xy))
            > portal_radius + 1e-6
        )
        target_outer = (
            float(np.linalg.norm(planned_target_xy)) > portal_radius + 1e-6
        )
        source_transfer_xy = pickup_adapter.gripper_xy.copy()
        target_transfer_xy = planned_target_xy.copy()

        preflight_snapshot = self._motion_snapshot()
        try:
            radial_pickup_stages, radial_lift_stage = self._plan_radial_manual_pickup(
                tag_id,
                pickup_adapter,
            )
            if source_outer:
                (
                    radial_source_outer_stages,
                    source_transfer_xy,
                ) = self._plan_radial_outer_source_egress(
                    tag_id,
                    pickup_adapter.gripper_xy,
                    pickup_rotation,
                )
            else:
                radial_source_outer_stages = ()

            if target_outer:
                (
                    radial_stages,
                    radial_target_outer_stages,
                    target_transfer_xy,
                ) = self._plan_radial_outer_target_approach(
                    tag_id,
                    source_xy=source_transfer_xy,
                    target_xy=planned_target_xy,
                    source_rotation=pickup_rotation,
                    target_rotation=target_rotation,
                )
            else:
                radial_stages = self._plan_radial_transfer(
                    tag_id,
                    source_xy=source_transfer_xy,
                    target_xy=target_transfer_xy,
                    source_rotation=pickup_rotation,
                    target_rotation=target_rotation,
                )
                attached_pose = self._component_relative_pose(tag_id)
                for radial_stage in radial_stages:
                    self._preflight_radial_stage(
                        radial_stage,
                        attached_pose=attached_pose,
                    )
                radial_target_outer_stages = ()
            place_planner = (
                self._plan_radial_outer_place
                if target_outer
                else self._plan_radial_place
            )
            radial_place_stage, radial_retreat_stage = place_planner(
                tag_id,
                planned_target_xy,
                target_rotation,
                release_z=release_z,
            )
            radial_target_egress_stages = (
                self._plan_radial_outer_postplace_egress(
                    planned_target_xy,
                    target_rotation,
                    portal_xy=target_transfer_xy,
                )
                if target_outer
                else ()
            )
            radial_home_stages = self._plan_radial_observation_home(
                start_xy=target_transfer_xy,
                start_rotation=target_rotation,
            )
            self._log(
                "radial_transaction_preflight_success",
                tag_id=tag_id,
                source_outer=source_outer,
                target_outer=target_outer,
                portal_radius_m=portal_radius,
                outer_carry_z_m=RADIAL_OUTER_CARRY_Z_M,
            )
        except Exception as exc:
            self._restore_motion_snapshot(preflight_snapshot)
            self._log(
                "radial_transaction_preflight_rejected",
                tag_id=tag_id,
                error=str(exc),
                state=self._runtime_snapshot(),
            )
            raise
        self._restore_motion_snapshot(preflight_snapshot)

        arm = XArmAPI(runtime=self)
        arm.motion_enable(True)
        arm.set_mode(0)
        arm.set_state(0)
        arm.set_gripper_enable(True)
        arm.set_gripper_speed(2000)

        self.allowed_collision_tag = None
        self.stage_trace.append("open gripper")
        arm.set_gripper_position(GRIPPER_OPEN, wait=True)
        for stage in radial_pickup_stages:
            self._execute_radial_joint_waypoints(
                stage.name,
                stage.waypoints,
                allowed_tag=stage.allowed_tag,
                prevalidated=True,
            )
        self.stage_trace.append("close while stationary")
        arm.set_gripper_position(GRIPPER_CLOSED, wait=True)
        self.stage_trace.append("secure physical grasp")
        self._settle(0.20)
        self._validate_physical_grasp(spec, stage="pickup")
        attached_pose = self._component_relative_pose(tag_id)
        self._execute_radial_joint_waypoints(
            radial_lift_stage.name,
            radial_lift_stage.waypoints,
            allowed_tag=radial_lift_stage.allowed_tag,
            attached_pose=attached_pose,
            prevalidated=True,
        )
        self._validate_physical_grasp(spec, stage="pickup lift")

        for stage in radial_source_outer_stages:
            attached_pose = self._component_relative_pose(tag_id)
            self._execute_radial_joint_waypoints(
                stage.name,
                stage.waypoints,
                allowed_tag=stage.allowed_tag,
                attached_pose=attached_pose,
                prevalidated=True,
            )
            self._validate_physical_grasp(spec, stage=stage.name)

        for stage in radial_stages:
            attached_pose = self._component_relative_pose(tag_id)
            self._execute_radial_joint_waypoints(
                stage.name,
                stage.waypoints,
                allowed_tag=stage.allowed_tag,
                attached_pose=attached_pose,
                prevalidated=True,
            )
            self._validate_physical_grasp(spec, stage=stage.name)

        for stage in radial_target_outer_stages:
            attached_pose = self._component_relative_pose(tag_id)
            self._execute_radial_joint_waypoints(
                stage.name,
                stage.waypoints,
                allowed_tag=stage.allowed_tag,
                attached_pose=attached_pose,
                prevalidated=True,
            )
            self._validate_physical_grasp(spec, stage=stage.name)

        attached_pose = self._component_relative_pose(tag_id)
        self._execute_radial_joint_waypoints(
            radial_place_stage.name,
            radial_place_stage.waypoints,
            allowed_tag=radial_place_stage.allowed_tag,
            attached_pose=attached_pose,
            prevalidated=True,
        )
        self.stage_trace.append("open and release")
        arm.set_gripper_position(GRIPPER_OPEN, wait=True)
        self._settle(0.25)
        self.allowed_collision_tag = None
        self._execute_radial_joint_waypoints(
            radial_retreat_stage.name,
            radial_retreat_stage.waypoints,
            allowed_tag=radial_retreat_stage.allowed_tag,
            prevalidated=True,
        )
        for stage in radial_target_egress_stages:
            self._execute_radial_joint_waypoints(
                stage.name,
                stage.waypoints,
                allowed_tag=stage.allowed_tag,
                prevalidated=True,
            )
        for stage in radial_home_stages:
            self._execute_radial_joint_waypoints(
                stage.name,
                stage.waypoints,
                allowed_tag=stage.allowed_tag,
                prevalidated=True,
            )
        self._rebase_radial_periodic_joint_branches(
            reason="radial observation home",
        )
        return self._placement_result(
            tag_id,
            target_xy=target_xy,
            target_rotation_deg=target_rotation_deg,
            position_tolerance_mm=RADIAL_PLACEMENT_POSITION_TOLERANCE_MM,
            allow_yaw_180_equivalence=True,
            enforce_tolerance=False,
        )

    def _pick_and_place_custom_ik(
        self,
        tag_id: str,
        *,
        target_x_mm: float,
        target_y_mm: float,
        target_rotation_deg: float,
        grasp_policy: str = "short_edges",
        pickup_context: str = "table",
    ) -> MoveResult:
        if tag_id not in self.scene.components:
            raise SimulatorError(f"MuJoCo body not found for {tag_id}")
        self.stage_trace = []
        spec = self.scene.components[tag_id]
        object_position, source_yaw = self._object_pose(tag_id)
        source_xy = object_position[:2]
        target_xy = np.array((target_x_mm / 1000.0, target_y_mm / 1000.0))
        source_rotation = self._target_rotation(source_yaw)
        grasp_z = spec.grasp_tcp_z_m
        release_z = grasp_z + RELEASE_CLEARANCE_M
        pickup_adapter = self._real_pickup_adapter_targets(
            source_xy,
            source_rotation,
            grasp_z=grasp_z,
            grasp_policy=grasp_policy,
            pickup_context=pickup_context,
        )
        target_rotation = self._target_rotation(
            target_rotation_deg + pickup_adapter.grasp_yaw_offset_deg
        )
        self._log_real_pickup_adapter(tag_id, pickup_adapter)
        clearance_z = self._travel_clearance_z(spec)
        self._validate_destination(spec, target_xy, target_rotation_deg)
        pickup_waypoints = [
            (stage.position_m, stage.rotation)
            for stage in self._real_pickup_pregrasp_stage_specs(tag_id, pickup_adapter)
        ]
        lift_stage = self._real_pickup_lift_stage_spec(tag_id, pickup_adapter)
        pickup_waypoints.append((lift_stage.position_m, lift_stage.rotation))
        self._preflight_waypoints(
            (
                *pickup_waypoints,
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
        self.allowed_collision_tag = None
        for stage in self._real_pickup_pregrasp_stage_specs(tag_id, pickup_adapter):
            self._execute_manual_cartesian_stage(
                stage.name,
                arm,
                stage.position_m,
                stage.rotation,
                speed_mm_s=stage.speed_mm_s,
                allowed_tag=stage.allowed_tag,
            )
        self.stage_trace.append("close while stationary")
        arm.set_gripper_position(GRIPPER_CLOSED, wait=True)
        self._validate_grasp_envelope(spec)
        weld_id = self.model.equality(spec.weld_name).id
        self.data.eq_active[weld_id] = 1
        self.stage_trace.append("activate assisted weld")
        self._settle(0.08)
        self._execute_manual_cartesian_stage(
            lift_stage.name,
            arm,
            lift_stage.position_m,
            lift_stage.rotation,
            speed_mm_s=135.0,
            allowed_tag=lift_stage.allowed_tag,
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

    def _physical_grasp_contact_bodies(self, spec: ComponentSpec) -> set[str]:
        mujoco.mj_forward(self.model, self.data)
        component_body_id = self._component_body_ids[spec.tag_id]
        contacts: set[str] = set()
        for contact_index in range(self.data.ncon):
            contact = self.data.contact[contact_index]
            geom1, geom2 = int(contact.geom1), int(contact.geom2)
            body1 = int(self.model.geom_bodyid[geom1])
            body2 = int(self.model.geom_bodyid[geom2])
            other_body = None
            if body1 == component_body_id and body2 != component_body_id:
                other_body = body2
            elif body2 == component_body_id and body1 != component_body_id:
                other_body = body1
            if other_body is None:
                continue
            body_name = self.model.body(other_body).name
            if body_name in GRIPPER_COMPONENT_TOUCH_BODIES:
                contacts.add(body_name)
        return contacts

    def _validate_physical_grasp(self, spec: ComponentSpec, *, stage: str) -> None:
        self._validate_grasp_envelope(spec)
        contacts = self._physical_grasp_contact_bodies(spec)
        has_left = any(name.startswith("left_") for name in contacts)
        has_right = any(name.startswith("right_") for name in contacts)
        if not (has_left and has_right):
            names = ", ".join(sorted(contacts)) or "none"
            raise SimulatorError(
                f"physical gripper did not secure {spec.tag_id} during {stage}; "
                f"finger contacts={names}"
            )
        relative_pose = self._component_relative_pose(spec.tag_id)
        self._log(
            "physical_grasp_verified",
            tag_id=spec.tag_id,
            stage=stage,
            contact_bodies=sorted(contacts),
            tcp_relative_position_m=(
                relative_pose[1] if relative_pose is not None else None
            ),
            state=self._runtime_snapshot(),
        )


def simulation_process_main(
    scene: SceneSpec,
    command_queue: Any,
    result_queue: Any,
    *,
    show_viewer: bool,
    realtime: bool,
    planner_backend: str = MUJOCO_PLANNER_RADIAL,
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
                    grasp_policy=str(command.get("grasp_policy") or "short_edges"),
                    pickup_context=str(command.get("pickup_context") or "table"),
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
