"""Damped-least-squares inverse kinematics for the MuJoCo xArm7."""

from __future__ import annotations

from dataclasses import dataclass

import mujoco
import numpy as np


ARM_DOF = 7


class IKError(RuntimeError):
    pass


def orientation_error(target: np.ndarray, current: np.ndarray) -> np.ndarray:
    return 0.5 * sum(
        np.cross(current[:, axis], target[:, axis]) for axis in range(3)
    )


@dataclass
class DampedLeastSquaresIK:
    max_iterations: int = 700
    damping: float = 2e-3
    rotation_weight: float = 0.45
    posture_weight: float = 0.003
    max_joint_step: float = 0.10
    position_tolerance_m: float = 8e-5
    rotation_tolerance_rad: float = 4e-4

    def solve(
        self,
        model: mujoco.MjModel,
        target_position: np.ndarray,
        target_rotation: np.ndarray,
        seed: np.ndarray,
        posture_reference: np.ndarray,
    ) -> np.ndarray:
        errors: list[IKError] = []
        for candidate in self._seed_candidates(
            model,
            np.asarray(target_position, dtype=float),
            np.asarray(seed, dtype=float),
        ):
            try:
                return self._solve_once(
                    model,
                    np.asarray(target_position, dtype=float),
                    np.asarray(target_rotation, dtype=float),
                    candidate,
                    np.asarray(posture_reference, dtype=float),
                )
            except IKError as exc:
                errors.append(exc)
        raise IKError(
            f"IK did not converge for TCP target "
            f"{np.round(np.asarray(target_position), 4).tolist()} "
            f"after {len(errors)} deterministic seeds"
        )

    def _solve_once(
        self,
        model: mujoco.MjModel,
        target_position: np.ndarray,
        target_rotation: np.ndarray,
        seed: np.ndarray,
        posture_reference: np.ndarray,
    ) -> np.ndarray:
        data = mujoco.MjData(model)
        data.qpos[:ARM_DOF] = np.asarray(seed, dtype=float)
        self._clip_joint_limits(model, data.qpos)
        site_id = model.site("link_tcp").id
        jac_pos = np.zeros((3, model.nv))
        jac_rot = np.zeros((3, model.nv))
        eye6 = np.eye(6)
        eye7 = np.eye(ARM_DOF)

        for _ in range(self.max_iterations):
            mujoco.mj_forward(model, data)
            current_position = data.site_xpos[site_id].copy()
            current_rotation = data.site_xmat[site_id].reshape(3, 3).copy()
            pos_delta = target_position - current_position
            rot_delta = orientation_error(target_rotation, current_rotation)
            if (
                np.linalg.norm(pos_delta) < self.position_tolerance_m
                and np.linalg.norm(rot_delta) < self.rotation_tolerance_rad
            ):
                return data.qpos[:ARM_DOF].copy()

            mujoco.mj_jacSite(model, data, jac_pos, jac_rot, site_id)
            jacobian = np.vstack(
                (
                    jac_pos[:, :ARM_DOF],
                    self.rotation_weight * jac_rot[:, :ARM_DOF],
                )
            )
            error = np.concatenate((pos_delta, self.rotation_weight * rot_delta))
            inverse_term = np.linalg.solve(
                jacobian @ jacobian.T + self.damping * eye6,
                eye6,
            )
            pseudo_inverse = jacobian.T @ inverse_term
            task_step = pseudo_inverse @ error
            nullspace = eye7 - pseudo_inverse @ jacobian
            posture_step = self.posture_weight * (
                posture_reference - data.qpos[:ARM_DOF]
            )
            velocity = task_step + nullspace @ posture_step
            largest = float(np.max(np.abs(velocity)))
            if largest > self.max_joint_step:
                velocity *= self.max_joint_step / largest
            data.qpos[:ARM_DOF] += velocity
            self._clip_joint_limits(model, data.qpos)

        raise IKError(
            f"IK did not converge for TCP target "
            f"{np.round(np.asarray(target_position), 4).tolist()}"
        )

    @staticmethod
    def _seed_candidates(
        model: mujoco.MjModel,
        target_position: np.ndarray,
        seed: np.ndarray,
    ) -> list[np.ndarray]:
        """Try the previous posture first, then base-aligned elbow postures.

        A single home seed traps DLS in local minima for valid poses in other
        table quadrants. The target bearing gives joint 1 a deterministic,
        physically meaningful starting point while retaining the previous
        solution as the preferred continuous branch.
        """
        candidates = [np.asarray(seed, dtype=float).copy()]
        bearing = float(np.arctan2(target_position[1], target_position[0]))
        shoulder_values = (
            float(seed[1]),
            -0.8,
            0.3,
            0.8,
        )
        for shoulder in shoulder_values:
            candidate = np.asarray(seed, dtype=float).copy()
            candidate[0] = bearing
            candidate[1] = shoulder
            DampedLeastSquaresIK._clip_joint_limits(model, candidate)
            if not any(np.allclose(candidate, existing, atol=1e-9) for existing in candidates):
                candidates.append(candidate)
        return candidates

    @staticmethod
    def _clip_joint_limits(model: mujoco.MjModel, qpos: np.ndarray) -> None:
        for index in range(ARM_DOF):
            joint_id = model.joint(f"joint{index + 1}").id
            if model.jnt_limited[joint_id]:
                low, high = model.jnt_range[joint_id]
                qpos[index] = np.clip(qpos[index], low + 1e-4, high - 1e-4)
