"""Mock ensemble optimization: synthetic landscape, router, hardware bridge."""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from lab_model.execution.optimization.backend import EnsembleEvaluationBackend
from lab_model.execution.optimization.metrics import evaluate_weighted_sum
from lab_model.execution.optimization.router import ActuatorRouter
from lab_model.execution.optimization.session import EnsembleOptimizationResult, run_ensemble_optimization
from lab_model.language.domain.component import get_tunables
from lab_model.execution.optimization.paths import parse_variable_path
from lab_model.execution.optimization.spec import (
    ObjectiveSpec,
    OptimizeEnsembleParameters,
    SolverBlockSpec,
    VariableRef,
)

# Hidden sensitivity: each motor steers centroid X and Y (cross-coupled).
_DEFAULT_JX = (18.0, 12.0, -10.0, 14.0)
_DEFAULT_JY = (-8.0, 16.0, 11.0, -9.0)


def _deterministic_truth_offset(variable_id: str) -> float:
    digest = hashlib.sha256(variable_id.encode("utf-8")).hexdigest()
    bucket = int(digest[:8], 16) / 0xFFFFFFFF
    return (bucket - 0.5) * 1.6


@dataclass
class MockEnsembleLandscape:
    """
    Coupled synthetic loss hidden from the solver.

    Truth optima are offset from session x0; COBYLA must recover them inside bounds.
    """

    variables: Sequence[VariableRef]
    x0: Mapping[str, float]
    target_px: tuple[float, float] = (512.0, 384.0)
    clamping_bias_px: float = 28.0
    truth: Dict[str, float] = field(default_factory=dict)
    jx: Sequence[float] = _DEFAULT_JX
    jy: Sequence[float] = _DEFAULT_JY
    intensity_sigma: float = 1.2

    def __post_init__(self) -> None:
        if not self.truth:
            self.truth = {
                vid: float(self.x0[vid]) + _deterministic_truth_offset(vid)
                for vid in self.x0
            }

    def _theta_delta_vector(self, physical: Mapping[str, float]) -> List[float]:
        ordered = [float(physical[v.id]) - float(self.truth[v.id]) for v in self.variables]
        return ordered

    def centroid_px(
        self,
        physical: Mapping[str, float],
        *,
        held: bool,
    ) -> tuple[float, float]:
        deltas = self._theta_delta_vector(physical)
        n = len(deltas)
        jx = list(self.jx[:n]) + [0.0] * max(0, n - len(self.jx))
        jy = list(self.jy[:n]) + [0.0] * max(0, n - len(self.jy))
        dx = sum(jx[i] * deltas[i] for i in range(n))
        dy = sum(jy[i] * deltas[i] for i in range(n))
        if held:
            dx += self.clamping_bias_px
            dy += self.clamping_bias_px * 0.35
        tx, ty = self.target_px
        return tx + dx, ty + dy

    def power_scalar(self, physical: Mapping[str, float]) -> float:
        deltas = self._theta_delta_vector(physical)
        sigma = max(self.intensity_sigma, 1e-6)
        chi2 = sum((d / sigma) ** 2 for d in deltas)
        return math.exp(-0.5 * chi2)

    def measurements_for_objective(
        self,
        physical: Mapping[str, float],
        objective: ObjectiveSpec,
        *,
        held: bool,
    ) -> Dict[str, Dict[str, Any]]:
        cx, cy = self.centroid_px(physical, held=held)
        power = self.power_scalar(physical)
        out: Dict[str, Dict[str, Any]] = {}
        for term in objective.terms:
            if term.source.kind == "derived_centroid":
                out[term.id] = {"centroid_x": cx, "centroid_y": cy}
            elif term.source.kind == "measurable_scalar":
                out[term.id] = {"scalar": power, "power": power}
            elif term.source.kind in ("torchscript_scalar", "torchscript_features"):
                kid = str(getattr(term.source, "kernel_id", None) or "").strip()
                if not kid:
                    raise RuntimeError(
                        f"term {term.id!r}: torchscript kind requires kernel_id"
                    )
                import numpy as np
                from lab_model.execution.optimization.kernels.torchscript_runtime import (
                    run_torchscript_output,
                )

                level = int(max(0, min(255, round(float(power) * 255.0))))
                bgr = np.full((64, 64, 3), level, dtype=np.uint8)
                kind, value = run_torchscript_output(kid, bgr)
                if kind == "features":
                    names = list(getattr(term.source, "feature_names", None) or [])
                    extra = getattr(term.source, "__pydantic_extra__", None) or {}
                    if not names and isinstance(extra, dict):
                        names = list(extra.get("feature_names") or [])
                    out[term.id] = {
                        "features": list(value),
                        "feature_names": names,
                        "scalar": float(value[0]) if value else float("nan"),
                    }
                else:
                    out[term.id] = {"scalar": float(value)}
            else:
                out[term.id] = {"scalar": power}
        return out

    def loss_at_truth(self, objective: ObjectiveSpec, *, held: bool = False) -> float:
        physical = dict(self.truth)
        meas = self.measurements_for_objective(physical, objective, held=held)
        loss, _ = evaluate_weighted_sum(objective, meas)
        return loss


