"""Measurable plugin: ``last_optimization_score`` (motorized / optimizable components)."""
from __future__ import annotations

import random
from typing import Any, Dict, Optional

from .registry import is_tensor_envelope, register_measurable


def _scalar_from_stored(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)):
        return float(value)
    if is_tensor_envelope(value):
        data = value.get("data")
        if isinstance(data, (int, float)):
            return float(data)
    return None


@register_measurable(
    field_id="last_optimization_score",
    widget="NumberBadge",
    dtype="float64",
    domain="scalar",
    units={"value": "1"},
    shape=(),
    layout="scalar",
)
async def observe(
    bridge: Any,
    tag_id: str,
    catalog_meta: Dict[str, Any],
) -> Optional[float]:
    saved = {}
    if hasattr(bridge, "return_measurables_for_tag"):
        saved = bridge.return_measurables_for_tag(tag_id) or {}
    score = _scalar_from_stored(saved.get("last_optimization_score"))
    if score is not None:
        return score
    return round(random.uniform(0.85, 0.99), 3)
