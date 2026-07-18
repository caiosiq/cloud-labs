"""Primitive orchestration templates (semantics); hardware stays in lab_communicator."""

from __future__ import annotations

import importlib
from typing import Any

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

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "TeleopController": (".teleop", "TeleopController"),
    "run_affirm_placed_at_current": (".table_moves", "run_affirm_placed_at_current"),
    "run_confirm_holding_tag": (".in_air", "run_confirm_holding_tag"),
    "run_end_live_feed": (".live_feed", "run_end_live_feed"),
    "run_hover_component": (".in_air", "run_hover_component"),
    "run_move_component": (".table_moves", "run_move_component"),
    "run_move_motor": (".motor", "run_move_motor"),
    "run_move_to_breadboard": (".move", "run_move_to_breadboard"),
    "run_move_to_storage": (".move", "run_move_to_storage"),
    "run_optimize_component": (".optimize", "run_optimize_component"),
    "run_pick_component": (".in_air", "run_pick_component"),
    "run_place_from_hover": (".in_air", "run_place_from_hover"),
    "run_place_from_storage": (".table_moves", "run_place_from_storage"),
    "run_recenter_stored_in_inventory": (
        ".table_moves",
        "run_recenter_stored_in_inventory",
    ),
    "run_record_measurables": (".measurables_record", "run_record_measurables"),
    "run_repack_storage_slot": (".table_moves", "run_repack_storage_slot"),
    "run_scan_rotate_in_place": (".scan_rotate", "run_scan_rotate_in_place"),
    "run_start_live_feed": (".live_feed", "run_start_live_feed"),
    "run_store_component": (".table_moves", "run_store_component"),
}


def __getattr__(name: str) -> Any:
    spec = _LAZY_EXPORTS.get(name)
    if spec is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = spec
    module = importlib.import_module(module_name, __name__)
    return getattr(module, attr_name)
