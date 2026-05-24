"""Primitive orchestration templates (semantics); hardware stays in lab_communicator."""

from .in_air import (
    run_confirm_holding_tag,
    run_hover_component,
    run_pick_component,
    run_place_from_hover,
)
from .live_feed import run_end_live_feed, run_start_live_feed
from .motor import run_move_motor
from .move import run_move_to_breadboard, run_move_to_storage
from .table_moves import (
    run_affirm_placed_at_current,
    run_move_component,
    run_place_from_storage,
    run_recenter_stored_in_inventory,
    run_repack_storage_slot,
    run_store_component,
)
from .measurables_record import run_record_measurables
from .optimize import run_optimize_component
from .scan_rotate import run_scan_rotate_in_place
from .teleop import TeleopController

__all__ = [
    "TeleopController",
    "run_affirm_placed_at_current",
    "run_confirm_holding_tag",
    "run_end_live_feed",
    "run_hover_component",
    "run_move_component",
    "run_move_motor",
    "run_move_to_breadboard",
    "run_move_to_storage",
    "run_optimize_component",
    "run_pick_component",
    "run_place_from_hover",
    "run_place_from_storage",
    "run_recenter_stored_in_inventory",
    "run_record_measurables",
    "run_repack_storage_slot",
    "run_scan_rotate_in_place",
    "run_start_live_feed",
    "run_store_component",
]
