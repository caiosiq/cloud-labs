"""Metric: minimize via value/ref (first-eval latch on the edge)."""
from __future__ import annotations

from typing import Any, Mapping

from lab_model.execution.optimization.spec import ObjectiveTermSpec

from .registry import register_metric


def _feature_index(src: Any) -> int:
    idx = getattr(src, "feature_index", None)
    if idx is None:
        extra = getattr(src, "model_extra", None) or {}
        idx = extra.get("feature_index", 0)
    if isinstance(idx, (list, tuple)):
        idx = idx[0] if idx else 0
    try:
        return int(idx)
    except (TypeError, ValueError):
        return 0


def _ratio(measurement: Mapping[str, Any], term: ObjectiveTermSpec, *, invert: bool) -> float:
    src = term.source
    feats = measurement.get("features")
    idx = _feature_index(src)
    raw = None
    if isinstance(feats, (list, tuple)) and 0 <= idx < len(feats):
        raw = feats[idx]
    if raw is None:
        raw = measurement.get("value") or measurement.get("scalar") or measurement.get("power")
    val = float(raw)
    extra = getattr(src, "model_extra", None) or {}
    ref = getattr(src, "value_ref", None)
    if ref is None:
        ref = extra.get("value_ref")
    if ref is None:
        ref = measurement.get("value_ref")
    ref_f = float(ref)
    if ref_f <= 0.0 or val <= 0.0:
        return 2.0
    return float(ref_f / val) if invert else float(val / ref_f)


@register_metric(metric_id="ratio_to_ref")
def metric_ratio_to_ref(
    measurement: Mapping[str, Any],
    term: ObjectiveTermSpec,
) -> float:
    return _ratio(measurement, term, invert=True)


@register_metric(metric_id="ratio_from_ref")
def metric_ratio_from_ref(
    measurement: Mapping[str, Any],
    term: ObjectiveTermSpec,
) -> float:
    return _ratio(measurement, term, invert=False)


__all__ = ["metric_ratio_from_ref", "metric_ratio_to_ref"]
