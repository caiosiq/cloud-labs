"""Metric: minimize a selected feature value (e.g. beam radius)."""
from __future__ import annotations

import math
from typing import Any, Mapping, Optional, Sequence

from lab_model.execution.optimization.spec import ObjectiveTermSpec

from .registry import register_metric

DEFAULT_LOSS_CAP = 2.0


def _feature_index(src: Any) -> int:
    idx = getattr(src, "feature_index", None)
    extra = getattr(src, "__pydantic_extra__", None)
    if idx is None and isinstance(extra, dict):
        idx = extra.get("feature_index")
    if isinstance(idx, (list, tuple)):
        if not idx:
            return 0
        return int(idx[0])
    try:
        return int(idx if idx is not None else 0)
    except (TypeError, ValueError):
        return 0


def _features_from_measurement(measurement: Mapping[str, Any]) -> Sequence[float]:
    raw = measurement.get("features")
    if isinstance(raw, (list, tuple)):
        return [float(x) for x in raw]
    scalar = measurement.get("scalar")
    if scalar is not None:
        return [float(scalar)]
    return []


def _extra(src: Any) -> Mapping[str, Any]:
    raw = getattr(src, "__pydantic_extra__", None)
    return raw if isinstance(raw, dict) else {}


def _loss_cap(src: Any) -> float:
    extra = _extra(src)
    raw = getattr(src, "loss_cap", None)
    if raw is None:
        raw = extra.get("loss_cap", DEFAULT_LOSS_CAP)
    try:
        cap = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_LOSS_CAP
    return cap if math.isfinite(cap) and cap > 0 else DEFAULT_LOSS_CAP


def _length_scale(measurement: Mapping[str, Any], src: Any) -> float:
    extra = _extra(src)
    for key in ("rms_scale_px", "value_scale_px"):
        raw = getattr(src, key, None)
        if raw is None:
            raw = extra.get(key)
        if raw is None:
            raw = measurement.get(key)
        if raw is None:
            continue
        try:
            scale = float(raw)
        except (TypeError, ValueError):
            continue
        if math.isfinite(scale) and scale > 1e-9:
            return scale
    norm = getattr(src, "normalize", None) or extra.get("normalize")
    if isinstance(norm, Mapping) and "max" in norm:
        try:
            scale = float(norm["max"]) - float(norm.get("min", 0.0))
        except (TypeError, ValueError):
            scale = 0.0
        if math.isfinite(scale) and scale > 1e-9:
            return scale
    hw = measurement.get("frame_hw") or getattr(src, "frame_hw", None) or extra.get("frame_hw")
    if isinstance(hw, (list, tuple)) and len(hw) >= 2:
        try:
            h, w = float(hw[0]), float(hw[1])
        except (TypeError, ValueError):
            h, w = 0.0, 0.0
        diag = math.hypot(w, h)
        if diag > 1e-9:
            return diag
    return 1.0


def _normalize_spatial(raw_px: float, measurement: Mapping[str, Any], src: Any) -> float:
    scale = _length_scale(measurement, src)
    return min(max(0.0, float(raw_px) / scale), _loss_cap(src))


@register_metric(metric_id="minimize_value")
def metric_minimize_value(
    measurement: Mapping[str, Any],
    term: ObjectiveTermSpec,
) -> float:
    """Loss = selected feature value (optionally FOV-/scale-normalized)."""
    feats = _features_from_measurement(measurement)
    if not feats:
        return 0.0
    src = term.source
    extra = _extra(src)
    idx = _feature_index(src)
    if idx < 0 or idx >= len(feats):
        raise ValueError(f"feature_index {idx} out of range for len={len(feats)}")
    value = float(feats[idx])
    use_abs = getattr(src, "use_abs", None)
    if use_abs is None:
        use_abs = extra.get("use_abs")
    if use_abs:
        value = abs(value)
    want_norm = bool(
        getattr(src, "normalize_by_fov", None)
        or extra.get("normalize_by_fov")
        or getattr(src, "value_scale_px", None) is not None
        or extra.get("value_scale_px") is not None
        or getattr(src, "rms_scale_px", None) is not None
        or extra.get("rms_scale_px") is not None
        or measurement.get("frame_hw") is not None
    )
    if want_norm:
        return _normalize_spatial(value, measurement, src)
    return value


