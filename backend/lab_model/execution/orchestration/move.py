"""Shared table-move orchestration (breadboard vs storage commits)."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from lab_model.language.domain.holding import SYSTEM_STATUS_BUSY, SYSTEM_STATUS_IDLE
from lab_model.coordinator.state.commits import commit_move_to_breadboard, commit_move_to_storage
from lab_model.coordinator.state.snapshot import LabPose

from .protocol import MoveHost


async def run_move_to_breadboard(
    host: MoveHost,
    target_id: str,
    commanded: LabPose,
    *,
    exiting_storage: bool = False,
) -> None:
    """BUSY → hardware move → BREADBOARD commit → IDLE."""
    host._null_measurables_for_targets([target_id], persist=False)
    host._set_status(SYSTEM_STATUS_BUSY)
    actual: Optional[LabPose] = None
    try:
        actual = await host._primitive_move_component(target_id, commanded)
    except Exception as e:  # noqa: BLE001
        print(f"{host.log_prefix} Move failed: {e}")
        host._set_status(SYSTEM_STATUS_IDLE)
        return

    with host._state_lock:
        commit_move_to_breadboard(
            host.current_state,
            target_id,
            x=commanded.x,
            y=commanded.y,
            rotation=commanded.rotation,
            actual_pose=actual,
        )
        host.current_state["last_updated"] = datetime.now().isoformat()
    if exiting_storage:
        host._after_move_out_of_storage(target_id)
    host._apply_is_placed_flag(target_id, True)
    host._set_status(SYSTEM_STATUS_IDLE)


async def run_move_to_storage(
    host: MoveHost,
    target_id: str,
    commanded: LabPose,
    slot_i: int,
    slot_j: int,
) -> None:
    """BUSY → hardware move → STORAGE commit → IDLE."""
    host._null_measurables_for_targets([target_id], persist=False)
    host._set_status(SYSTEM_STATUS_BUSY)
    actual: Optional[LabPose] = None
    try:
        actual = await host._primitive_move_component(target_id, commanded)
    except Exception as e:  # noqa: BLE001
        print(f"{host.log_prefix} Move failed: {e}")
        host._set_status(SYSTEM_STATUS_IDLE)
        return

    with host._state_lock:
        commit_move_to_storage(
            host.current_state,
            target_id,
            x=commanded.x,
            y=commanded.y,
            rotation=commanded.rotation,
            slot_i=slot_i,
            slot_j=slot_j,
            actual_pose=actual,
        )
        host.current_state["last_updated"] = datetime.now().isoformat()
    host._after_move_to_storage(target_id, slot_i, slot_j)
    host._apply_is_placed_flag(target_id, False)
    host._set_status(SYSTEM_STATUS_IDLE)
