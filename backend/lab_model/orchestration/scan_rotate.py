"""SCAN_ROTATE_IN_PLACE primitive orchestration."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from lab_model.domain.holding import (
    DEFAULT_HOVER_Z_MM,
    SYSTEM_STATUS_BUSY,
    SYSTEM_STATUS_HOLDING,
    SYSTEM_STATUS_IDLE,
    get_holding,
    is_holding,
)
from lab_model.state.commits import commit_scan_rotation
from lab_model.state.state_machine import (
    refuse_if_holding_other_tag,
    refuse_if_not_in_state,
    refuse_if_not_on_breadboard,
    refuse_if_teleop_active,
)

from .protocol import ScanRotateHost


async def run_scan_rotate_in_place(
    host: ScanRotateHost, target_id: str, params: Dict[str, Any]
) -> None:
    with host._state_lock:
        snapshot = host.current_state
        comp_snapshot = (snapshot.get("components") or {}).get(target_id)
        holding_now = is_holding(snapshot)
        status_at_entry = snapshot.get("system_status") or SYSTEM_STATUS_IDLE

    if holding_now:
        same_tag = refuse_if_holding_other_tag(
            snapshot, target_id, primitive_name="scan_rotate_in_place"
        )
        if same_tag:
            print(
                f"{host.log_prefix} Refusing scan_rotate_in_place: "
                f"{same_tag.reason}"
            )
            return
        mode = "held"
    else:
        if status_at_entry != SYSTEM_STATUS_IDLE:
            print(
                f"{host.log_prefix} Refusing scan_rotate_in_place: "
                f"system_status={status_at_entry!r}, need IDLE or HOLDING."
            )
            return
        in_state = refuse_if_not_in_state(
            snapshot, target_id, primitive_name="scan_rotate_in_place"
        )
        if in_state:
            print(
                f"{host.log_prefix} Refusing scan_rotate_in_place: "
                f"{in_state.reason}"
            )
            return
        on_table = refuse_if_not_on_breadboard(
            snapshot, target_id, primitive_name="scan_rotate_in_place"
        )
        if on_table:
            print(
                f"{host.log_prefix} Refusing scan_rotate_in_place: "
                f"{on_table.reason}"
            )
            return
        mode = "placed"

    teleop_busy = refuse_if_teleop_active(
        snapshot, target_id, primitive_name="scan_rotate_in_place"
    )
    if teleop_busy:
        print(
            f"{host.log_prefix} Refusing scan_rotate_in_place: "
            f"{teleop_busy.reason}"
        )
        return

    params = params or {}
    try:
        theta_min = float(params.get("theta_min", 0.0))
        theta_max = float(params.get("theta_max", 0.0))
        speed = float(params.get("speed_deg_per_s", 1.0))
    except (TypeError, ValueError):
        print(f"{host.log_prefix} scan_rotate_in_place: non-numeric params.")
        return
    if speed <= 0:
        print(f"{host.log_prefix} scan_rotate_in_place: speed must be > 0.")
        return
    axis = str(params.get("axis", "z"))

    if mode == "held":
        held = get_holding(snapshot)
        base = dict(held.get("nominal_pose") or {})
        base_x = float(base.get("x", 0.0))
        base_y = float(base.get("y", 0.0))
        base_z: Optional[float] = float(base.get("z", DEFAULT_HOVER_Z_MM))
    else:
        cur_pose = ((comp_snapshot or {}).get("measurables") or {}).get("pose") or {}
        base_x = float(cur_pose.get("x", 0.0))
        base_y = float(cur_pose.get("y", 0.0))
        base_z = None

    print(
        f"{host.log_prefix} ScanRotate mode={mode} {target_id}: "
        f"{theta_min} deg -> {theta_max} deg @ {speed} deg/s axis={axis}"
    )

    def on_rotation_update(rotation: float) -> None:
        with host._state_lock:
            commit_scan_rotation(
                host.current_state,
                target_id,
                mode=mode,
                x=base_x,
                y=base_y,
                rotation=float(rotation),
                z=base_z,
            )
            host.current_state["last_updated"] = datetime.now().isoformat()
        host._persist_state()

    host._null_measurables_for_targets([target_id], persist=False)
    host._set_status(SYSTEM_STATUS_BUSY)
    final_status = SYSTEM_STATUS_HOLDING if mode == "held" else SYSTEM_STATUS_IDLE
    try:
        await host._primitive_scan_rotate_in_place(
            target_id=target_id,
            mode=mode,
            theta_min=theta_min,
            theta_max=theta_max,
            speed=speed,
            axis=axis,
            base_x=base_x,
            base_y=base_y,
            base_z=base_z,
            params=params,
            on_rotation_update=on_rotation_update,
        )
    except Exception:
        host._set_status(final_status)
        raise

    on_rotation_update(theta_max)
    host._set_status(final_status)
    print(
        f"{host.log_prefix} ScanRotate ({mode}) done {target_id}: "
        f"final rot={theta_max:.2f} deg -- {final_status}"
    )
