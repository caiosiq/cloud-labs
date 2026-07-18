"""Measurable plugin: ``output_power_readback_mw`` (laser sources)."""
from __future__ import annotations

from typing import Any, Dict, Optional

from .registry import register_measurable


@register_measurable(
    field_id="output_power_readback_mw",
    widget="NumberBadge",
    dtype="float64",
    domain="scalar",
    units={"value": "mW"},
    shape=(),
    layout="scalar",
)
async def observe(
    bridge: Any,
    tag_id: str,
    catalog_meta: Dict[str, Any],  # noqa: ARG001
) -> Optional[float]:
    hook = getattr(bridge, "_hardware_read_laser_output_power_mw", None)
    if callable(hook):
        val = hook(tag_id)
        if val is not None:
            return float(val)

    with bridge._state_lock:
        entry = (bridge.current_state.get("components") or {}).get(tag_id)
        if not isinstance(entry, dict):
            return None
        tun = (entry.get("statecontrol") or {}).get("tunables") or {}
        if isinstance(tun, dict) and isinstance(tun.get("output_power_mw"), (int, float)):
            return float(tun["output_power_mw"])
    return None
