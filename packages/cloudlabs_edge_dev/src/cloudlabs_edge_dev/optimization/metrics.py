"""Objective metrics for the edge optimization engine.

Behaviour (Phase 1 policy — not a silent port of coordinator defaults):

- Empty / missing features → :class:`MeasurementInvalid` (never score as perfect).
- Missing scalar → :class:`MeasurementInvalid` (never substitute 0.0).
- Session converts invalid measurements into a finite penalty on the **normalized**
  loss scale (~0–2), not raw pixels.
- Spatial metrics (``rms_distance``, scaled ``minimize_value``) divide by a
  length scale (FOV diagonal / ``rms_scale_px`` / ``normalize.max``) and soft-cap.
"""
from __future__ import annotations

import math
from typing import Any, Callable, Dict, Mapping, Optional, Sequence, TypeVar

from .spec import ObjectiveDecl, ObjectiveTerm

F = TypeVar("F", bound=Callable[..., float])

MetricFn = Callable[[Mapping[str, Any], ObjectiveTerm], float]
METRIC_REGISTRY: Dict[str, MetricFn] = {}

# Invalid / refused measurement penalty on the normalized loss scale so terms
# remain mixable (align + width + power) without 1e3-vs-pixel weight fights.
DEFAULT_INVALID_PENALTY = 2.0
DEFAULT_LOSS_CAP = 2.0
DEFAULT_ABSENT_PENALTY = 2.0


class MeasurementInvalid(Exception):
    """A required measurement feature/scalar is missing or non-finite."""

    def __init__(self, message: str, *, term_id: str = "") -> None:
        super().__init__(message)
        self.term_id = term_id
        self.message = message


def register_metric(*, metric_id: str) -> Callable[[F], F]:
    def deco(fn: F) -> F:
        if metric_id in METRIC_REGISTRY:
            raise ValueError(f"Duplicate metric registration: {metric_id!r}")
        METRIC_REGISTRY[metric_id] = fn
        return fn

    return deco


def get_metric(metric_id: str) -> Optional[MetricFn]:
    return METRIC_REGISTRY.get(metric_id)


def _require_finite(value: Any, *, term_id: str, what: str) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise MeasurementInvalid(f"{what} is not numeric: {value!r}", term_id=term_id) from exc
    if not math.isfinite(out):
        raise MeasurementInvalid(f"{what} is not finite: {out!r}", term_id=term_id)
    return out


def _features(measurement: Mapping[str, Any]) -> Sequence[float]:
    raw = measurement.get("features")
    if isinstance(raw, (list, tuple)):
        return [float(x) for x in raw]
    scalar = measurement.get("scalar")
    if scalar is not None:
        return [float(scalar)]
    return []


def _param(term: ObjectiveTerm, key: str, default: Any = None) -> Any:
    if key in term.params:
        return term.params[key]
    return default


def _loss_cap(term: ObjectiveTerm) -> float:
    raw = _param(term, "loss_cap", DEFAULT_LOSS_CAP)
    try:
        cap = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_LOSS_CAP
    if not math.isfinite(cap) or cap <= 0.0:
        return DEFAULT_LOSS_CAP
    return cap


def _frame_hw(measurement: Mapping[str, Any], term: ObjectiveTerm) -> Optional[tuple[float, float]]:
    for raw in (
        measurement.get("frame_hw"),
        _param(term, "frame_hw"),
        measurement.get("shape"),
    ):
        if isinstance(raw, (list, tuple)) and len(raw) >= 2:
            try:
                h, w = float(raw[0]), float(raw[1])
            except (TypeError, ValueError):
                continue
            if h > 0 and w > 0:
                return h, w
    return None


