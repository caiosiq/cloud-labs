"""Real bench ensemble optimization — capture, actuation, loss evaluation on edge."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, TYPE_CHECKING

from lab_model import motor_rotation_store as motor_rot
from lab_model.catalog.schema import resolve_cam_id_for_tag, resolve_hardware_binding, resolve_telemetry_stream_backend
from lab_model.domain.component import get_measurables, get_tunables
from lab_model.optimization.backend import EnsembleEvaluationBackend
from lab_model.optimization.metrics import evaluate_weighted_sum
from lab_model.optimization.paths import parse_variable_path
from lab_model.optimization.router import ActuatorRouter
from lab_model.optimization.session import EnsembleOptimizationResult, run_ensemble_optimization
from lab_model.optimization.spec import ObjectiveSpec, OptimizeEnsembleParameters, SolverBlockSpec, VariableRef

if TYPE_CHECKING:
    from lab_communicator.real.communicator import RealLabCommunicator


def compute_beam_centroid_px(bgr: Any) -> Optional[tuple[float, float]]:
    """Intensity-weighted centroid of the brightest region in a BGR frame."""
    if bgr is None:
        return None
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None

    if getattr(bgr, "ndim", 0) != 3 or bgr.shape[2] < 3:
        return None

    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    flat = gray.astype(np.float64).ravel()
    if flat.size == 0:
        return None

    threshold = float(np.percentile(flat, 92.0))
    mask = gray.astype(np.float64) >= threshold
    weights = gray.astype(np.float64) * mask
    total = float(weights.sum())
    if total <= 1e-6:
        return None

    ys, xs = np.indices(gray.shape)
    cx = float((xs * weights).sum() / total)
    cy = float((ys * weights).sum() / total)
    return cx, cy


def compute_beam_power_scalar(bgr: Any) -> Optional[float]:
    """Normalized bright-pixel energy in [0, 1] for scalar objective terms."""
    if bgr is None:
        return None
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None

    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    flat = gray.astype(np.float64).ravel()
    if flat.size == 0:
        return None
    threshold = float(np.percentile(flat, 90.0))
    bright = flat[flat >= threshold]
    if bright.size == 0:
        return 0.0
    return float(np.clip(bright.mean() / 255.0, 0.0, 1.0))


def _decode_png_bytes_to_bgr(data: bytes) -> Optional[Any]:
    if not data or len(data) < 8:
        return None
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None

    arr = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_UNCHANGED)
    if img is None:
        return None
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    elif img.ndim == 3 and img.shape[2] == 4:
        img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
    if img.ndim != 3 or img.shape[2] != 3:
        return None
    return img


def _read_scalar_from_state(
    state: Mapping[str, Any],
    tag_id: str,
    path: Optional[str],
) -> Optional[float]:
    if not path or not path.startswith("measurables."):
        return None
    field_name = path.split(".", 1)[1]
    entry = (state.get("components") or {}).get(tag_id)
    if not isinstance(entry, dict):
        return None
    meas = get_measurables(entry).get(field_name)
    if isinstance(meas, dict):
        for key in ("scalar", "value", "power", "score"):
            if key in meas:
                try:
                    return float(meas[key])
                except (TypeError, ValueError):
                    continue
    try:
        return float(meas)
    except (TypeError, ValueError):
        return None


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
        png = self._capture_png_for_tag(tag_id)
        return _decode_png_bytes_to_bgr(png) if png else None

    def _capture_png_for_tag(self, tag_id: str) -> Optional[bytes]:
        comm = self.communicator
        catalog_meta = (comm.catalog_map or {}).get(tag_id) or {}
        exp_ms = 200.0
        tun = comm.return_tunables_for_tag(tag_id) or {}
        if isinstance(tun.get("exposure_time_ms"), (int, float)):
            exp_ms = float(tun["exposure_time_ms"])
        exposure_s = exp_ms / 1000.0

        binding = resolve_hardware_binding(catalog_meta)
        stream_backend = resolve_telemetry_stream_backend(catalog_meta)
        if stream_backend == "overhead" or (
            binding is not None and binding.backend in ("opencv_usb", "overhead")
        ):
            return comm.capture_overhead_cam(exposure=exposure_s)

        cam_id = resolve_cam_id_for_tag(catalog_meta) or 1
        if hasattr(comm, "table_cam_connect"):
            connected = getattr(comm, "_table_cam_connected", {}).get(int(cam_id))
            if not connected:
                comm.table_cam_connect(int(cam_id))
        return comm.capture_table_cam(int(cam_id), exposure=exposure_s)

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
    ) -> None:
        self.bridge = bridge
        self.spec = spec
        self.variables_by_id = {v.id: v for v in spec.variables}

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

    def _measurements_for_objective(
        self,
        objective: ObjectiveSpec,
    ) -> Dict[str, Dict[str, Any]]:
        state = self.bridge.communicator.current_state
        out: Dict[str, Dict[str, Any]] = {}
        capture_cache: Dict[str, Any] = {}

        for term in objective.terms:
            kind = term.source.kind
            tag_id = term.source.tag_id

            if kind == "derived_centroid":
                if tag_id not in capture_cache:
                    png = self.bridge._capture_png_for_tag(tag_id)
                    self.bridge.maybe_archive_eval_frame(tag_id, png)
                    capture_cache[tag_id] = _decode_png_bytes_to_bgr(png) if png else None
                bgr = capture_cache[tag_id]
                centroid = compute_beam_centroid_px(bgr)
                if centroid is None:
                    out[term.id] = {"centroid_x": float("nan"), "centroid_y": float("nan")}
                else:
                    out[term.id] = {"centroid_x": centroid[0], "centroid_y": centroid[1]}

            elif kind == "measurable_scalar":
                scalar = _read_scalar_from_state(state, tag_id, term.source.path)
                if scalar is None and tag_id not in capture_cache:
                    png = self.bridge._capture_png_for_tag(tag_id)
                    self.bridge.maybe_archive_eval_frame(tag_id, png)
                    capture_cache[tag_id] = _decode_png_bytes_to_bgr(png) if png else None
                if scalar is None:
                    scalar = compute_beam_power_scalar(capture_cache.get(tag_id))
                out[term.id] = {"scalar": float(scalar if scalar is not None else 0.0)}

            else:
                scalar = _read_scalar_from_state(state, tag_id, term.source.path)
                out[term.id] = {"scalar": float(scalar if scalar is not None else 0.0)}

        return out

    def evaluate_loss(
        self,
        physical: Mapping[str, float],
        objective: ObjectiveSpec,
        *,
        router: ActuatorRouter,
    ) -> tuple[float, Dict[str, float]]:
        del physical, router
        meas = self._measurements_for_objective(objective)
        return evaluate_weighted_sum(objective, meas)


def build_real_ensemble_backend(
    communicator: "RealLabCommunicator",
    spec: OptimizeEnsembleParameters,
) -> RealEnsembleBackend:
    bridge = RealEnsembleHardwareBridge(
        communicator=communicator,
        variables_by_id={v.id: v for v in spec.variables},
        settle_ms=int(spec.solver.settle_ms),
    )
    return RealEnsembleBackend(bridge=bridge, spec=spec)


def run_real_ensemble_session(
    communicator: "RealLabCommunicator",
    spec: OptimizeEnsembleParameters,
    x0: Mapping[str, float],
    *,
    session_id: str,
    progress_callback: Optional[Callable[..., None]] = None,
) -> EnsembleOptimizationResult:
    backend = build_real_ensemble_backend(communicator, spec)
    return run_ensemble_optimization(
        spec,
        x0,
        backend=backend,
        session_id=session_id,
        progress_callback=progress_callback,
    )


__all__ = [
    "RealActuatorRouter",
    "RealEnsembleBackend",
    "RealEnsembleHardwareBridge",
    "build_real_ensemble_backend",
    "compute_beam_centroid_px",
    "compute_beam_power_scalar",
    "run_real_ensemble_session",
]
