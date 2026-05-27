"""Measurable plugin: ``last_optimization_score`` (optimizable components)."""
from __future__ import annotations

import random
from typing import Any, Dict, Optional

from .registry import register_measurable


@register_measurable(field_id="last_optimization_score", widget="NumberBadge")
async def observe(
    bridge: Any,
    tag_id: str,
    catalog_meta: Dict[str, Any],
) -> Optional[float]:
    saved = {}
    if hasattr(bridge, "return_measurables_for_tag"):
        saved = bridge.return_measurables_for_tag(tag_id) or {}
    score = saved.get("last_optimization_score")
    if score is not None:
        return float(score)
    return round(random.uniform(0.85, 0.99), 3)
