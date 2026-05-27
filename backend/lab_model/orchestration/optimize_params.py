"""Normalize OPTIMIZE command params to the UI/catalog contract."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from lab_model.catalog.schema import catalog_has_motors, catalog_optimize_strategies

DEFAULT_AXIS = "x"
DEFAULT_TOLERANCE_RATIO = 0.05
DEFAULT_VIDEO_EXPOSURE = 0.2
MIN_VIDEO_EXPOSURE = 0.001
MAX_VIDEO_EXPOSURE = 30.0
MIN_TOLERANCE_RATIO = 0.001
MAX_TOLERANCE_RATIO = 0.5


def allowed_loss_metrics(
    catalog_row: Optional[Dict[str, Any]], strategy_name: str
) -> List[str]:
    """Loss metrics permitted for ``strategy_name`` on this catalog row."""
    strat = (strategy_name or "").upper()
    strategies = catalog_optimize_strategies(catalog_row)
    row = strategies.get(strat) or {}
    metrics = row.get("loss_metrics")
    if isinstance(metrics, list) and metrics:
        return [str(m) for m in metrics]
    if strat == "NEWTON":
        return ["centroid_match"]
    if strat == "COBYLA":
        return ["reference_match"]
    return []


def _clamp_float(value: Any, default: float, lo: float, hi: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def normalize_optimize_params(
    catalog_row: Optional[Dict[str, Any]],
    target_id: str,
    strategy_name: str,
    params: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Merge UI params with catalog defaults (sensor, motors, loss, Newton knobs)."""
    out: Dict[str, Any] = dict(params or {})
    strat = (strategy_name or out.get("strategy") or "COBYLA").upper()
    out["strategy"] = strat

    sensor = out.get("sensor_component") or out.get("sensor_tag_id")
    if isinstance(sensor, str) and sensor.strip():
        out["sensor_component"] = sensor.strip()
    else:
        out.pop("sensor_component", None)

    if strat == "COBYLA" and catalog_row and not out.get("motor_ids"):
        mids = catalog_row.get("motor_ids")
        if isinstance(mids, list) and mids:
            out["motor_ids"] = list(mids)

    if strat == "COBYLA" and catalog_row and not catalog_has_motors(catalog_row):
        _ = target_id  # refusal happens upstream in optimize_policy

    raw_exp = out.get("video_exposure")
    if raw_exp is None:
        raw_exp = out.get("exposure")
    out["video_exposure"] = _clamp_float(
        raw_exp, DEFAULT_VIDEO_EXPOSURE, MIN_VIDEO_EXPOSURE, MAX_VIDEO_EXPOSURE
    )
    out.pop("exposure", None)

    if strat == "NEWTON":
        axis = str(out.get("axis") or DEFAULT_AXIS).lower().strip()
        out["axis"] = axis if axis in ("x", "y") else DEFAULT_AXIS
        out["tolerance_ratio"] = _clamp_float(
            out.get("tolerance_ratio"),
            DEFAULT_TOLERANCE_RATIO,
            MIN_TOLERANCE_RATIO,
            MAX_TOLERANCE_RATIO,
        )
    else:
        out.pop("axis", None)
        out.pop("tolerance_ratio", None)

    allowed = allowed_loss_metrics(catalog_row, strat)
    metric = out.get("loss_metric")
    if not metric:
        out["loss_metric"] = allowed[0] if allowed else (
            "centroid_match" if strat == "NEWTON" else "reference_match"
        )
    elif str(metric) not in allowed:
        out["_loss_metric_invalid"] = str(metric)
        out["_loss_metrics_allowed"] = allowed

    return out
