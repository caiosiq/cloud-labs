"""Metric: minimize a selected feature value (e.g. beam radius)."""
from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence, Union

from lab_model.execution.optimization.spec import ObjectiveTermSpec

from .registry import register_metric


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


@register_metric(metric_id="minimize_value")
def metric_minimize_value(
    measurement: Mapping[str, Any],
    term: ObjectiveTermSpec,
) -> float:
    """Loss = selected feature value (optionally abs via source.use_abs)."""
    feats = _features_from_measurement(measurement)
    if not feats:
        return 0.0
    idx = _feature_index(term.source)
    if idx < 0 or idx >= len(feats):
        raise ValueError(f"feature_index {idx} out of range for len={len(feats)}")
    value = float(feats[idx])
    use_abs = getattr(term.source, "use_abs", None)
    extra = getattr(term.source, "__pydantic_extra__", None)
    if use_abs is None and isinstance(extra, dict):
        use_abs = extra.get("use_abs")
    if use_abs:
        return abs(value)
    return value


@register_metric(metric_id="rms_distance")
def metric_rms_distance(
    measurement: Mapping[str, Any],
    term: ObjectiveTermSpec,
) -> float:
    """RMS distance between selected feature pair and target x/y (or target_vec)."""
    import math

    feats = _features_from_measurement(measurement)
    src = term.source
    idx = getattr(src, "feature_index", None)
    extra = getattr(src, "__pydantic_extra__", None) or {}
    if idx is None and isinstance(extra, dict):
        idx = extra.get("feature_index")
    if isinstance(idx, (list, tuple)) and len(idx) >= 2:
        i0, i1 = int(idx[0]), int(idx[1])
    else:
        i0, i1 = 0, 1
    if not feats or max(i0, i1) >= len(feats):
        return 0.0
    cx, cy = float(feats[i0]), float(feats[i1])
    target = getattr(src, "target_px", None) or getattr(src, "target", None)
    if target is None and isinstance(extra, dict):
        target = extra.get("target_px") or extra.get("target") or extra.get("target_vec")
    if isinstance(target, Mapping):
        tx = float(target.get("x", target.get("0", 0.0)))
        ty = float(target.get("y", target.get("1", 0.0)))
    elif isinstance(target, (list, tuple)) and len(target) >= 2:
        tx, ty = float(target[0]), float(target[1])
    else:
        tx, ty = 0.0, 0.0
    return math.sqrt((cx - tx) ** 2 + (cy - ty) ** 2)


__all__ = ["metric_minimize_value", "metric_rms_distance"]