def resolve_length_scale_px(
    measurement: Mapping[str, Any],
    term: ObjectiveTerm,
    *,
    prefer_keys: Sequence[str] = ("rms_scale_px", "value_scale_px"),
) -> float:
    """Characteristic length (px) for normalizing spatial losses.

    Order: explicit scale keys → ``normalize.max`` → FOV diagonal from
    ``frame_hw`` → conservative fallback ``1.0`` (avoids divide-by-zero; caller
    should prefer injecting FOV from capture).
    """
    for key in prefer_keys:
        raw = _param(term, key)
        if raw is None and key in measurement:
            raw = measurement.get(key)
        if raw is None:
            continue
        try:
            scale = float(raw)
        except (TypeError, ValueError):
            continue
        if math.isfinite(scale) and scale > 1e-9:
            return scale

    norm = _param(term, "normalize")
    if isinstance(norm, Mapping) and "max" in norm:
        try:
            scale = float(norm["max"]) - float(norm.get("min", 0.0))
        except (TypeError, ValueError):
            scale = 0.0
        if math.isfinite(scale) and scale > 1e-9:
            return scale

    hw = _frame_hw(measurement, term)
    if hw is not None:
        h, w = hw
        diag = math.hypot(w, h)
        if diag > 1e-9:
            return diag

    return 1.0


def normalize_spatial_loss(
    raw_px: float,
    measurement: Mapping[str, Any],
    term: ObjectiveTerm,
    *,
    prefer_keys: Sequence[str] = ("rms_scale_px", "value_scale_px"),
) -> float:
    """``min(raw_px / scale, loss_cap)`` — typically ~0–1, soft-capped near 2."""
    scale = resolve_length_scale_px(measurement, term, prefer_keys=prefer_keys)
    capped = float(raw_px) / scale
    return min(max(0.0, capped), _loss_cap(term))


@register_metric(metric_id="one_minus_normalized")
def metric_one_minus_normalized(
    measurement: Mapping[str, Any],
    term: ObjectiveTerm,
) -> float:
    """``1 - clip((val-min)/(max-min))`` for known physical bounds (e.g. mW meter).

    Do **not** use a made-up max like ``1e6`` for camera flux — prefer
    ``ratio_to_ref`` which latches the first-eval reading as the scale.
    """
    norm = _param(term, "normalize")
    if isinstance(norm, Mapping):
        lo = float(norm.get("min", 0.0))
        hi = float(norm.get("max", 1.0))
    else:
        lo, hi = 0.0, 1.0
    span = hi - lo
    if abs(span) < 1e-15:
        return 0.0
    raw = measurement.get("scalar")
    if raw is None:
        raw = measurement.get("power")
    if raw is None:
        feats = _features(measurement)
        idx = _param(term, "feature_index", 0)
        if isinstance(idx, (list, tuple)):
            idx = idx[0] if idx else 0
        try:
            i = int(idx)
        except (TypeError, ValueError):
            i = 0
        if feats and 0 <= i < len(feats):
            raw = feats[i]
    if raw is None:
        raise MeasurementInvalid("missing scalar for one_minus_normalized", term_id=term.id)
    val = _require_finite(raw, term_id=term.id, what="scalar")
    normalized = max(0.0, min(1.0, (val - lo) / span))
    return 1.0 - normalized


@register_metric(metric_id="ratio_to_ref")
def metric_ratio_to_ref(
    measurement: Mapping[str, Any],
    term: ObjectiveTerm,
) -> float:
    """Maximize a feature/scalar via ``ref / max(value, eps)`` (no hard cap).

    ``value_ref`` is latched on the first finite positive reading (session
    ``apply_value_ref_latch``). Loss starts ≈ 1 at that reading, falls toward 0
    as the signal grows, and rises above 1 if it gets worse. Invalid / missing
    readings still use the session invalid penalty (~2) — that is not a maximize
    soft-cap.
    """
    return _ratio_metric(measurement, term, invert=True)


@register_metric(metric_id="ratio_from_ref")
def metric_ratio_from_ref(
    measurement: Mapping[str, Any],
    term: ObjectiveTerm,
) -> float:
    """Minimize a feature/scalar via ``value / ref`` (inverse of ``ratio_to_ref``).

    Latches first-eval ``value_ref`` the same way. Loss starts ≈ 1, falls toward
    0 as the signal shrinks (darker / less flux).
    """
    return _ratio_metric(measurement, term, invert=False)


