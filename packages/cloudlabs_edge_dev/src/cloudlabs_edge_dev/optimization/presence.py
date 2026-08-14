"""Beam-presence latch vs first-eval peak (OPTIMIZE side-band gate).

Centroid kernels that self-normalize to the current frame invent a CoM from
noise when the beam leaves the FOV. Latch ``peak_ref`` on the first finite
peak feature, then mark later measurements absent when
``peak < min_peak_ratio * peak_ref`` so metrics refuse / penalize.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Mapping, MutableMapping, Optional, Sequence

from .spec import ObjectiveTerm

DEFAULT_MIN_PEAK_RATIO = 0.5
DEFAULT_PEAK_FEATURE_INDEX = 2
_RMS_METRICS = frozenset({"rms_distance", "rms_distance_px"})
_PRESENCE_METRICS = frozenset({"beam_presence"}) | _RMS_METRICS


def _peak_index(term: ObjectiveTerm) -> int:
    raw = term.params.get("peak_feature_index", DEFAULT_PEAK_FEATURE_INDEX)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return DEFAULT_PEAK_FEATURE_INDEX


def _min_ratio(term: ObjectiveTerm) -> float:
    raw = term.params.get("min_peak_ratio", DEFAULT_MIN_PEAK_RATIO)
    try:
        r = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_MIN_PEAK_RATIO
    if not math.isfinite(r) or r <= 0.0:
        return DEFAULT_MIN_PEAK_RATIO
    return r


def _should_gate(term: ObjectiveTerm, feats: Sequence[float], peak_idx: int) -> bool:
    if term.params.get("latch_peak_ref") is False:
        return False
    if term.params.get("latch_peak_ref") is True:
        return peak_idx < len(feats)
    if term.metric in _PRESENCE_METRICS and peak_idx < len(feats):
        # Auto-enable when the kernel already emits a peak channel.
        return True
    return False


def apply_peak_presence_gate(
    measurements: MutableMapping[str, Dict[str, Any]],
    terms: Sequence[ObjectiveTerm],
    peak_refs: Dict[str, float],
) -> Dict[str, Any]:
    """Latch / gate per term; mutate ``measurements`` and ``term.params``.

    Returns a small debug dict (peak_ref, peak, presence_ok) keyed by term id.
    """
    debug: Dict[str, Any] = {}
    for term in terms:
        row = measurements.get(term.id)
        if not isinstance(row, Mapping):
            continue
        feats_raw = row.get("features")
        if not isinstance(feats_raw, (list, tuple)) or not feats_raw:
            continue
        feats = [float(x) for x in feats_raw]
        peak_idx = _peak_index(term)
        if not _should_gate(term, feats, peak_idx):
            continue
        peak = float(feats[peak_idx])
        if not math.isfinite(peak):
            continue

        # Explicit peak_ref in params wins (operator override); else latch.
        explicit = term.params.get("peak_ref")
        if explicit is not None and term.id not in peak_refs:
            try:
                peak_refs[term.id] = float(explicit)
            except (TypeError, ValueError):
                pass
        if term.id not in peak_refs:
            if peak > 0.0:
                peak_refs[term.id] = peak
            else:
                continue

        ref = float(peak_refs[term.id])
        term.params["peak_ref"] = ref
        ratio = _min_ratio(term)
        term.params.setdefault("min_peak_ratio", ratio)
        term.params.setdefault("peak_feature_index", peak_idx)
        present = peak >= (ratio * ref)
        # Mutate a copy so callers that hold the list see the gate flag.
        mutable = dict(row)
        mutable["peak"] = peak
        mutable["peak_ref"] = ref
        mutable["presence_ok"] = present
        if not present:
            # Invalidate centroid channels so rms_distance cannot score noise.
            # Keep peak metadata for beam_presence / debug.
            mutable["features"] = []
            mutable["presence_failed"] = True
        measurements[term.id] = mutable
        debug[term.id] = {
            "peak": peak,
            "peak_ref": ref,
            "min_peak_ratio": ratio,
            "presence_ok": present,
        }
    return debug


__all__ = [
    "DEFAULT_MIN_PEAK_RATIO",
    "DEFAULT_PEAK_FEATURE_INDEX",
    "apply_peak_presence_gate",
]