@dataclass
class MockActuatorRouter(ActuatorRouter):
    """Mock router with clearance + lazy re-engage; records events for tests."""

    bridge: "MockEnsembleHardwareBridge"
    variables_by_id: Dict[str, VariableRef]
    block_variable_ids: Sequence[str]
    events: List[str] = field(default_factory=list)
    gripper_engaged: bool = False
    arm_at_home: bool = True
    last_invasive_physical: Dict[str, float] = field(default_factory=dict)
    _in_block: bool = False

    @property
    def held(self) -> bool:
        return self.gripper_engaged

    def _log(self, event: str) -> None:
        self.events.append(event)

    def enter_continuous_block(self, variable_ids: Sequence[str]) -> None:
        self._in_block = True
        self._log("enter_continuous_block")
        self.bridge.release_all_grippers()
        self.gripper_engaged = False
        self.bridge.retract_to_safe_home()
        self.arm_at_home = True
        self._log("arm_at_home")

    def enter_invasive_block(self, variable_ids: Sequence[str]) -> None:
        self._in_block = True
        self._log("enter_invasive_block")
        self.arm_at_home = False

    def apply_eval(
        self,
        physical_values: Mapping[str, float],
        *,
        block_id: str,
    ) -> None:
        continuous: Dict[str, float] = {}
        invasive: Dict[str, float] = {}
        for vid in self.block_variable_ids:
            var = self.variables_by_id[vid]
            val = float(physical_values[vid])
            if var.physical_type == "invasive_discrete":
                invasive[vid] = val
            else:
                continuous[vid] = val

        if continuous and invasive and self.gripper_engaged:
            self.bridge.release_all_grippers()
            self.gripper_engaged = False
            self.bridge.retract_to_safe_home()
            self.arm_at_home = True

        if continuous:
            self.bridge.apply_continuous(continuous, self.variables_by_id)

        if invasive:
            for vid, val in invasive.items():
                prev = self.last_invasive_physical.get(vid)
                needs_move = prev is None or abs(prev - val) > 1e-9
                if needs_move:
                    if not self.gripper_engaged:
                        self._log("re_engage_on_demand")
                        self.bridge.acquire_gripper(vid, self.variables_by_id[vid])
                        self.gripper_engaged = True
                        self.arm_at_home = False
                    self.bridge.apply_invasive_move(vid, val, self.variables_by_id[vid])
                    self.last_invasive_physical[vid] = val
                self._log("release_for_measure")
                self.bridge.release_all_grippers()
                self.gripper_engaged = False
                self.bridge.retract_to_safe_home()
                self.arm_at_home = True

        self.bridge.set_measurement_context(held=self.gripper_engaged)

    def exit_block(self, block_id: str) -> None:
        self._log(f"exit_block:{block_id}")
        if self.gripper_engaged:
            self.bridge.release_all_grippers()
            self.gripper_engaged = False
        self.bridge.retract_to_safe_home()
        self.arm_at_home = True
        self._in_block = False


@dataclass
class MockEnsembleHardwareBridge:
    """Apply ensemble setpoints to mock lab state (under caller's lock)."""

    state: Dict[str, Any]
    variables_by_id: Dict[str, VariableRef]
    landscape: MockEnsembleLandscape
    _held_for_measure: bool = False
    state_lock: Any = None

    def apply_continuous(
        self,
        values: Mapping[str, float],
        variables_by_id: Mapping[str, VariableRef],
    ) -> None:
        for vid, val in values.items():
            self._write_variable(vid, val, variables_by_id[vid])

    def apply_invasive_move(
        self,
        variable_id: str,
        value: float,
        var: VariableRef,
    ) -> None:
        self._write_variable(variable_id, value, var)

    def acquire_gripper(self, variable_id: str, var: VariableRef) -> None:
        tg = var.touch_and_go
        if tg is not None:
            holding = self.state.setdefault("holding", {})
            holding["tag_id"] = var.tag_id
            holding["requires_operator_confirm"] = False

    def release_all_grippers(self) -> None:
        holding = self.state.get("holding")
        if isinstance(holding, dict):
            holding["tag_id"] = None
            holding["nominal_pose"] = None
        self._held_for_measure = False

    def retract_to_safe_home(self) -> None:
        self._held_for_measure = False

    def set_measurement_context(self, *, held: bool) -> None:
        self._held_for_measure = held

    def sync_measurables_from_eval(
        self,
        physical: Mapping[str, float],
        objective: ObjectiveSpec,
        *,
        held: bool,
    ) -> None:
        """Write synthetic centroid/power into runtime measurables (mock bridge)."""
        cx, cy = self.landscape.centroid_px(physical, held=held)
        power = self.landscape.power_scalar(physical)
        primary_tag = "tag_20"
        for term in objective.terms:
            if term.source.kind == "derived_centroid":
                primary_tag = term.source.tag_id
                break
            if term.source.kind == "measurable_scalar":
                primary_tag = term.source.tag_id

        def _apply() -> None:
            entry = (self.state.get("components") or {}).get(primary_tag)
            if not isinstance(entry, dict):
                return
            sc = entry.setdefault("statecontrol", {})
            meas = sc.setdefault("measurables", {})
            if isinstance(meas, dict):
                meas["centroid_x_px"] = float(cx)
                meas["centroid_y_px"] = float(cy)
                meas["last_optimization_score"] = float(power)
                meas["mock_ensemble_power"] = float(power)

        if self.state_lock is not None:
            with self.state_lock:
                _apply()
        else:
            _apply()

    def _write_variable(self, variable_id: str, value: float, var: VariableRef) -> None:
        def _apply() -> None:
            parsed = parse_variable_path(var.path)
            entry = (self.state.get("components") or {}).get(var.tag_id)
            if not isinstance(entry, dict):
                return
            tun = get_tunables(entry)
            if parsed.kind == "motor":
                motors = tun.setdefault("nominal_motor_positions", {})
                if isinstance(motors, dict) and parsed.motor_id is not None:
                    motors[str(parsed.motor_id)] = float(value)
            elif parsed.axis is not None:
                pose = tun.setdefault("nominal_pose", {})
                if isinstance(pose, dict):
                    pose[parsed.axis] = float(value)

        if self.state_lock is not None:
            with self.state_lock:
                _apply()
        else:
            _apply()


