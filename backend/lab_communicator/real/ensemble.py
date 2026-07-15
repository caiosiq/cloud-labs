"""Real bench ensemble optimization — capture, actuation, loss evaluation on edge."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, TYPE_CHECKING

from lab_model import motor_rotation_store as motor_rot
from lab_model.domain.component import get_tunables
from lab_model.measurables.capture import (
    camera_image_meta_from_png,
    capture_png_for_tag,
)
from lab_model.optimization.backend import EnsembleEvaluationBackend
from lab_model.optimization.eval_sync import sync_measurables_from_objective_eval
from lab_model.optimization.kernels import apply_kernel_hooks
from lab_model.optimization.metrics import evaluate_weighted_sum
from lab_model.optimization.metrics.image_features import decode_png_bytes_to_bgr
from lab_model.optimization.objective_measurements import (
    collect_objective_measurements,
    read_laser_power_readback_mw,
    read_scalar_from_component_state,
)
from lab_model.optimization.paths import parse_variable_path
from lab_model.optimization.router import ActuatorRouter
from lab_model.optimization.session import EnsembleOptimizationResult, run_ensemble_optimization
from lab_model.optimization.spec import ObjectiveSpec, OptimizeEnsembleParameters, SolverBlockSpec, VariableRef

if TYPE_CHECKING:
    from lab_communicator.real.communicator import RealLabCommunicator


def _decode_png_bytes_to_bgr(data: bytes) -> Optional[Any]:
    return decode_png_bytes_to_bgr(data)


def _read_scalar_from_state(
    state: Mapping[str, Any],
    tag_id: str,
    path: Optional[str],
) -> Optional[float]:
    if not path or not path.startswith("measurables."):
        return None
    field_name = path.split(".", 1)[1]
    return read_scalar_from_component_state(state, tag_id, field_name)


@dataclass
class RealEnsembleHardwareBridge:
    """Apply ensemble setpoints to real motors and lab state."""

    communicator: "RealLabCommunicator"
    variables_by_id: Dict[str, VariableRef]
    settle_ms: int = 0
    _eval_counter: int = 0

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
        raise NotImplementedError(
            f"Real ensemble invasive move for {variable_id!r} is not implemented yet "
            f"(touch-and-go on {var.tag_id})."
        )

    def acquire_gripper(self, variable_id: str, var: VariableRef) -> None:
        raise NotImplementedError(
            f"Real ensemble gripper acquire for {variable_id!r} on {var.tag_id} "
            f"is not implemented yet."
        )

    def release_all_grippers(self) -> None:
        return None

    def retract_to_safe_home(self) -> None:
        return None

    def set_measurement_context(self, *, held: bool) -> None:
        return None

    def _sync_move_motor(self, tag_id: str, motor_id: int, target_angle_deg: float) -> None:
        if not self.communicator._motor_catalog_ok(tag_id, motor_id):
            raise RuntimeError(
                f"[REAL LAB] ensemble: invalid motor {motor_id} on {tag_id}"
            )
        cur = motor_rot.get_angle(tag_id, motor_id)
        delta = float(target_angle_deg) - cur
        if abs(delta) < 1e-9:
            return

        meta = self.communicator.catalog_map.get(tag_id) or {}
        controller_name = meta.get("motor_controller")
        if not controller_name:
            raise RuntimeError(
                f"[REAL LAB] ensemble: tag {tag_id} has no motor_controller in catalog"
            )
        controller = getattr(self.communicator.experiment, controller_name, None)
        if controller is None:
            raise RuntimeError(
                f"[REAL LAB] ensemble: controller {controller_name!r} missing on experiment"
            )
        controller.move_motor(int(motor_id), delta, wait_completion=True)
        motor_rot.add_delta(tag_id, int(motor_id), delta)

    def _write_variable(self, variable_id: str, value: float, var: VariableRef) -> None:
        parsed = parse_variable_path(var.path)
        if parsed.kind != "motor":
            raise NotImplementedError(
                f"Real ensemble v1 only supports motor variables; got {var.path!r}"
            )
        assert parsed.motor_id is not None
        motor_key = str(parsed.motor_id)
        target = float(value)

        with self.communicator._state_lock:
            entry = (self.communicator.current_state.get("components") or {}).get(var.tag_id)
            if not isinstance(entry, dict):
                raise RuntimeError(
                    f"[REAL LAB] ensemble: tag {var.tag_id!r} missing from state"
                )
            tun = get_tunables(entry)
            motors = tun.setdefault("nominal_motor_positions", {})
            if isinstance(motors, dict):
                motors[motor_key] = target
            self.communicator.current_state["last_updated"] = datetime.now().isoformat()

        self._sync_move_motor(var.tag_id, int(parsed.motor_id), target)

    def capture_bgr_for_tag(self, tag_id: str) -> Optional[Any]:
        from lab_model.measurables.capture import read_camera_bgr_for_tag

        comm = self.communicator
        catalog_meta = (comm.catalog_map or {}).get(tag_id) or {}
        return read_camera_bgr_for_tag(comm, tag_id, catalog_meta)

    def _capture_png_for_tag(self, tag_id: str) -> Optional[bytes]:
        comm = self.communicator
        catalog_meta = (comm.catalog_map or {}).get(tag_id) or {}
        return capture_png_for_tag(comm, tag_id, catalog_meta)

    def materialize_camera_image(self, tag_id: str, png: Optional[bytes]) -> Optional[Dict[str, Any]]:
        """Persist PNG and return camera_image measurable metadata for state sync."""
        if not png:
            return None
        comm = self.communicator
        catalog_meta = (comm.catalog_map or {}).get(tag_id) or {}
        run_dir = getattr(comm, "_active_optimization_image_dir", None)
        filename = None
        if run_dir and os.path.isdir(run_dir):
            self._eval_counter += 1
            filename = f"ensemble_eval_{self._eval_counter:04d}.png"
            # Prefer run dir for MeasurableTensor path during OPTIMIZING
            meta = camera_image_meta_from_png(
                comm,
                tag_id,
                catalog_meta,
                png,
                filename=filename,
                source="real_ensemble_eval",
            )
            # camera_image_meta writes to bridge capture dir; also archive to run_dir
            try:
                out_path = os.path.join(run_dir, filename)
                with open(out_path, "wb") as handle:
                    handle.write(png)
                meta = {**meta, "path": out_path}
            except OSError as exc:
                print(f"[REAL LAB] ensemble: could not write eval frame {run_dir}: {exc}")
            return meta
        return camera_image_meta_from_png(
            comm,
            tag_id,
            catalog_meta,
            png,
            filename=f"{tag_id}_ensemble_last.png",
            source="real_ensemble_eval",
        )

    def maybe_archive_eval_frame(self, tag_id: str, png: Optional[bytes]) -> None:
        if not png:
            return
        run_dir = getattr(self.communicator, "_active_optimization_image_dir", None)
        if not run_dir or not os.path.isdir(run_dir):
            return
        self._eval_counter += 1
        out_path = os.path.join(run_dir, f"ensemble_eval_{self._eval_counter:04d}.png")
        try:
            with open(out_path, "wb") as f:
                f.write(png)
        except OSError as exc:
            print(f"[REAL LAB] ensemble: could not write eval frame {out_path}: {exc}")


@dataclass
class RealActuatorRouter(ActuatorRouter):
    """Real router — continuous motor blocks in v1; invasive touch-and-go deferred."""

    bridge: RealEnsembleHardwareBridge
    variables_by_id: Dict[str, VariableRef]
    block_variable_ids: Sequence[str]
    events: List[str] = field(default_factory=list)
    gripper_engaged: bool = False

    @property
    def held(self) -> bool:
        return self.gripper_engaged

    def _log(self, event: str) -> None:
        self.events.append(event)

    def enter_continuous_block(self, variable_ids: Sequence[str]) -> None:
        self._log("enter_continuous_block")
        with self.bridge.communicator._state_lock:
            state = self.bridge.communicator.current_state
        from lab_model.domain.holding import is_holding

        if is_holding(state):
            print(
                "[REAL LAB] ensemble: arm is holding a component — "
                "continuous mirror block expects clear beam path."
            )

    def enter_invasive_block(self, variable_ids: Sequence[str]) -> None:
        raise NotImplementedError(
            "Real ensemble invasive_discrete blocks (touch-and-go) are not implemented yet."
        )

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

        if invasive:
            raise NotImplementedError(
                f"Real ensemble block {block_id!r} includes invasive variables "
                f"{list(invasive)} — not supported in v1."
            )
        if continuous:
            self.bridge.apply_continuous(continuous, self.variables_by_id)
        if self.bridge.settle_ms > 0:
            time.sleep(self.bridge.settle_ms / 1000.0)

    def exit_block(self, block_id: str) -> None:
        self._log(f"exit_block:{block_id}")


class RealEnsembleBackend(EnsembleEvaluationBackend):
    """Bench-side ensemble backend: motors + camera capture + shared metrics."""

    def __init__(
        self,
        bridge: RealEnsembleHardwareBridge,
        spec: OptimizeEnsembleParameters,
        *,
        kernels: Optional[Sequence[str]] = None,
    ) -> None:
        self.bridge = bridge
        self.spec = spec
        self.variables_by_id = {v.id: v for v in spec.variables}
        merged = list(kernels or [])
        for kid in getattr(spec, "kernels", None) or []:
            if kid not in merged:
                merged.append(kid)
        self.kernels = merged
        self._last_sync: Optional[Dict[str, Any]] = None
        self._last_kernel_notes: Optional[Dict[str, Any]] = None

    def router_for_block(
        self,
        block: SolverBlockSpec,
        variable_ids: Sequence[str],
    ) -> ActuatorRouter:
        return RealActuatorRouter(
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

    def _eval_flags(self) -> Dict[str, Any]:
        context: Dict[str, Any] = {"backend": "real"}
        notes = apply_kernel_hooks(self.kernels, hook="evaluate", context=context)
        self._last_kernel_notes = notes
        return context

    def _measurements_for_objective(
        self,
        objective: ObjectiveSpec,
        *,
        write_camera_image: bool = True,
    ) -> tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
        comm = self.bridge.communicator
        state = comm.current_state
        catalog_map = comm.catalog_map or {}
        camera_images: Dict[str, Dict[str, Any]] = {}

        def _capture_bgr(tag_id: str) -> Any:
            png = self.bridge._capture_png_for_tag(tag_id)
            if write_camera_image:
                meta = self.bridge.materialize_camera_image(tag_id, png)
                if meta:
                    camera_images[tag_id] = meta
            else:
                self.bridge.maybe_archive_eval_frame(tag_id, png)
            return _decode_png_bytes_to_bgr(png) if png else None

        def _read_scalar(tag_id: str, field: str) -> Optional[float]:
            if field == "output_power_readback_mw":
                return read_laser_power_readback_mw(comm, tag_id, state)
            return read_scalar_from_component_state(state, tag_id, field)

        meas = collect_objective_measurements(
            objective,
            state=state,
            catalog_map=catalog_map,
            capture_bgr_for_tag=_capture_bgr,
            read_scalar=_read_scalar,
            allow_image_scalar_fallback=True,
        )
        return meas, camera_images

    def evaluate_loss(
        self,
        physical: Mapping[str, float],
        objective: ObjectiveSpec,
        *,
        router: ActuatorRouter,
    ) -> tuple[float, Dict[str, float]]:
        del physical, router
        flags = self._eval_flags()
        write_cam = bool(flags.get("write_camera_image", True))
        sync = bool(flags.get("sync_measurables", True))

        meas, camera_images = self._measurements_for_objective(
            objective,
            write_camera_image=write_cam,
        )

        if sync:
            comm = self.bridge.communicator
            self._last_sync = sync_measurables_from_objective_eval(
                comm.current_state,
                objective,
                meas,
                catalog_map=comm.catalog_map or {},
                camera_images=camera_images if write_cam else None,
                state_lock=getattr(comm, "_state_lock", None),
            )

        return evaluate_weighted_sum(objective, meas)


def build_real_ensemble_backend(
    communicator: "RealLabCommunicator",
    spec: OptimizeEnsembleParameters,
    *,
    kernels: Optional[Sequence[str]] = None,
) -> RealEnsembleBackend:
    bridge = RealEnsembleHardwareBridge(
        communicator=communicator,
        variables_by_id={v.id: v for v in spec.variables},
        settle_ms=int(spec.solver.settle_ms),
    )
    return RealEnsembleBackend(bridge=bridge, spec=spec, kernels=kernels)


def run_real_ensemble_session(
    communicator: "RealLabCommunicator",
    spec: OptimizeEnsembleParameters,
    x0: Mapping[str, float],
    *,
    session_id: str,
    progress_callback: Optional[Callable[..., None]] = None,
    should_abort: Optional[Callable[[], bool]] = None,
    kernels: Optional[Sequence[str]] = None,
) -> EnsembleOptimizationResult:
    backend = build_real_ensemble_backend(communicator, spec, kernels=kernels)
    return run_ensemble_optimization(
        spec,
        x0,
        backend=backend,
        session_id=session_id,
        progress_callback=progress_callback,
        should_abort=should_abort,
    )


__all__ = [
    "RealActuatorRouter",
    "RealEnsembleBackend",
    "RealEnsembleHardwareBridge",
    "build_real_ensemble_backend",
    "run_real_ensemble_session",
]
