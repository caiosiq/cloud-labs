"""Tunable plugin: ``nominal_motor_positions`` (per-motor setpoints)."""
from __future__ import annotations

from typing import Any

from lab_model.domain.component import tunables_bucket
from lab_model.primitives.ids import PrimitiveId

from .registry import register_tunable


@register_tunable(
    field_id="nominal_motor_positions",
    widget="JsonInspector",
    write_primitive=PrimitiveId.SET_MOTOR_SETPOINT,
)
async def apply(
    bridge: Any, tag_id: str, motor_id: int, angle_deg: float
) -> None:
    """Core SET_MOTOR_SETPOINT semantics (called by bridge and macro)."""
    if not bridge._motor_catalog_ok(tag_id, motor_id):
        print(
            f"{bridge.log_prefix} set_motor_setpoint: invalid tag or "
            f"motor_id for {tag_id} m{motor_id}"
        )
        return

    from datetime import datetime

    from lab_model import motor_rotation_store as motor_rot

    angle = float(angle_deg)
    with bridge._state_lock:
        entry = (bridge.current_state.get("components") or {}).get(tag_id)
        if isinstance(entry, dict):
            tun = tunables_bucket(entry)
            nmp = tun.setdefault("nominal_motor_positions", {})
            if isinstance(nmp, dict):
                nmp[str(int(motor_id))] = angle
            bridge.current_state["last_updated"] = datetime.now().isoformat()
    bridge._persist_state()

    cur = motor_rot.get_angle(tag_id, motor_id)
    delta = angle - cur
    if abs(delta) < 1e-9:
        print(
            f"{bridge.log_prefix} Motor {motor_id} on {tag_id}: "
            f"setpoint {angle:g}° (intent only, already at θ)."
        )
        return
    await bridge.move_motor(tag_id, motor_id, delta)
