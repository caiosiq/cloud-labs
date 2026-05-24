"""Measurable plugin: ``pose`` (table pose readout from state or nominal tunable)."""
from __future__ import annotations

from typing import Any, Dict, Optional

from .registry import register_measurable


@register_measurable(field_id="pose", widget="PoseReadout")
async def observe(
    bridge: Any,
    tag_id: str,
    catalog_meta: Dict[str, Any],
) -> Optional[Dict[str, float]]:
    saved = {}
    if hasattr(bridge, "return_measurables_for_tag"):
        saved = bridge.return_measurables_for_tag(tag_id) or {}
    pose = saved.get("pose")
    if isinstance(pose, dict) and pose:
        return {
            "x": float(pose.get("x", 0.0)),
            "y": float(pose.get("y", 0.0)),
            "rotation": float(pose.get("rotation", 0.0)),
        }
    tun = {}
    if hasattr(bridge, "return_tunables_for_tag"):
        tun = bridge.return_tunables_for_tag(tag_id) or {}
    np = (tun.get("nominal_pose") or {}) if isinstance(tun.get("nominal_pose"), dict) else {}
    return {
        "x": float(np.get("x", 0.0)),
        "y": float(np.get("y", 0.0)),
        "rotation": float(np.get("rotation", 0.0)),
    }
