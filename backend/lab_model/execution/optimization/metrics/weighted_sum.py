"""Weighted-sum objective over registered metrics."""
from __future__ import annotations

from typing import Any, Dict, Mapping

from lab_model.execution.optimization.spec import ObjectiveSpec

from . import one_minus_normalized as one_minus_normalized  # noqa: F401 — register
from . import rms_distance_px as rms_distance_px  # noqa: F401 — register
from . import squared_error as squared_error  # noqa: F401 — register
from . import feature_metrics as feature_metrics  # noqa: F401 — register
from .registry import get_metric


def evaluate_weighted_sum(
    objective: ObjectiveSpec,
    measurements: Mapping[str, Mapping[str, Any]],
) -> tuple[float, Dict[str, float]]:
    """
    Compute scalar loss and per-term contributions.

    ``measurements`` maps term id → measurement dict from the bench backend.
    """
    terms_out: Dict[str, float] = {}
    total = 0.0
    sign = 1.0 if objective.minimize else -1.0
    for term in objective.terms:
        metric_fn = get_metric(term.metric)
        if metric_fn is None:
            raise ValueError(f"unsupported objective metric: {term.metric!r}")
        raw = metric_fn(measurements.get(term.id, {}), term)
        weighted = float(term.weight) * raw
        terms_out[term.id] = weighted
        total += weighted
    return sign * total, terms_out


__all__ = ["evaluate_weighted_sum"]
