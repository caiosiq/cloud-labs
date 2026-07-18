"""Long-running MuJoCo runtime and process entrypoint."""

from __future__ import annotations

import math
import queue
import time
import traceback
from dataclasses import dataclass
from typing import Any, Dict, Optional, Sequence

import mujoco
import mujoco.viewer
import numpy as np

from simulation_edge.host.ik import ARM_DOF, DampedLeastSquaresIK, IKError
from simulation_edge.host.scene import ComponentSpec, SceneSpec
from simulation_edge.sim_xarm.wrapper import XArmAPI


GRIPPER_OPEN = 0.0
GRIPPER_CLOSED = 255.0
CLEARANCE_Z_M = 0.48
APPROACH_CLEARANCE_M = 0.11
RELEASE_CLEARANCE_M = 0.006
ARM_SERVO_STIFFNESS_SCALE = 2.0


class SimulatorError(RuntimeError):
    pass


class ViewerClosedError(SimulatorError):
    pass


class CollisionPlanError(SimulatorError):
    pass


def _smoothstep(value: float) -> float:
    value = float(np.clip(value, 0.0, 1.0))
    return value * value * (3.0 - 2.0 * value)


def _rotation_z(yaw_rad: float) -> np.ndarray:
    c, s = math.cos(yaw_rad), math.sin(yaw_rad)
    return np.array(((c, -s, 0.0), (s, c, 0.0), (0.0, 0.0, 1.0)))


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


class MuJoCoRobotRuntime:
    """Owns one compiled model, physics state, viewer, and IK solver."""

    def __init__(
        self,
        scene: SceneSpec,
        *,
        show_viewer: bool,
        realtime: bool,
    ) -> None:
        self.scene = scene
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
        self.current_joint_target = self.home.copy()
        self.home_tcp_rotation = self.data.site("link_tcp").xmat.reshape(3, 3).copy()
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
        self.data.ctrl[:ARM_DOF] = self.home
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
        self._settle(0.25)

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
        self._viewer_entered.sync()
        if self.realtime:
            remaining = self.model.opt.timestep - (time.perf_counter() - started)
            if remaining > 0:
                time.sleep(remaining)

    def _settle(self, duration_s: float) -> None:
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
            correction_joints = self.ik.solve(
                self.model,
                compensated_position,
                target_rotation,
                actual_joints,
                self.home,
            )
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

    def _execute_joint_target(self, target: np.ndarray, duration_s: float) -> None:
        previous = self.current_joint_target.copy()
        started = float(self.data.time)
        while self.data.time - started < duration_s:
            phase = _smoothstep((self.data.time - started) / duration_s)
            self.data.ctrl[:ARM_DOF] = previous + phase * (target - previous)
            self.data.ctrl[ARM_DOF] = self._gripper_position
            self._step()
        self.data.ctrl[:ARM_DOF] = target
        self.current_joint_target = target.copy()
        self._settle(0.20)

    def _preflight_joint_path(
        self,
        start: np.ndarray,
        end: np.ndarray,
        *,
        allowed_tag: Optional[str],
    ) -> None:
        probe = mujoco.MjData(self.model)
        probe.qpos[:] = self.data.qpos
        allowed_body = self._component_body_ids.get(allowed_tag or "")
        component_bodies = set(self._component_body_ids.values())
        for fraction in np.linspace(0.0, 1.0, 18):
            probe.qpos[:ARM_DOF] = start + float(fraction) * (end - start)
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
                del arm_body

    def _target_rotation(self, yaw_deg: float) -> np.ndarray:
        return _rotation_z(math.radians(float(yaw_deg))) @ self.home_tcp_rotation

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
        arm.set_position_aa(
            self._pose_for(position_m, rotation),
            speed=speed_mm_s,
            mvacc=100.0,
            wait=True,
            is_radian=False,
        )

    def _object_pose(self, tag_id: str) -> tuple[np.ndarray, float]:
        spec = self.scene.components[tag_id]
        body = self.data.body(spec.body_name)
        position = body.xpos.copy()
        rotation = body.xmat.reshape(3, 3).copy()
        yaw = math.degrees(math.atan2(rotation[1, 0], rotation[0, 0]))
        return position, yaw

    def pick_and_place(
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
        self._validate_destination(spec, target_xy)
        self._preflight_waypoints(
            (
                (np.array((*source_xy, CLEARANCE_Z_M)), source_rotation),
                (np.array((*source_xy, approach_z)), source_rotation),
                (np.array((*source_xy, grasp_z)), source_rotation),
                (np.array((*source_xy, CLEARANCE_Z_M)), source_rotation),
                (np.array((*target_xy, CLEARANCE_Z_M)), target_rotation),
                (np.array((*target_xy, release_z)), target_rotation),
                (np.array((*target_xy, CLEARANCE_Z_M)), target_rotation),
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
            np.array((*source_xy, CLEARANCE_Z_M)),
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
            np.array((*source_xy, CLEARANCE_Z_M)),
            source_rotation,
            speed_mm_s=135.0,
        )
        self._move_stage(
            "translate",
            arm,
            np.array((*target_xy, CLEARANCE_Z_M)),
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
        self.allowed_collision_tag = None
        self._move_stage(
            "retreat",
            arm,
            np.array((*target_xy, CLEARANCE_Z_M)),
            target_rotation,
            speed_mm_s=130.0,
        )
        self._settle(0.45)
        final_position, final_yaw = self._object_pose(tag_id)
        position_error_mm = float(
            np.linalg.norm(final_position[:2] - target_xy) * 1000.0
        )
        yaw_error_deg = abs(
            (final_yaw - float(target_rotation_deg) + 180.0) % 360.0 - 180.0
        )
        if position_error_mm > 5.0 or yaw_error_deg > 2.0:
            raise SimulatorError(
                f"placement tolerance failed for {tag_id}: "
                f"position error={position_error_mm:.2f} mm, "
                f"yaw error={yaw_error_deg:.2f} deg"
            )
        return MoveResult(
            tag_id=tag_id,
            x_mm=float(final_position[0] * 1000.0),
            y_mm=float(final_position[1] * 1000.0),
            rotation_deg=float(final_yaw),
            stage_trace=tuple(self.stage_trace),
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
) -> None:
    runtime: Optional[MuJoCoRobotRuntime] = None
    try:
        runtime = MuJoCoRobotRuntime(
            scene,
            show_viewer=show_viewer,
            realtime=realtime,
        )
        result_queue.put({"type": "ready"})
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
                    }
                )
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
