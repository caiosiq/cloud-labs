"""Metric: squared deviation from a scalar target, ``(M - M0)^2``."""
from __future__ import annotations

from typing import Any, Mapping

from lab_model.optimization.spec import ObjectiveTermSpec

from .registry import register_metric


@register_metric(metric_id="squared_error")
def metric_squared_error(
    measurement: Mapping[str, Any],
    term: ObjectiveTermSpec,
) -> float:
    """Loss ``(scalar - target_scalar)^2``.

    ``target_scalar`` / ``M0`` is authored on the objective source (baked into the
    job at submit time — the edge loop cannot call back to Python for M0).

    When ``feature_index`` is set and ``features`` are present, uses that channel.
    """
    src = term.source
    target = getattr(src, "target_scalar", None)
    if target is None and isinstance(getattr(src, "model_extra", None), dict):
        target = src.model_extra.get("target_scalar")
    # Pydantic v2 extra fields may also appear via __pydantic_extra__
    extra = getattr(src, "__pydantic_extra__", None)
    if target is None and isinstance(extra, dict):
        target = extra.get("target_scalar")
    try:
        m0 = float(target if target is not None else 0.0)
    except (TypeError, ValueError):
        m0 = 0.0

    raw = measurement.get("scalar")
    feats = measurement.get("features")
    idx = getattr(src, "feature_index", None)
    if idx is None and isinstance(extra, dict):
        idx = extra.get("feature_index")
    if isinstance(feats, (list, tuple)) and feats:
        try:
            i = int(idx if idx is not None else 0)
        except (TypeError, ValueError):
            i = 0
        if 0 <= i < len(feats):
            raw = feats[i]
    if raw is None:
        raw = measurement.get("power")
    try:
        m = float(raw if raw is not None else m0)
    except (TypeError, ValueError):
        m = m0
    err = m - m0
    return float(err * err)


__all__ = ["metric_squared_error"]
