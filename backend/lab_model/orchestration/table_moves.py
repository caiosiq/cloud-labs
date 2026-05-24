"""Table-move primitive entrypoints (refusal gates + slot allocation)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from lab_model.domain.storage_region import (
    STORAGE_NOMINAL_ROTATION_DEG,
    is_placed_region,
    nominal_center_pose_for_stored_entry,
)
from lab_model.state.commits import commit_affirm_placed
from lab_model.state.snapshot import LabPose
from lab_model.state.state_machine import (
    refuse_if_in_storage_quadrant,
    refuse_if_not_in_state,
    refuse_if_not_on_breadboard,
    refuse_if_not_stored,
    refuse_if_stored,
    refuse_if_teleop_active,
)

from .move import run_move_to_breadboard, run_move_to_storage
from .protocol import MoveHost


def _parse_table_xy_rotation(target_pose: Dict[str, Any]) -> Tuple[float, float, float]:
    target_pose = target_pose or {}
    tx = float(target_pose.get("target_x", target_pose.get("x", 0.0)))
    ty = float(target_pose.get("target_y", target_pose.get("y", 0.0)))
    trot = float(target_pose.get("rotation", 0.0))
    return tx, ty, trot


async def run_move_component(
    host: MoveHost, target_id: str, target_pose: Dict[str, float]
) -> None:
    """Move a placed (BREADBOARD) part to a new (XY, rotation) on the table."""
    print(f"{host.log_prefix} Move {target_id} -> {target_pose}")
    with host._state_lock:
        snapshot = host.current_state

    for refusal in (
        refuse_if_not_in_state(snapshot, target_id, primitive_name="move"),
        refuse_if_stored(snapshot, target_id, primitive_name="move"),
        refuse_if_teleop_active(snapshot, target_id, primitive_name="move"),
    ):
        if refusal:
            print(f"{host.log_prefix} Refusing move: {refusal.reason}")
            return

    tx, ty, trot = _parse_table_xy_rotation(target_pose)
    bound = refuse_if_in_storage_quadrant(tx, ty, primitive_name="move")
    if bound:
        print(f"{host.log_prefix} Refusing move: {bound.reason}")
        return

    commanded = LabPose(x=tx, y=ty, z=0.0, rotation=trot)
    await run_move_to_breadboard(host, target_id, commanded)


async def run_store_component(host: MoveHost, target_id: str) -> None:
    """Move a BREADBOARD part into the storage quadrant at a packed slot."""
    print(f"{host.log_prefix} Store {target_id}")
    with host._state_lock:
        snapshot = host.current_state

    refusal = refuse_if_not_on_breadboard(
        snapshot, target_id, primitive_name="store"
    )
    if refusal:
        print(f"{host.log_prefix} Refusing store: {refusal.reason}")
        return
    refusal = refuse_if_teleop_active(snapshot, target_id, primitive_name="store")
    if refusal:
        print(f"{host.log_prefix} Refusing store: {refusal.reason}")
        return

    slot = host._allocate_storage_slot(target_id)
    if slot is None:
        print(f"{host.log_prefix} Refusing store: no free storage slot in Q3.")
        return
    sx, sy, si, sj = slot
    commanded = LabPose(
        x=sx, y=sy, z=0.0, rotation=STORAGE_NOMINAL_ROTATION_DEG
    )
    await run_move_to_storage(host, target_id, commanded, si, sj)


async def run_place_from_storage(
    host: MoveHost, target_id: str, target_pose: Dict[str, Any]
) -> None:
    """Place a STORED part onto the breadboard at the given lab pose."""
    print(f"{host.log_prefix} PlaceFromStorage {target_id} -> {target_pose}")
    with host._state_lock:
        snapshot = host.current_state

    refusal = refuse_if_not_stored(
        snapshot, target_id, primitive_name="place_from_storage"
    )
    if refusal:
        print(f"{host.log_prefix} Refusing place_from_storage: {refusal.reason}")
        return
    refusal = refuse_if_teleop_active(
        snapshot, target_id, primitive_name="place_from_storage"
    )
    if refusal:
        print(f"{host.log_prefix} Refusing place_from_storage: {refusal.reason}")
        return

    tx, ty, trot = _parse_table_xy_rotation(target_pose)
    if not is_placed_region(tx, ty):
        print(
            f"{host.log_prefix} Refusing place_from_storage: target "
            f"({tx},{ty}) is inside storage quadrant."
        )
        return
    commanded = LabPose(x=tx, y=ty, z=0.0, rotation=trot)
    await run_move_to_breadboard(
        host, target_id, commanded, exiting_storage=True
    )


async def run_repack_storage_slot(host: MoveHost, target_id: str) -> None:
    """Move a STORED part to the next free inventory cell."""
    print(f"{host.log_prefix} RepackStorageSlot {target_id}")
    with host._state_lock:
        snapshot = host.current_state

    refusal = refuse_if_not_stored(snapshot, target_id, primitive_name="repack")
    if refusal:
        print(f"{host.log_prefix} Refusing repack: {refusal.reason}")
        return

    slot = host._allocate_storage_slot(target_id)
    if slot is None:
        print(f"{host.log_prefix} Refusing repack: no free storage slot.")
        return
    sx, sy, si, sj = slot
    commanded = LabPose(
        x=sx, y=sy, z=0.0, rotation=STORAGE_NOMINAL_ROTATION_DEG
    )
    await run_move_to_storage(host, target_id, commanded, si, sj)


async def run_recenter_stored_in_inventory(host: MoveHost, target_id: str) -> None:
    """Move a STORED part to the center of its currently assigned cell."""
    print(f"{host.log_prefix} RecenterStored {target_id}")
    with host._state_lock:
        snapshot = host.current_state
        entry = (snapshot.get("components") or {}).get(target_id)

    refusal = refuse_if_not_stored(snapshot, target_id, primitive_name="recenter")
    if refusal:
        print(f"{host.log_prefix} Refusing recenter: {refusal.reason}")
        return

    nom = nominal_center_pose_for_stored_entry(entry or {})
    if nom is None:
        print(
            f"{host.log_prefix} Refusing recenter: cannot resolve storage "
            f"cell (need slot metadata or pose in Q3)."
        )
        return
    sx, sy, si, sj = nom
    commanded = LabPose(
        x=sx, y=sy, z=0.0, rotation=STORAGE_NOMINAL_ROTATION_DEG
    )
    await run_move_to_storage(host, target_id, commanded, si, sj)


async def run_affirm_placed_at_current(host: MoveHost, target_id: str) -> None:
    """Mark a STORED part as PLACED at its current pose (no hardware move)."""
    print(f"{host.log_prefix} AffirmPlacedAtCurrent {target_id}")
    with host._state_lock:
        snapshot = host.current_state

    refusal = refuse_if_not_stored(
        snapshot, target_id, primitive_name="affirm_placed"
    )
    if refusal:
        print(f"{host.log_prefix} Refusing affirm: {refusal.reason}")
        return

    with host._state_lock:
        commit_affirm_placed(host.current_state, target_id)
        host.current_state["last_updated"] = datetime.now().isoformat()
    host._after_move_out_of_storage(target_id)
    host._apply_is_placed_flag(target_id, True)
    host._persist_state()
    print(f"{host.log_prefix} {target_id} marked PLACED at current pose")