def _ratio_metric(
    measurement: Mapping[str, Any],
    term: ObjectiveTerm,
    *,
    invert: bool,
) -> float:
    feats = _features(measurement)
    idx = _param(term, "feature_index", 0)
    if isinstance(idx, (list, tuple)):
        idx = idx[0] if idx else 0
    try:
        i = int(idx)
    except (TypeError, ValueError):
        i = 0
    raw = None
    if feats and 0 <= i < len(feats):
        raw = feats[i]
    if raw is None:
        raw = measurement.get("value")
    if raw is None:
        raw = measurement.get("scalar")
    if raw is None:
        raw = measurement.get("power")
    if raw is None:
        raise MeasurementInvalid("missing value for ratio metric", term_id=term.id)
    val = _require_finite(raw, term_id=term.id, what="value")
    ref_raw = _param(term, "value_ref", None)
    if ref_raw is None:
        ref_raw = measurement.get("value_ref")
    if ref_raw is None:
        raise MeasurementInvalid("missing value_ref for ratio metric", term_id=term.id)
    ref = _require_finite(ref_raw, term_id=term.id, what="value_ref")
    if ref <= 0.0:
        raise MeasurementInvalid("value_ref must be > 0", term_id=term.id)
    if val <= 0.0:
        raise MeasurementInvalid("non-positive value for ratio metric", term_id=term.id)
    if invert:
        return float(ref / val)
    return float(val / ref)


@register_metric(metric_id="squared_error")
def metric_squared_error(
    measurement: Mapping[str, Any],
    term: ObjectiveTerm,
) -> float:
    m0 = _param(term, "target_scalar", 0.0)
    m0 = _require_finite(m0, term_id=term.id, what="target_scalar")
    raw = measurement.get("scalar")
    feats = _features(measurement)
    idx = _param(term, "feature_index", None)
    if feats:
        try:
            i = int(idx if idx is not None else 0)
        except (TypeError, ValueError):
            i = 0
        if 0 <= i < len(feats):
            raw = feats[i]
    if raw is None:
        raw = measurement.get("power")
    if raw is None:
        raise MeasurementInvalid("missing scalar for squared_error", term_id=term.id)
    m = _require_finite(raw, term_id=term.id, what="scalar")
    err = m - m0
    return float(err * err)


@register_metric(metric_id="minimize_value")
def metric_minimize_value(
    measurement: Mapping[str, Any],
    term: ObjectiveTerm,
) -> float:
    """Minimize a feature; optionally normalize by a length scale (FOV / params)."""
    feats = _features(measurement)
    if not feats:
        raise MeasurementInvalid("empty features for minimize_value", term_id=term.id)
    idx = _param(term, "feature_index", 0)
    if isinstance(idx, (list, tuple)):
        idx = idx[0] if idx else 0
    i = int(idx)
    if i < 0 or i >= len(feats):
        raise MeasurementInvalid(
            f"feature_index {i} out of range for len={len(feats)}",
            term_id=term.id,
        )
    value = _require_finite(feats[i], term_id=term.id, what="feature")
    if _param(term, "use_abs", False):
        value = abs(value)
    # Normalize when an explicit scale / normalize.max / FOV is available, or
    # when normalize_by_fov is requested (gaussian width preset).
    want_norm = bool(
        _param(term, "normalize_by_fov")
        or _param(term, "value_scale_px") is not None
        or _param(term, "rms_scale_px") is not None
        or isinstance(_param(term, "normalize"), Mapping)
        or _frame_hw(measurement, term) is not None
    )
    if want_norm:
        return normalize_spatial_loss(
            value,
            measurement,
            term,
            prefer_keys=("value_scale_px", "rms_scale_px"),
        )
    return value