@register_metric(metric_id="rms_distance")
def metric_rms_distance(
    measurement: Mapping[str, Any],
    term: ObjectiveTermSpec,
) -> float:
    """Normalized RMS distance (~0–1, soft-capped near loss_cap)."""
    src = term.source
    if measurement.get("presence_ok") is False:
        return _loss_cap(src)
    feats = _features_from_measurement(measurement)
    extra = _extra(src)
    idx = getattr(src, "feature_index", None)
    if idx is None:
        idx = extra.get("feature_index")
    if isinstance(idx, (list, tuple)) and len(idx) >= 2:
        i0, i1 = int(idx[0]), int(idx[1])
    else:
        i0, i1 = 0, 1
    if not feats or max(i0, i1) >= len(feats):
        return 0.0
    cx, cy = float(feats[i0]), float(feats[i1])
    target = getattr(src, "target_px", None) or getattr(src, "target", None)
    if target is None:
        target = extra.get("target_px") or extra.get("target") or extra.get("target_vec")
    if isinstance(target, Mapping):
        tx = float(target.get("x", target.get("0", 0.0)))
        ty = float(target.get("y", target.get("1", 0.0)))
    elif isinstance(target, (list, tuple)) and len(target) >= 2:
        tx, ty = float(target[0]), float(target[1])
    else:
        tx, ty = 0.0, 0.0
    rms_px = math.sqrt((cx - tx) ** 2 + (cy - ty) ** 2)
    return _normalize_spatial(rms_px, measurement, src)


@register_metric(metric_id="beam_presence")
def metric_beam_presence(
    measurement: Mapping[str, Any],
    term: ObjectiveTermSpec,
) -> float:
    """0 when peak ≥ ratio·peak_ref; else absent_penalty (default 2.0)."""
    src = term.source
    extra = _extra(src)
    absent = float(extra.get("absent_penalty", getattr(src, "absent_penalty", 2.0)) or 2.0)
    if measurement.get("presence_ok") is False:
        return absent
    feats = _features_from_measurement(measurement)
    idx = getattr(src, "peak_feature_index", None)
    if idx is None:
        idx = extra.get("peak_feature_index", 2)
    try:
        i = int(idx)
    except (TypeError, ValueError):
        i = 2
    peak = None
    if feats and 0 <= i < len(feats):
        peak = float(feats[i])
    elif measurement.get("peak") is not None:
        peak = float(measurement["peak"])
    if peak is None:
        raise ValueError("missing peak for beam_presence")
    peak_ref = getattr(src, "peak_ref", None)
    if peak_ref is None:
        peak_ref = extra.get("peak_ref")
    if peak_ref is None:
        return 0.0
    ratio = float(extra.get("min_peak_ratio", getattr(src, "min_peak_ratio", 0.5)) or 0.5)
    if peak >= ratio * float(peak_ref):
        return 0.0
    return absent


@register_metric(metric_id="signed_axis_offset")
def metric_signed_axis_offset(
    measurement: Mapping[str, Any],
    term: ObjectiveTermSpec,
) -> float:
    """Maximize signed CoM displacement along one axis (see edge metrics)."""
    src = term.source
    extra = _extra(src)
    if measurement.get("presence_ok") is False:
        return _loss_cap(src)

    feats = _features_from_measurement(measurement)
    idx = getattr(src, "feature_index", None)
    if idx is None:
        idx = extra.get("feature_index", [0, 1])
    if isinstance(idx, (list, tuple)) and len(idx) >= 2:
        i0, i1 = int(idx[0]), int(idx[1])
    else:
        i0, i1 = 0, 1
    if not feats or max(i0, i1) >= len(feats):
        raise ValueError("empty or incomplete features for signed_axis_offset")
    cx, cy = float(feats[i0]), float(feats[i1])

    origin = getattr(src, "origin_px", None) or extra.get("origin_px")
    if origin is None:
        origin = getattr(src, "target_px", None) or extra.get("target_px")
    if origin is None:
        origin = getattr(src, "target", None) or extra.get("target")
    if isinstance(origin, Mapping):
        ox = float(origin.get("x", origin.get("0", 0.0)))
        oy = float(origin.get("y", origin.get("1", 0.0)))
    elif isinstance(origin, (list, tuple)) and len(origin) >= 2:
        ox, oy = float(origin[0]), float(origin[1])
    else:
        raise ValueError("missing origin_px for signed_axis_offset")

    axis_raw = str(
        getattr(src, "axis", None) or extra.get("axis", "x") or "x"
    ).strip().lower()
    if axis_raw in ("y", "1", "cy"):
        coord, origin_v = cy, oy
    else:
        coord, origin_v = cx, ox

    direction_raw = getattr(src, "direction", None)
    if direction_raw is None:
        direction_raw = extra.get("direction", 1)
    try:
        direction = float(direction_raw)
    except (TypeError, ValueError):
        direction = 1.0
    direction = 1.0 if direction >= 0 else -1.0

    signed_px = direction * (coord - origin_v)
    scale = _length_scale(measurement, src)
    raw = -float(signed_px) / scale
    return max(raw, -_loss_cap(src))


__all__ = [
    "metric_beam_presence",
    "metric_minimize_value",
    "metric_rms_distance",
    "metric_signed_axis_offset",
]
