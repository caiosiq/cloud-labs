"""Motor MOVE_MOTOR orchestration."""
from __future__ import annotations

from lab_model.domain import motor_rotation_store as motor_rot
from lab_model.domain.holding import SYSTEM_STATUS_BUSY, SYSTEM_STATUS_IDLE
from lab_model.state.state_machine import refuse_if_stored, refuse_if_teleop_active

from .protocol import MotorHost


async def run_move_motor(
    host: MotorHost, target_id: str, motor_id: int, distance: float
) -> None:
    """Refuse → BUSY → hardware → advance motor_rotation_store → IDLE."""
    print(
        f"{host.log_prefix} Moving motor {motor_id} of {target_id} "
        f"by {distance} (RELATIVE)..."
    )
    with host._state_lock:
        snapshot = host.current_state

    refusal = refuse_if_stored(snapshot, target_id, primitive_name="move motor")
    if refusal:
        print(f"{host.log_prefix} Refusing motor move: {refusal.reason}")
        return
    refusal = refuse_if_teleop_active(
        snapshot, target_id, primitive_name="move motor"
    )
    if refusal:
        print(f"{host.log_prefix} Refusing motor move: {refusal.reason}")
        return

    if not host._motor_catalog_ok(target_id, motor_id):
        mids = (host._catalog_meta_for_tag(target_id) or {}).get("motor_ids") or []
        print(
            f"{host.log_prefix} Error: motor_id {motor_id} invalid for "
            f"{target_id} (motor_ids={mids})."
        )
        return

    host._null_measurables_for_targets([target_id], persist=False)
    host._set_status(SYSTEM_STATUS_BUSY)
    try:
        await host._primitive_move_motor(target_id, motor_id, float(distance))
        motor_rot.add_delta(target_id, motor_id, float(distance))
        from datetime import datetime

        from lab_model.state.motor_state import set_nominal_motor_angle

        new_angle = motor_rot.get_angle(target_id, motor_id)
        with host._state_lock:
            entry = (host.current_state.get("components") or {}).get(target_id)
            if isinstance(entry, dict):
                set_nominal_motor_angle(entry, motor_id, new_angle)
                host.current_state["last_updated"] = datetime.now().isoformat()
        host._persist_state()
        print(f"{host.log_prefix} Motor moved.")
    except Exception as e:
        print(f"{host.log_prefix} Motor move failed: {e}")
    finally:
        host._set_status(SYSTEM_STATUS_IDLE)
