"""Measurable plugin: ``motor_rotations`` (motorized components)."""
from __future__ import annotations

from typing import Any, Dict, Optional

from lab_model import motor_rotation_store as motor_rot

from .registry import register_measurable


@register_measurable(field_id="motor_rotations", widget="MotorRotationsReadout")
async def observe(
    bridge: Any,
    tag_id: str,
    catalog_meta: Dict[str, Any],
) -> Optional[Dict[str, float]]:
    motor_ids = (catalog_meta or {}).get("motor_ids") or []
    if not motor_ids:
        return None
    mr = motor_rot.get_rotations_for_motor_ids(tag_id, list(motor_ids))
    return {str(k): float(v) for k, v in mr.items()}
