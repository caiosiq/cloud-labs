"""In-air primitive orchestration: PICK, HOVER, PLACE_FROM_HOVER."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from lab_model.domain.holding import (
    DEFAULT_HOVER_Z_MM,
    SYSTEM_STATUS_BUSY,
    SYSTEM_STATUS_HOLDING,
    SYSTEM_STATUS_IDLE,
    confirm_holding_tag as commit_confirm_holding_tag,
    is_holding,
)
from lab_model.state.commits import commit_hover, commit_pick, commit_place_from_hover
from lab_model.state.snapshot import LabPose
from lab_model.state.state_machine import (
    refuse_if_holding,
    refuse_if_holding_other_tag,
    refuse_if_in_storage_quadrant,
    refuse_if_not_holding,
    refuse_if_not_in_state,
    refuse_if_stored,
    refuse_if_teleop_active,
    refuse_if_z_lab_out_of_bounds,
)

from .protocol import InAirHost


async def run_pick_component(
    host: InAirHost, target_id: str, params: Dict[str, Any]
) -> None:
    print(f"{host.log_prefix} Pick {target_id}...")
    with host._state_lock:
        snapshot = host.current_state

    for refusal in (
        refuse_if_holding(snapshot, primitive_name="pick"),
        refuse_if_stored(snapshot, target_id, primitive_name="pick"),
        refuse_if_not_in_state(snapshot, target_id, primitive_name="pick"),
        refuse_if_teleop_active(snapshot, target_id, primitive_name="pick"),
    ):
        if refusal:
            print(f"{host.log_prefix} Refusing pick: {refusal.reason}")
            return

    with host._state_lock:
        entry = (host.current_state.get("components") or {}).get(target_id) or {}
    pose_dict = ((entry.get("measurables") or {}).get("pose") or {})
    commanded = LabPose(
        x=float(pose_dict.get("x", 0.0)),
        y=float(pose_dict.get("y", 0.0)),
        z=0.0,
        rotation=float(pose_dict.get("rotation", 0.0)),
    )

    host._null_measurables_for_targets([target_id], persist=False)
    host._set_status(SYSTEM_STATUS_BUSY)
    try:
        settled_z = await host._primitive_pick_component(target_id, commanded, params)
    except Exception:
        host._set_status(SYSTEM_STATUS_IDLE)
        raise

    settled_z_f = float(settled_z)
    with host._state_lock:
        commit_pick(
            host.current_state,
            target_id,
            x=commanded.x,
            y=commanded.y,
            rotation=commanded.rotation,
            settled_z=settled_z_f,
        )
        host.current_state["last_updated"] = datetime.now().isoformat()
    host._set_status(SYSTEM_STATUS_HOLDING)
    print(
        f"{host.log_prefix} Picked {target_id} at "
        f"({commanded.x:.1f},{commanded.y:.1f},rot={commanded.rotation:.1f}) "
        f"-> HOLDING @ z_lab={settled_z_f:.1f} mm"
    )


async def run_hover_component(
    host: InAirHost, target_id: str, target_pose: Dict[str, float]
) -> None:
    print(f"{host.log_prefix} Hover {target_id} -> {target_pose}")
    with host._state_lock:
        snapshot = host.current_state

    for refusal in (
        refuse_if_not_holding(snapshot, primitive_name="hover"),
        refuse_if_holding_other_tag(snapshot, target_id, primitive_name="hover"),
        refuse_if_teleop_active(snapshot, target_id, primitive_name="hover"),
    ):
        if refusal:
            print(f"{host.log_prefix} Refusing hover: {refusal.reason}")
            return

    target_pose = target_pose or {}
    tx = float(target_pose.get("target_x", target_pose.get("x", 0.0)))
    ty = float(target_pose.get("target_y", target_pose.get("y", 0.0)))
    trot = float(target_pose.get("rotation", 0.0))
    tz = float(target_pose.get("z", DEFAULT_HOVER_Z_MM))
    try:
        speed = int(target_pose.get("speed", 100))
    except (TypeError, ValueError):
        speed = 100

    bound = refuse_if_z_lab_out_of_bounds(
        tz,
        max_safe_z_lab_mm=host.max_safe_hover_z_lab_mm,
        primitive_name="hover",
    )
    if bound:
        print(f"{host.log_prefix} Refusing hover: {bound.reason}")
        return

    commanded = LabPose(x=tx, y=ty, z=tz, rotation=trot)

    host._null_measurables_for_targets([target_id], persist=False)
    host._set_status(SYSTEM_STATUS_BUSY)
    try:
        actual = await host._primitive_hover_component(target_id, commanded, speed)
    except Exception:
        host._set_status(SYSTEM_STATUS_HOLDING)
        raise

    with host._state_lock:
        commit_hover(
            host.current_state,
            target_id,
            x=commanded.x,
            y=commanded.y,
            rotation=commanded.rotation,
            z=commanded.z,
            actual_pose=actual,
        )
        host.current_state["last_updated"] = datetime.now().isoformat()
    host._set_status(SYSTEM_STATUS_HOLDING)
    print(
        f"{host.log_prefix} Hovered {target_id} -> "
        f"({commanded.x:.1f},{commanded.y:.1f},rot={commanded.rotation:.1f},"
        f"z={commanded.z:.1f})"
    )


async def run_place_from_hover(
    host: InAirHost, target_id: str, target_pose: Dict[str, float]
) -> None:
    print(f"{host.log_prefix} PlaceFromHover {target_id} -> {target_pose}")
    with host._state_lock:
        snapshot = host.current_state

    for refusal in (
        refuse_if_not_holding(snapshot, primitive_name="place_from_hover"),
        refuse_if_holding_other_tag(
            snapshot, target_id, primitive_name="place_from_hover"
        ),
        refuse_if_teleop_active(
            snapshot, target_id, primitive_name="place_from_hover"
        ),
    ):
        if refusal:
            print(f"{host.log_prefix} Refusing place_from_hover: {refusal.reason}")
            return

    target_pose = target_pose or {}
    tx = float(target_pose.get("target_x", target_pose.get("x", 0.0)))
    ty = float(target_pose.get("target_y", target_pose.get("y", 0.0)))
    trot = float(target_pose.get("rotation", 0.0))

    bound = refuse_if_in_storage_quadrant(tx, ty, primitive_name="place_from_hover")
    if bound:
        print(f"{host.log_prefix} Refusing place_from_hover: {bound.reason}")
        return

    commanded = LabPose(x=tx, y=ty, z=0.0, rotation=trot)

    host._null_measurables_for_targets([target_id], persist=False)
    host._set_status(SYSTEM_STATUS_BUSY)
    try:
        await host._primitive_place_from_hover(target_id, commanded, target_pose)
    except Exception:
        host._set_status(SYSTEM_STATUS_HOLDING)
        raise

    with host._state_lock:
        commit_place_from_hover(
            host.current_state,
            target_id,
            x=commanded.x,
            y=commanded.y,
            rotation=commanded.rotation,
        )
        host.current_state["last_updated"] = datetime.now().isoformat()
    host._set_status(SYSTEM_STATUS_IDLE)
    print(
        f"{host.log_prefix} Placed {target_id} from hover at "
        f"({commanded.x:.1f},{commanded.y:.1f},rot={commanded.rotation:.1f})"
    )


async def run_confirm_holding_tag(host: InAirHost, tag_id: str) -> None:
    """Operator confirms which tag is in the gripper (state-only)."""
    print(f"{host.log_prefix} ConfirmHoldingTag {tag_id}")
    with host._state_lock:
        if not is_holding(host.current_state):
            print(
                f"{host.log_prefix} confirm_holding_tag: system not "
                f"HOLDING; nothing to confirm."
            )
            return
        commit_confirm_holding_tag(host.current_state, tag_id)
        host.current_state["last_updated"] = datetime.now().isoformat()
    host._persist_state()
    print(f"{host.log_prefix} Confirmed held tag: {tag_id}")
