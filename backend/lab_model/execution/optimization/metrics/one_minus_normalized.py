"""Metric: one minus normalized scalar in [min, max]."""
from __future__ import annotations

from typing import Any, Mapping

from lab_model.execution.optimization.spec import ObjectiveTermSpec

from .registry import register_metric


def _clip(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


@register_metric(metric_id="one_minus_normalized")
def metric_one_minus_normalized(
    measurement: Mapping[str, Any],
    term: ObjectiveTermSpec,
) -> float:
    norm = term.source.normalize
    lo = float(norm.min) if norm is not None else 0.0
    hi = float(norm.max) if norm is not None else 1.0
    span = hi - lo
    if abs(span) < 1e-15:
        return 0.0
    raw = measurement.get("scalar")
    if raw is None:
        raw = measurement.get("power")
    try:
        val = float(raw if raw is not None else lo)
    except (TypeError, ValueError):
        val = lo
    normalized = _clip((val - lo) / span, 0.0, 1.0)
    return 1.0 - normalized


__all__ = ["metric_one_minus_normalized"]