class MockEnsembleBackend(EnsembleEvaluationBackend):
    """Mock implementation wired into :func:`run_ensemble_optimization`."""

    def __init__(
        self,
        bridge: MockEnsembleHardwareBridge,
        landscape: MockEnsembleLandscape,
        spec: OptimizeEnsembleParameters,
    ) -> None:
        self.bridge = bridge
        self.landscape = landscape
        self.spec = spec
        self.variables_by_id = {v.id: v for v in spec.variables}

    def router_for_block(
        self,
        block: SolverBlockSpec,
        variable_ids: Sequence[str],
    ) -> ActuatorRouter:
        return MockActuatorRouter(
            bridge=self.bridge,
            variables_by_id=self.variables_by_id,
            block_variable_ids=list(variable_ids),
        )

    def apply_through_router(
        self,
        router: ActuatorRouter,
        physical: Mapping[str, float],
        *,
        block_id: str,
    ) -> None:
        router.apply_eval(physical, block_id=block_id)

    def evaluate_loss(
        self,
        physical: Mapping[str, float],
        objective: ObjectiveSpec,
        *,
        router: ActuatorRouter,
    ) -> tuple[float, Dict[str, float]]:
        held = bool(getattr(router, "gripper_engaged", False))
        meas = self.landscape.measurements_for_objective(
            physical,
            objective,
            held=held,
        )
        self.bridge.sync_measurables_from_eval(
            physical,
            objective,
            held=held,
        )
        return evaluate_weighted_sum(objective, meas)


def build_mock_ensemble_backend(
    state: Dict[str, Any],
    spec: OptimizeEnsembleParameters,
    x0: Mapping[str, float],
    *,
    target_px: Optional[tuple[float, float]] = None,
    state_lock: Any = None,
) -> MockEnsembleBackend:
    target = target_px or (512.0, 384.0)
    for term in spec.objective.terms:
        if term.source.target_px:
            target = (
                float(term.source.target_px.get("x", target[0])),
                float(term.source.target_px.get("y", target[1])),
            )
            break
    landscape = MockEnsembleLandscape(
        variables=spec.variables,
        x0=x0,
        target_px=target,
    )
    bridge = MockEnsembleHardwareBridge(
        state=state,
        variables_by_id={v.id: v for v in spec.variables},
        landscape=landscape,
        state_lock=state_lock,
    )
    return MockEnsembleBackend(bridge=bridge, landscape=landscape, spec=spec)


def run_mock_ensemble_session(
    state: Dict[str, Any],
    spec: OptimizeEnsembleParameters,
    x0: Mapping[str, float],
    *,
    session_id: str,
    progress_callback: Optional[Callable[..., None]] = None,
    state_lock: Any = None,
    should_abort: Optional[Callable[[], bool]] = None,
) -> EnsembleOptimizationResult:
    backend = build_mock_ensemble_backend(
        state, spec, x0, state_lock=state_lock,
    )
    return run_ensemble_optimization(
        spec,
        x0,
        backend=backend,
        session_id=session_id,
        progress_callback=progress_callback,
        should_abort=should_abort,
    )


__all__ = [
    "MockActuatorRouter",
    "MockEnsembleBackend",
    "MockEnsembleHardwareBridge",
    "MockEnsembleLandscape",
    "build_mock_ensemble_backend",
    "run_mock_ensemble_session",
]
