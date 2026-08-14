"""Latch first-eval scalar/feature as ``value_ref`` for maximize-style metrics.

``ratio_to_ref`` loss is ``ref / max(value, eps)``: starts near 1 at the first
finite reading, falls toward 0 as the value grows, and rises above 1 if the
signal gets worse. No fixed engineer max (e.g. 1e6) and no hard cap at 2.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Mapping, MutableMapping, Optional, Sequence

from .spec import ObjectiveTerm

_RATIO_METRICS = frozenset({"ratio_to_ref", "ratio_from_ref"})


def _feature_index(term: ObjectiveTerm) -> Optional[int]:
    raw = term.params.get("feature_index", None)
    if raw is None:
        return 0 if term.metric in _RATIO_METRICS else None
    if isinstance(raw, (list, tuple)):
        raw = raw[0] if raw else 0
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def _extract_value(row: Mapping[str, Any], term: ObjectiveTerm) -> Optional[float]:
    idx = _feature_index(term)
    feats = row.get("features")
    if isinstance(feats, (list, tuple)) and idx is not None and 0 <= idx < len(feats):
        try:
            v = float(feats[idx])
        except (TypeError, ValueError):
            v = float("nan")
        if math.isfinite(v):
            return v
    for key in ("scalar", "power"):
        raw = row.get(key)
        if raw is None:
            continue
        try:
            v = float(raw)
        except (TypeError, ValueError):
            continue
        if math.isfinite(v):
            return v
    return None


def _should_latch(term: ObjectiveTerm) -> bool:
    if term.params.get("latch_value_ref") is False:
        return False
    if term.params.get("latch_value_ref") is True:
        return True
    if term.metric in _RATIO_METRICS:
        return True
    return False


def apply_value_ref_latch(
    measurements: MutableMapping[str, Dict[str, Any]],
    terms: Sequence[ObjectiveTerm],
    value_refs: Dict[str, float],
) -> Dict[str, Any]:
    """Latch first finite reading per term into ``value_refs`` / ``term.params``."""
    debug: Dict[str, Any] = {}
    for term in terms:
        if not _should_latch(term):
            continue
        row = measurements.get(term.id)
        if not isinstance(row, Mapping):
            continue
        value = _extract_value(row, term)
        if value is None:
            continue

        explicit = term.params.get("value_ref")
        if explicit is not None and term.id not in value_refs:
            try:
                er = float(explicit)
                if math.isfinite(er) and er > 0.0:
                    value_refs[term.id] = er
            except (TypeError, ValueError):
                pass
        if term.id not in value_refs:
            if value > 0.0:
                value_refs[term.id] = value
            else:
                continue

        ref = float(value_refs[term.id])
        term.params["value_ref"] = ref
        term.params.setdefault("latch_value_ref", True)
        mutable = dict(row)
        mutable["value_ref"] = ref
        mutable["value"] = value
        measurements[term.id] = mutable
        debug[term.id] = {
            "value": value,
            "value_ref": ref,
            "ratio": (ref / value) if value > 0 else None,
        }
    return debug


__all__ = ["apply_value_ref_latch"]