@register_metric(metric_id="rms_distance")
def metric_rms_distance(
    measurement: Mapping[str, Any],
    term: ObjectiveTerm,
) -> float:
    """Normalized RMS distance to target (~0–1, soft-capped near ``loss_cap``)."""
    if measurement.get("presence_ok") is False:
        # Same scale as a worst-case FOV miss — not a 1e3 invalid spike.
        return _loss_cap(term)

    feats = _features(measurement)
    idx = _param(term, "feature_index", [0, 1])
    if isinstance(idx, (list, tuple)) and len(idx) >= 2:
        i0, i1 = int(idx[0]), int(idx[1])
    else:
        i0, i1 = 0, 1
    if not feats or max(i0, i1) >= len(feats):
        if "centroid_x" in measurement and "centroid_y" in measurement:
            cx = _require_finite(measurement["centroid_x"], term_id=term.id, what="centroid_x")
            cy = _require_finite(measurement["centroid_y"], term_id=term.id, what="centroid_y")
        else:
            raise MeasurementInvalid(
                "empty or incomplete features for rms_distance",
                term_id=term.id,
            )
    else:
        cx = _require_finite(feats[i0], term_id=term.id, what="feature[0]")
        cy = _require_finite(feats[i1], term_id=term.id, what="feature[1]")

    target = _param(term, "target")
    if target is None:
        target = _param(term, "target_px")
    if isinstance(target, Mapping):
        tx = float(target.get("x", target.get("0", 0.0)))
        ty = float(target.get("y", target.get("1", 0.0)))
    elif isinstance(target, (list, tuple)) and len(target) >= 2:
        tx, ty = float(target[0]), float(target[1])
    else:
        tx, ty = 0.0, 0.0
    rms_px = math.sqrt((cx - tx) ** 2 + (cy - ty) ** 2)
    return normalize_spatial_loss(rms_px, measurement, term)


@register_metric(metric_id="signed_axis_offset")
def metric_signed_axis_offset(
    measurement: Mapping[str, Any],
    term: ObjectiveTerm,
) -> float:
    """Maximize signed CoM displacement along one axis from an origin.

    Params:
      - ``origin_px`` / ``target_px``: ``{x, y}`` reference pixel
      - ``axis``: ``\"x\"`` or ``\"y\"``
      - ``direction``: ``+1`` or ``-1`` (positive / negative along that axis)

    Loss = ``-(direction * (c_axis - o_axis)) / scale`` so lower is farther in
    the requested direction (weight +1). Absent beam → ``loss_cap``.
    """
    if measurement.get("presence_ok") is False:
        return _loss_cap(term)

    feats = _features(measurement)
    idx = _param(term, "feature_index", [0, 1])
    if isinstance(idx, (list, tuple)) and len(idx) >= 2:
        i0, i1 = int(idx[0]), int(idx[1])
    else:
        i0, i1 = 0, 1
    if not feats or max(i0, i1) >= len(feats):
        raise MeasurementInvalid(
            "empty or incomplete features for signed_axis_offset",
            term_id=term.id,
        )
    cx = _require_finite(feats[i0], term_id=term.id, what="feature[0]")
    cy = _require_finite(feats[i1], term_id=term.id, what="feature[1]")

    origin = _param(term, "origin_px")
    if origin is None:
        origin = _param(term, "target_px")
    if origin is None:
        origin = _param(term, "target")
    if isinstance(origin, Mapping):
        ox = float(origin.get("x", origin.get("0", 0.0)))
        oy = float(origin.get("y", origin.get("1", 0.0)))
    elif isinstance(origin, (list, tuple)) and len(origin) >= 2:
        ox, oy = float(origin[0]), float(origin[1])
    else:
        raise MeasurementInvalid(
            "missing origin_px for signed_axis_offset",
            term_id=term.id,
        )

    axis_raw = str(_param(term, "axis", "x") or "x").strip().lower()
    if axis_raw in ("y", "1", "cy"):
        coord, origin_v = cy, oy
    else:
        coord, origin_v = cx, ox

    direction_raw = _param(term, "direction", 1)
    try:
        direction = float(direction_raw)
    except (TypeError, ValueError):
        direction = 1.0
    if direction >= 0:
        direction = 1.0
    else:
        direction = -1.0

    signed_px = direction * (coord - origin_v)
    scale = resolve_length_scale_px(
        measurement,
        term,
        prefer_keys=("value_scale_px", "rms_scale_px"),
    )
    # Lower loss = farther in the desired direction; allow negative (no floor).
    raw = -float(signed_px) / scale
    # Soft-cap the "good" side so a huge FOV jump does not dominate mixed terms.
    return max(raw, -_loss_cap(term))


