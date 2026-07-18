"""Metric: RMS pixel distance from target centroid."""
from __future__ import annotations

import math
from typing import Any, Mapping

from lab_model.execution.optimization.spec import ObjectiveTermSpec

from .registry import register_metric


@register_metric(metric_id="rms_distance_px")
def metric_rms_distance_px(
    measurement: Mapping[str, Any],
    term: ObjectiveTermSpec,
) -> float:
    target = term.source.target_px or {"x": 512.0, "y": 384.0}
    cx = float(measurement.get("centroid_x", target.get("x", 512.0)))
    cy = float(measurement.get("centroid_y", target.get("y", 384.0)))
    tx = float(target.get("x", 512.0))
    ty = float(target.get("y", 384.0))
    return math.sqrt((cx - tx) ** 2 + (cy - ty) ** 2)


__all__ = ["metric_rms_distance_px"]
