"""Edge optimization session loop — capture → kernel → loss → stepper → actuate."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Sequence
import math

from .capture import CaptureSource
from .guards import (
    ClearanceError,
    MaxDeltaError,
    check_max_delta,
    max_delta_limits,
    settle_final_values,
)
from .kernels import KernelError, default_kernels_dir, eval_kernel
from .metrics import MeasurementInvalid, evaluate_weighted_sum
from .normalize import NormalizedSearchSpace
from .presence import apply_peak_presence_gate
from .value_ref import apply_value_ref_latch
from .router import ActuatorRouter
from .spec import OptimizationPipeline, ObjectiveTerm, parse_pipeline
from .stepper import run_block_cobyla
from .tensors import EdgeTensor


@dataclass
class OptimizationResult:
    session_id: str
    best_loss: float
    final_values: Dict[str, float]
    evals: int = 0
    aborted: bool = False
    early_stopped: bool = False
    refusal: Optional[str] = None
    trace: list[Dict[str, Any]] = field(default_factory=list)
    early_stop_reason: Optional[str] = None


def _tag_for_measurable(pipeline: OptimizationPipeline, term: ObjectiveTerm) -> str:
    if pipeline.variables:
        return pipeline.variables[0].tag_id
    return "unknown"


def _collect_measurements(
    pipeline: OptimizationPipeline,
    capture: CaptureSource,
    *,
    kernels_dir: Path,
    physical: Mapping[str, float],
) -> tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    """Capture once per capture_id, eval kernels; return (measurements, debug)."""
    frames: Dict[str, EdgeTensor] = {}
    capture_dbg: Dict[str, Any] = {"ok": True, "frames": [], "error": None}
    kernel_dbg: Dict[str, Any] = {"ok": True, "terms": {}, "error": None}
    needed = {t.capture_id for t in pipeline.objective.terms if t.capture_id}
    for cid in needed:
        try:
            tensor = capture.capture(cid)
            frames[cid] = tensor
            data = getattr(tensor, "data", None)
            shape = None
            try:
                import numpy as np

                shape = list(np.asarray(data).shape) if data is not None else None
            except Exception:  # noqa: BLE001
                shape = None
            capture_dbg["frames"].append(
                {
                    "capture_id": cid,
                    "tag_id": getattr(tensor, "tag_id", None),
                    "shape": shape,
                    "layout": getattr(tensor, "layout", None),
                }
            )
        except Exception as exc:  # noqa: BLE001
            capture_dbg["ok"] = False
            capture_dbg["error"] = f"{cid}: {exc}"
            raise

    out: Dict[str, Dict[str, Any]] = {}
    for term in pipeline.objective.terms:
        if term.measurable_path:
            tag_id = _tag_for_measurable(pipeline, term)
            tag_id = str(term.params.get("tag_id") or tag_id)
            try:
                value = capture.read_measurable(term.measurable_path, tag_id=tag_id)
                out[term.id] = {"scalar": float(value)}
                kernel_dbg["terms"][term.id] = {
                    "kind": "measurable",
                    "path": term.measurable_path,
                    "scalar": float(value),
                }
            except Exception as exc:  # noqa: BLE001
                kernel_dbg["ok"] = False
                kernel_dbg["error"] = f"{term.id}: {exc}"
                kernel_dbg["terms"][term.id] = {"kind": "measurable", "error": str(exc)}
                raise
            continue

        assert term.capture_id and term.kernel_id
        tensor = frames[term.capture_id]
        try:
            kind, value = eval_kernel(term.kernel_id, tensor.data, kernels_dir=kernels_dir)
            if kind == "features":
                feats = list(value)
                row: Dict[str, Any] = {"features": feats}
                if feats:
                    row["scalar"] = float(feats[0])
                out[term.id] = row
                term_dbg: Dict[str, Any] = {
                    "kind": "features",
                    "kernel_id": term.kernel_id,
                    "n": len(feats),
                    "preview": [round(float(x), 4) for x in feats[:5]],
                    "metric": term.metric,
                }
                _attach_viz_params(term_dbg, term)
                if len(feats) >= 2 and term.metric in (
                    "rms_distance",
                    "rms_distance_px",
                ):
                    term_dbg["detected_xy"] = [
                        round(float(feats[0]), 3),
                        round(float(feats[1]), 3),
                    ]
                kernel_dbg["terms"][term.id] = term_dbg
            else:
                out[term.id] = {"scalar": float(value)}
                term_dbg = {
                    "kind": "scalar",
                    "kernel_id": term.kernel_id,
                    "scalar": float(value),
                    "metric": term.metric,
                }
                _attach_viz_params(term_dbg, term)
                kernel_dbg["terms"][term.id] = term_dbg
        except Exception as exc:  # noqa: BLE001
            kernel_dbg["ok"] = False
            kernel_dbg["error"] = f"{term.id}/{term.kernel_id}: {exc}"
            kernel_dbg["terms"][term.id] = {
                "kind": "error",
                "kernel_id": term.kernel_id,
                "error": str(exc),
            }
            raise
    _ = physical
    return out, {"capture": capture_dbg, "kernel": kernel_dbg}


def _attach_viz_params(entry: Dict[str, Any], term: ObjectiveTerm) -> None:
    """Copy UI-overlay fields (target / frame size) onto a debug term row."""
    tp = term.params.get("target_px")
    if isinstance(tp, Mapping):
        try:
            entry["target_px"] = {
                "x": float(tp.get("x", 0.0)),
                "y": float(tp.get("y", 0.0)),
            }
        except (TypeError, ValueError):
            pass
    target = term.params.get("target")
    if isinstance(target, (list, tuple)) and len(target) >= 2:
        try:
            entry["target"] = [float(target[0]), float(target[1])]
        except (TypeError, ValueError):
            pass
    hw = term.params.get("frame_hw")
    if isinstance(hw, (list, tuple)) and len(hw) >= 2:
        try:
            entry["frame_hw"] = [int(hw[0]), int(hw[1])]
        except (TypeError, ValueError):
            pass


def _first_frame_hw(capture_dbg: Mapping[str, Any]) -> Optional[tuple[int, int]]:
    frames = capture_dbg.get("frames") if isinstance(capture_dbg, Mapping) else None
    if not isinstance(frames, list):
        return None
    for fr in frames:
        if not isinstance(fr, Mapping):
            continue
        shape = fr.get("shape")
        if isinstance(shape, (list, tuple)) and len(shape) >= 2:
            try:
                h, w = int(shape[0]), int(shape[1])
            except (TypeError, ValueError):
                continue
            if h > 0 and w > 0:
                return h, w
    return None


def inject_frame_scales(
    measurements: Dict[str, Dict[str, Any]],
    terms: Sequence[ObjectiveTerm],
    capture_dbg: Mapping[str, Any],
) -> Optional[Dict[str, float]]:
    """Stamp FOV length scales onto term params + measurements for normalized losses."""
    hw = _first_frame_hw(capture_dbg)
    if hw is None:
        return None
    h, w = hw
    diag = float(math.hypot(w, h))
    width_scale = float(min(w, h)) / 4.0
    info = {"height": float(h), "width": float(w), "diagonal": diag, "width_scale": width_scale}
    for term in terms:
        row = measurements.get(term.id)
        if isinstance(row, dict):
            row["frame_hw"] = [h, w]
        # Align: FOV diagonal as rms scale unless operator set one.
        if term.metric in ("rms_distance", "rms_distance_px"):
            term.params.setdefault("rms_scale_px", diag)
            term.params.setdefault("loss_cap", 2.0)
            term.params.setdefault("frame_hw", [h, w])
        # Width / magnitude minimize: fraction of min dimension unless set.
        if term.metric == "minimize_value" and (
            term.params.get("normalize_by_fov")
            or term.kernel_id in ("builtin.gaussian_beam_fit", "builtin.beam_shift")
        ):
            term.params.setdefault("normalize_by_fov", True)
            term.params.setdefault("value_scale_px", width_scale)
            term.params.setdefault("loss_cap", 2.0)
            term.params.setdefault("frame_hw", [h, w])
        if term.metric == "beam_presence":
            term.params.setdefault("absent_penalty", 2.0)
    return info


# Extra loss when beam is absent: base absent (~2) + weight * ||Δu|| from last
# present point. Large ejected steps look worse than small ones so COBYLA prefers
# returning toward the last-good region.
DEFAULT_ABSENT_STEP_BARRIER = 1.0


def _l2_u(
    space: NormalizedSearchSpace,
    a: Mapping[str, float],
    b: Mapping[str, float],
) -> float:
    """Euclidean distance in normalized search space between two physical points."""
    acc = 0.0
    for vid in space.variable_ids:
        ua = space.normalize_scalar(vid, float(a[vid]))
        ub = space.normalize_scalar(vid, float(b[vid]))
        d = ua - ub
        acc += d * d
    return math.sqrt(acc)


def _any_presence_absent(presence_dbg: Optional[Mapping[str, Any]]) -> bool:
    if not presence_dbg:
        return False
    return any(
        isinstance(v, Mapping) and v.get("presence_ok") is False
        for v in presence_dbg.values()
    )


def _build_policy_debug(
    *,
    terms: Sequence[ObjectiveTerm],
    measurements: Mapping[str, Mapping[str, Any]],
    term_losses: Mapping[str, float],
    total_loss: float,
    stop_loss: Optional[float],
    scale_dbg: Optional[Mapping[str, Any]],
    presence_dbg: Optional[Mapping[str, Any]],
    peak_refs: Mapping[str, float],
) -> Dict[str, Any]:
    """Compact per-eval policy snapshot for Twin stage debug + edge logs."""
    term_rows: Dict[str, Any] = {}
    for term in terms:
        row = measurements.get(term.id) if isinstance(measurements, Mapping) else None
        row = row if isinstance(row, Mapping) else {}
        feats = row.get("features") if isinstance(row.get("features"), (list, tuple)) else []
        entry: Dict[str, Any] = {
            "metric": term.metric,
            "weight": float(term.weight),
            "loss": term_losses.get(term.id),
            "kernel_id": term.kernel_id,
        }
        _attach_viz_params(entry, term)
        for key in (
            "rms_scale_px",
            "value_scale_px",
            "loss_cap",
            "normalize_by_fov",
            "min_peak_ratio",
            "peak_feature_index",
            "latch_peak_ref",
        ):
            if key in term.params:
                entry[key] = term.params[key]
        if feats:
            entry["features_preview"] = [round(float(x), 4) for x in list(feats)[:5]]
            if len(feats) >= 2 and term.metric in ("rms_distance", "rms_distance_px"):
                entry["detected_xy"] = [
                    round(float(feats[0]), 3),
                    round(float(feats[1]), 3),
                ]
        if "peak" in row:
            entry["peak"] = row.get("peak")
        if "peak_ref" in row or term.id in peak_refs:
            entry["peak_ref"] = row.get("peak_ref", peak_refs.get(term.id))
        if "presence_ok" in row:
            entry["presence_ok"] = row.get("presence_ok")
        if row.get("presence_failed"):
            entry["presence_failed"] = True
        term_rows[term.id] = entry

    hit = (
        stop_loss is not None
        and math.isfinite(float(total_loss))
        and float(total_loss) <= float(stop_loss)
    )
    policy: Dict[str, Any] = {
        "ok": True,
        "loss": float(total_loss) if math.isfinite(float(total_loss)) else None,
        "stop_loss": float(stop_loss) if stop_loss is not None else None,
        "would_early_stop": bool(hit),
        "terms": term_rows,
    }
    if scale_dbg:
        policy["fov"] = dict(scale_dbg)
        policy["ok"] = True
    else:
        policy["fov"] = None
        # Missing FOV is a warning when any spatial metric is active.
        needs_fov = any(
            t.metric in ("rms_distance", "rms_distance_px")
            or t.params.get("normalize_by_fov")
            for t in terms
        )
        if needs_fov:
            policy["ok"] = False
            policy["error"] = "no frame_hw / FOV scale from capture (normalized loss may be wrong)"
    if presence_dbg:
        policy["presence"] = dict(presence_dbg)
        if any(
            isinstance(v, Mapping) and v.get("presence_ok") is False
            for v in presence_dbg.values()
        ):
            policy["presence_absent"] = True
    return policy


def run_optimization_session(
    pipeline: OptimizationPipeline | Mapping[str, Any],
    x0: Mapping[str, float],
    *,
    capture: CaptureSource,
    router: ActuatorRouter,
    kernels_dir: Optional[Path] = None,
    session_id: str = "",
    progress_callback: Optional[Callable[..., None]] = None,
    should_abort: Optional[Callable[[], bool]] = None,
    should_accept: Optional[Callable[[], bool]] = None,
) -> OptimizationResult:
    """
    Inner macro loop on the edge: block COBYLA over capture→kernel→loss→actuate.

    Lab-specific work is confined to ``capture`` and ``router``.
    Phase 6: clearance interlock, max-delta vs x0, keep_best / rollback settle.
    """
    spec = parse_pipeline(dict(pipeline) if isinstance(pipeline, Mapping) else pipeline)
    kdir = Path(kernels_dir) if kernels_dir is not None else default_kernels_dir()
    variables_by_id = {v.id: v for v in spec.variables}
    space = NormalizedSearchSpace(spec.variables, x0)
    x0_vals = {vid: float(x0[vid]) for vid in space.variable_ids}
    current = dict(x0_vals)
    best = dict(current)
    best_loss = float("inf")
    evals = 0
    aborted = False
    early_stopped = False
    refusal: Optional[str] = None
    trace: list[Dict[str, Any]] = []
    delta_limits = max_delta_limits(spec.solver.constraints)
    # First-eval full-frame peak latch for beam-presence gating (per term id).
    peak_refs: Dict[str, float] = {}
    # First-eval flux/scalar latch for ratio_to_ref maximize metrics.
    value_refs: Dict[str, float] = {}
    # Last physical point where beam presence was OK (restore target after absent).
    last_present: Dict[str, float] = dict(x0_vals)
    absent_step_barrier = DEFAULT_ABSENT_STEP_BARRIER
    raw_barrier = getattr(spec.solver, "absent_step_barrier", None)
    if raw_barrier is not None:
        try:
            bw = float(raw_barrier)
            if math.isfinite(bw) and bw >= 0.0:
                absent_step_barrier = bw
        except (TypeError, ValueError):
            pass
    raw_stop = getattr(spec.solver, "stop_loss", None)
    stop_loss: Optional[float] = None
    if raw_stop is not None:
        try:
            sl = float(raw_stop)
            if math.isfinite(sl) and sl >= 0.0:
                stop_loss = sl
        except (TypeError, ValueError):
            stop_loss = None

    class _Aborted(Exception):
        pass

    class _EarlyStop(Exception):
        """Good-enough success path (stop_loss or operator accept) — not an abort."""

        def __init__(self, reason: str = "stop_loss") -> None:
            super().__init__(reason)
            self.reason = reason

    def _check_abort() -> None:
        if should_abort is not None and should_abort():
            raise _Aborted()

    def _check_accept() -> None:
        if should_accept is not None and should_accept():
            raise _EarlyStop("operator_accept")

    def _objective_for_block(
        block_id: str, *, continuous: bool
    ) -> Callable[[Mapping[str, float]], float]:
        def _fn(physical: Mapping[str, float]) -> float:
            nonlocal evals, best_loss, best, current, refusal, aborted, early_stopped
            nonlocal last_present
            _check_abort()
            _check_accept()
            if continuous:
                try:
                    router.assert_optical_path_clear()
                except ClearanceError as exc:
                    refusal = str(exc)
                    aborted = True
                    if progress_callback is not None:
                        progress_callback(
                            step=evals,
                            best_loss=best_loss,
                            stages={
                                "capture": {"ok": None, "error": None},
                                "kernel": {"ok": None, "error": None},
                                "actuate": {
                                    "ok": False,
                                    "error": str(exc),
                                    "refused": "clearance",
                                },
                            },
                            values={vid: float(physical[vid]) for vid in space.variable_ids},
                        )
                    raise _Aborted() from exc
            if delta_limits is not None:
                try:
                    check_max_delta(physical, x0_vals, spec.variables, delta_limits)
                except MaxDeltaError as exc:
                    # Refuse the step with a large penalty; do not actuate.
                    loss, terms = evaluate_weighted_sum(
                        spec.objective, {}, on_invalid="penalty"
                    )
                    evals += 1
                    record = {
                        "eval": evals,
                        "loss": loss,
                        "terms": terms,
                        "u": space.normalize_dict(physical),
                        "block_id": block_id,
                        "values": {vid: float(physical[vid]) for vid in space.variable_ids},
                        "refused": "max_delta_from_start",
                        "stages": {
                            "capture": {"ok": None},
                            "kernel": {"ok": None},
                            "actuate": {
                                "ok": False,
                                "refused": "max_delta_from_start",
                                "error": str(exc),
                                "proposed": {
                                    vid: float(physical[vid]) for vid in space.variable_ids
                                },
                            },
                        },
                    }
                    trace.append(record)
                    if progress_callback is not None:
                        progress_callback(step=evals, best_loss=best_loss, **record)
                    return loss

            delta_from_x0 = {
                vid: float(physical[vid]) - float(x0_vals[vid]) for vid in space.variable_ids
            }
            try:
                router.apply_eval(physical, block_id=block_id)
                actuate_dbg: Dict[str, Any] = {
                    "ok": True,
                    "block_id": block_id,
                    "delta_from_x0": delta_from_x0,
                    "applied": {vid: float(physical[vid]) for vid in space.variable_ids},
                    "error": None,
                }
            except Exception as exc:  # noqa: BLE001
                actuate_dbg = {
                    "ok": False,
                    "block_id": block_id,
                    "delta_from_x0": delta_from_x0,
                    "error": str(exc),
                }
                evals += 1
                record = {
                    "eval": evals,
                    "loss": float("nan"),
                    "terms": {},
                    "u": space.normalize_dict(physical),
                    "block_id": block_id,
                    "values": {vid: float(physical[vid]) for vid in space.variable_ids},
                    "stages": {
                        "capture": {"ok": None},
                        "kernel": {"ok": None},
                        "actuate": actuate_dbg,
                    },
                }
                trace.append(record)
                if progress_callback is not None:
                    progress_callback(step=evals, best_loss=best_loss, **record)
                raise

            current = {vid: float(physical[vid]) for vid in space.variable_ids}
            trial_values = dict(current)
            stage_dbg: Dict[str, Any] = {
                "capture": {"ok": True},
                "kernel": {"ok": True},
                "actuate": actuate_dbg,
            }
            try:
                measurements, ck_dbg = _collect_measurements(
                    spec, capture, kernels_dir=kdir, physical=physical
                )
                cap_part = ck_dbg.get("capture") or {}
                scale_dbg = inject_frame_scales(
                    measurements, spec.objective.terms, cap_part
                )
                presence_dbg = apply_peak_presence_gate(
                    measurements, spec.objective.terms, peak_refs
                )
                value_ref_dbg = apply_value_ref_latch(
                    measurements, spec.objective.terms, value_refs
                )
                stage_dbg["capture"] = cap_part or stage_dbg["capture"]
                stage_dbg["kernel"] = ck_dbg.get("kernel") or stage_dbg["kernel"]
                # Stamp frame_hw onto kernel term rows after FOV injection (for Twin overlays).
                kern = stage_dbg.get("kernel")
                if isinstance(kern, dict) and isinstance(kern.get("terms"), dict):
                    for term in spec.objective.terms:
                        trow = kern["terms"].get(term.id)
                        if isinstance(trow, dict):
                            _attach_viz_params(trow, term)
                if scale_dbg:
                    stage_dbg["scales"] = scale_dbg
                if presence_dbg:
                    stage_dbg["presence"] = presence_dbg
                if value_ref_dbg:
                    stage_dbg["value_ref"] = value_ref_dbg
                loss, terms = evaluate_weighted_sum(
                    spec.objective, measurements, on_invalid="penalty"
                )
                # Beam lost: distance-aware barrier + restore last-present pose so
                # the next COBYLA trial does not start from an ejected state.
                if _any_presence_absent(presence_dbg):
                    du = _l2_u(space, physical, last_present)
                    barrier = float(absent_step_barrier) * float(du)
                    if barrier > 0.0 and math.isfinite(barrier):
                        loss = float(loss) + barrier
                        terms = dict(terms)
                        terms["__absent_step_barrier__"] = barrier
                    restore_vals = {
                        vid: float(last_present[vid]) for vid in space.variable_ids
                    }
                    restore_dbg: Dict[str, Any] = {
                        "ok": True,
                        "reason": "presence_absent",
                        "du_from_last_present": round(float(du), 6),
                        "barrier": round(float(barrier), 6),
                        "restored": restore_vals,
                        "error": None,
                    }
                    try:
                        router.apply_eval(restore_vals, block_id=f"{block_id}:restore")
                        current = dict(restore_vals)
                    except Exception as restore_exc:  # noqa: BLE001
                        restore_dbg["ok"] = False
                        restore_dbg["error"] = str(restore_exc)
                    stage_dbg["actuate_restore"] = restore_dbg
                    print(
                        f"[optimize.policy] presence ABSENT→restore "
                        f"du={du:.4f} barrier={barrier:.4f} loss={loss!r}",
                        flush=True,
                    )
                else:
                    # Keep last-present for restore target (only when gate ran OK).
                    if presence_dbg:
                        last_present = {
                            vid: float(physical[vid]) for vid in space.variable_ids
                        }
                stage_dbg["policy"] = _build_policy_debug(
                    terms=spec.objective.terms,
                    measurements=measurements,
                    term_losses=terms,
                    total_loss=loss,
                    stop_loss=stop_loss,
                    scale_dbg=scale_dbg,
                    presence_dbg=presence_dbg,
                    peak_refs=peak_refs,
                )
                if stage_dbg.get("actuate_restore"):
                    pol = stage_dbg["policy"]
                    if isinstance(pol, dict):
                        pol["presence_restore"] = dict(stage_dbg["actuate_restore"])
                print(
                    f"[optimize.policy] eval pending loss={loss!r} "
                    f"stop_loss={stop_loss!r} "
                    f"fov={scale_dbg} presence={presence_dbg} "
                    f"terms={ {k: round(float(v), 4) for k, v in terms.items()} }",
                    flush=True,
                )
            except (KernelError, MeasurementInvalid, KeyError) as exc:
                stage_dbg["kernel"] = {
                    "ok": False,
                    "error": str(exc),
                    "terms": (stage_dbg.get("kernel") or {}).get("terms") or {},
                }
                loss, terms = evaluate_weighted_sum(
                    spec.objective, {}, on_invalid="penalty"
                )
                stage_dbg["policy"] = {
                    "ok": False,
                    "error": f"measure failed before policy: {exc}",
                    "loss": loss,
                    "stop_loss": stop_loss,
                }
                print(
                    f"[optimize.policy] measure FAIL: {exc}",
                    flush=True,
                )
            evals += 1
            u = space.normalize_dict(physical)
            record: Dict[str, Any] = {
                "eval": evals,
                "loss": loss,
                "terms": terms,
                "u": u,
                "block_id": block_id,
                # Trial that was measured (restore may have moved hardware back).
                "values": dict(trial_values),
                "stages": stage_dbg,
            }
            # Stamp eval number onto policy for Twin correlation.
            pol = stage_dbg.get("policy")
            if isinstance(pol, dict):
                pol["eval"] = evals
            trace.append(record)
            if loss < best_loss:
                best_loss = loss
                best = dict(current)
            hit_stop = (
                stop_loss is not None
                and math.isfinite(loss)
                and float(loss) <= float(stop_loss)
            )
            if hit_stop:
                early_stopped = True
                record["early_stop"] = True
                record["early_stop_reason"] = "stop_loss"
                record["stop_loss"] = float(stop_loss)
                if isinstance(pol, dict):
                    pol["early_stop"] = True
                    pol["would_early_stop"] = True
                    pol["early_stop_reason"] = "stop_loss"
                print(
                    f"[optimize.policy] EARLY STOP eval={evals} loss={loss} "
                    f"stop_loss={stop_loss}",
                    flush=True,
                )
            if progress_callback is not None:
                progress_callback(step=evals, best_loss=best_loss, **record)
            if hit_stop:
                raise _EarlyStop("stop_loss")
            _check_accept()
            return loss

        return _fn

    early_stop_reason = None
    try:
        for block in spec.solver.blocks:
            _check_abort()
            _check_accept()
            block_vars = [variables_by_id[vid] for vid in block.variable_ids]
            invasive = any(v.physical_type == "invasive_discrete" for v in block_vars)
            continuous = not invasive
            if invasive:
                router.enter_invasive_block(block.variable_ids)
            else:
                try:
                    router.enter_continuous_block(block.variable_ids)
                    router.assert_optical_path_clear()
                except ClearanceError as exc:
                    refusal = str(exc)
                    aborted = True
                    try:
                        router.exit_block(block.id)
                    except Exception:  # noqa: BLE001
                        pass
                    break

            try:
                obj_fn = _objective_for_block(block.id, continuous=continuous)
                remaining = max(1, spec.solver.max_total_evals - evals)

                for _pass in range(block.passes):
                    _check_abort()
                    _check_accept()
                    if early_stopped or evals >= spec.solver.max_total_evals:
                        break
                    per_block = min(block.max_evals, remaining)
                    try:
                        current = run_block_cobyla(
                            block,
                            block.variable_ids,
                            space,
                            current,
                            obj_fn,
                            max_evals=per_block,
                        )
                    except _EarlyStop as stop_exc:
                        # COBYLA interrupted by stop_loss / operator accept.
                        early_stopped = True
                        early_stop_reason = getattr(stop_exc, "reason", None) or "stop_loss"
                        if best_loss < float("inf"):
                            current = dict(best)
                        break
                    remaining = max(1, spec.solver.max_total_evals - evals)
            finally:
                try:
                    router.exit_block(block.id)
                except Exception:  # noqa: BLE001
                    pass

            if aborted or early_stopped or evals >= spec.solver.max_total_evals:
                break
    except _Aborted:
        aborted = True
    except _EarlyStop as stop_exc:
        early_stopped = True
        early_stop_reason = getattr(stop_exc, "reason", None) or "stop_loss"
        if best_loss < float("inf"):
            current = dict(best)
        if early_stop_reason == "operator_accept":
            print(
                f"[optimize.policy] OPERATOR ACCEPT evals={evals} "
                f"best_loss={best_loss}",
                flush=True,
            )
            if progress_callback is not None and trace:
                # Annotate last progress row for Twin stage debug.
                last = trace[-1]
                last["early_stop"] = True
                last["early_stop_reason"] = "operator_accept"
                pol = (last.get("stages") or {}).get("policy")
                if isinstance(pol, dict):
                    pol["early_stop"] = True
                    pol["early_stop_reason"] = "operator_accept"
                try:
                    progress_callback(
                        step=evals,
                        best_loss=best_loss,
                        early_stop=True,
                        early_stop_reason="operator_accept",
                        **{k: v for k, v in last.items() if k not in ("eval",)},
                    )
                except TypeError:
                    pass

    final = settle_final_values(
        x0=x0_vals,
        current=current,
        best=best,
        keep_best=bool(spec.solver.keep_best),
        rollback_on_fail=bool(spec.solver.rollback_on_fail),
        aborted=aborted,
    )
    # Re-enter briefly so lab routers that require an open block can settle.
    if final != current or (aborted and (spec.solver.rollback_on_fail or spec.solver.keep_best)):
        all_ids = list(space.variable_ids)
        try:
            router.enter_continuous_block(all_ids)
            try:
                if not any(
                    variables_by_id[vid].physical_type == "invasive_discrete"
                    for vid in all_ids
                ):
                    router.assert_optical_path_clear()
                router.apply_eval(final, block_id="settle")
                current = dict(final)
            finally:
                router.exit_block("settle")
        except Exception:  # noqa: BLE001 — settle is best-effort after abort
            pass

    if early_stopped and early_stop_reason is None:
        early_stop_reason = "stop_loss"
    return OptimizationResult(
        session_id=session_id or (spec.session_label or "ensemble"),
        best_loss=best_loss if best_loss < float("inf") else float("inf"),
        final_values=final,
        evals=evals,
        aborted=aborted,
        early_stopped=early_stopped,
        refusal=refusal,
        trace=trace,
        early_stop_reason=early_stop_reason,
    )


__all__ = [
    "DEFAULT_ABSENT_STEP_BARRIER",
    "OptimizationResult",
    "run_optimization_session",
]