@register_metric(metric_id="beam_presence")
def metric_beam_presence(
    measurement: Mapping[str, Any],
    term: ObjectiveTerm,
) -> float:
    """0 when peak ≥ ratio·peak_ref; else ``absent_penalty`` (default 2.0)."""
    absent = float(_param(term, "absent_penalty", DEFAULT_ABSENT_PENALTY))
    if measurement.get("presence_ok") is False:
        return absent
    feats = _features(measurement)
    idx = _param(term, "peak_feature_index", 2)
    try:
        i = int(idx)
    except (TypeError, ValueError):
        i = 2
    peak: Optional[float] = None
    if feats and 0 <= i < len(feats):
        peak = _require_finite(feats[i], term_id=term.id, what="peak")
    elif measurement.get("peak") is not None:
        peak = _require_finite(measurement["peak"], term_id=term.id, what="peak")
    if peak is None:
        raise MeasurementInvalid("missing peak for beam_presence", term_id=term.id)
    peak_ref = _param(term, "peak_ref")
    if peak_ref is None:
        return 0.0
    ref = _require_finite(peak_ref, term_id=term.id, what="peak_ref")
    ratio = float(_param(term, "min_peak_ratio", 0.5) or 0.5)
    if peak >= ratio * ref:
        return 0.0
    return absent


# Alias used by some coordinator payloads.
METRIC_REGISTRY["rms_distance_px"] = metric_rms_distance


def invalid_penalty(term: ObjectiveTerm) -> float:
    """Finite penalty for a refused measurement on the normalized loss scale."""
    w = abs(float(term.weight)) if term.weight else 1.0
    # Prefer per-term loss_cap when set so invalid matches absent/worst-case.
    base = float(_param(term, "loss_cap", DEFAULT_INVALID_PENALTY) or DEFAULT_INVALID_PENALTY)
    return base * max(w, 1e-6)


def evaluate_weighted_sum(
    objective: ObjectiveDecl,
    measurements: Mapping[str, Mapping[str, Any]],
    *,
    on_invalid: str = "penalty",
) -> tuple[float, Dict[str, float]]:
    """
    Compute scalar loss and per-term contributions.

    ``on_invalid``:
      - ``"penalty"`` — substitute :func:`invalid_penalty` (session default)
      - ``"raise"`` — propagate :class:`MeasurementInvalid`
    """
    terms_out: Dict[str, float] = {}
    total = 0.0
    sign = 1.0 if objective.minimize else -1.0
    for term in objective.terms:
        metric_fn = get_metric(term.metric)
        if metric_fn is None:
            raise ValueError(f"unsupported objective metric: {term.metric!r}")
        try:
            raw = metric_fn(measurements.get(term.id, {}), term)
            if not math.isfinite(raw):
                raise MeasurementInvalid(
                    f"metric returned non-finite {raw!r}",
                    term_id=term.id,
                )
        except MeasurementInvalid:
            if on_invalid == "raise":
                raise
            raw = invalid_penalty(term) / max(abs(float(term.weight)), 1e-6)
        weighted = float(term.weight) * float(raw)
        terms_out[term.id] = weighted
        total += weighted
    return sign * total, terms_out


__all__ = [
    "DEFAULT_ABSENT_PENALTY",
    "DEFAULT_INVALID_PENALTY",
    "DEFAULT_LOSS_CAP",
    "METRIC_REGISTRY",
    "MeasurementInvalid",
    "MetricFn",
    "evaluate_weighted_sum",
    "get_metric",
    "invalid_penalty",
    "metric_beam_presence",
    "metric_minimize_value",
    "metric_one_minus_normalized",
    "metric_ratio_from_ref",
    "metric_ratio_to_ref",
    "metric_rms_distance",
    "metric_signed_axis_offset",
    "metric_squared_error",
    "normalize_spatial_loss",
    "register_metric",
    "resolve_length_scale_px",
]
