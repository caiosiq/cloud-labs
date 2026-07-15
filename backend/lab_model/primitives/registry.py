"""
Registry: primitive id → metadata + handler method name on LabCommunicator.

Validation lives in schemas.py (Pydantic models per body).

**Atomic:** one ``LabCommunicator`` method per dispatch step (see ``dispatch._invoke_atomic``).

**Macro:** expanded inside ``dispatch.execute_validated_command`` into atomic primitives (e.g.
``MOTOR_SEND_HOME`` → read angle + ``MOVE_MOTOR``). ``handler`` may still name a method on the
communicator for **direct** Python callers / tests; macros bypass it in ``dispatch``.
"""
from __future__ import annotations

from typing import Any, Dict

from .ids import PrimitiveId, PrimitiveKind

# method name on LabCommunicator — must match base.py (None = no single adapter method used by dispatch)
PRIMITIVE_REGISTRY: Dict[PrimitiveId, Dict[str, Any]] = {
    PrimitiveId.GET_TUNABLES: {
        "kind": PrimitiveKind.ATOMIC,
        "read_only": True,
        "handler": "return_tunables_for_tag",
        "http": "GET /api/components/{tag_id}/tunables",
    },
    PrimitiveId.GET_MEASURABLES: {
        "kind": PrimitiveKind.ATOMIC,
        "read_only": True,
        "handler": "return_measurables_for_tag",
        "http": "GET /api/components/{tag_id}/measurables",
    },
    PrimitiveId.RECORD_MEASURABLES: {
        "kind": PrimitiveKind.ATOMIC,
        "read_only": False,
        "handler": "record_measurables_for_tag",
        "http": "POST /api/components/{tag_id}/measurables/record",
    },
    PrimitiveId.EVAL_KERNEL: {
        "kind": PrimitiveKind.ATOMIC,
        "read_only": False,
        "handler": "eval_kernel_for_tag",
        "http": "POST /api/command (action=EVAL_KERNEL)",
        "notes": (
            "Authoring probe; kernels are inputs (kernel_id). "
            "Closed-loop OPTIMIZE runs kernels in-process — not via this primitive per eval."
        ),
    },
    PrimitiveId.MOVE_COMPONENT: {
        "kind": PrimitiveKind.ATOMIC,
        "read_only": False,
        "handler": "move_component",
    },
    PrimitiveId.MOVE_MOTOR: {
        "kind": PrimitiveKind.ATOMIC,
        "read_only": False,
        "handler": "move_motor",
    },
    PrimitiveId.SET_MOTOR_SETPOINT: {
        "kind": PrimitiveKind.ATOMIC,
        "read_only": False,
        "handler": "set_motor_setpoint",
    },
    PrimitiveId.SET_EXPOSURE: {
        "kind": PrimitiveKind.ATOMIC,
        "read_only": False,
        "handler": "set_exposure_time_ms",
    },
    PrimitiveId.SET_LASER_OUTPUT: {
        "kind": PrimitiveKind.ATOMIC,
        "read_only": False,
        "handler": "set_output_power_mw",
    },
    PrimitiveId.APPLY_TUNABLES_PATCH: {
        "kind": PrimitiveKind.MACRO,
        "read_only": False,
        "handler": "apply_tunables_patch",
        "macro_expands_to": [
            PrimitiveId.SET_EXPOSURE,
            PrimitiveId.SET_LASER_OUTPUT,
            PrimitiveId.SET_MOTOR_SETPOINT,
        ],
    },
    PrimitiveId.MOTOR_SEND_HOME: {
        "kind": PrimitiveKind.MACRO,
        "read_only": False,
        "handler": "motor_send_home",
        "macro_expands_to": [PrimitiveId.MOVE_MOTOR],
    },
    PrimitiveId.MOTOR_SET_ZERO: {
        "kind": PrimitiveKind.ATOMIC,
        "read_only": False,
        "handler": "motor_set_zero",
    },
    PrimitiveId.OPTIMIZE: {
        "kind": PrimitiveKind.ATOMIC,
        "read_only": False,
        "handler": "optimize_component",
    },
    PrimitiveId.STORE_COMPONENT: {
        "kind": PrimitiveKind.ATOMIC,
        "read_only": False,
        "handler": "store_component",
    },
    PrimitiveId.PLACE_FROM_STORAGE: {
        "kind": PrimitiveKind.ATOMIC,
        "read_only": False,
        "handler": "place_from_storage",
    },
    PrimitiveId.AFFIRM_PLACED_AT_CURRENT: {
        "kind": PrimitiveKind.ATOMIC,
        "read_only": False,
        "handler": "affirm_placed_at_current",
    },
    PrimitiveId.REPACK_STORAGE: {
        "kind": PrimitiveKind.ATOMIC,
        "read_only": False,
        "handler": "repack_storage_slot",
    },
    PrimitiveId.RECENTER_IN_STORAGE: {
        "kind": PrimitiveKind.ATOMIC,
        "read_only": False,
        "handler": "recenter_stored_in_inventory",
    },
    PrimitiveId.SCAN: {
        "kind": PrimitiveKind.ATOMIC,
        "read_only": False,
        "handler": None,
    },
    PrimitiveId.REMOVE: {
        "kind": PrimitiveKind.ATOMIC,
        "read_only": False,
        "handler": "remove_component",
    },
    PrimitiveId.PICK_COMPONENT: {
        "kind": PrimitiveKind.ATOMIC,
        "read_only": False,
        "handler": "pick_component",
    },
    PrimitiveId.HOVER: {
        "kind": PrimitiveKind.ATOMIC,
        "read_only": False,
        "handler": "hover_component",
    },
    PrimitiveId.PLACE_FROM_HOVER: {
        "kind": PrimitiveKind.ATOMIC,
        "read_only": False,
        "handler": "place_from_hover",
    },
    PrimitiveId.SCAN_ROTATE_IN_PLACE: {
        "kind": PrimitiveKind.ATOMIC,
        "read_only": False,
        "handler": "scan_rotate_in_place",
    },
    PrimitiveId.CONFIRM_HOLDING_TAG: {
        "kind": PrimitiveKind.ATOMIC,
        "read_only": False,
        "handler": "confirm_holding_tag",
    },
    # --- Per-component TELEOP (Phase 8 / §16.5) ---
    PrimitiveId.START_TELEOP: {
        "kind": PrimitiveKind.ATOMIC,
        "read_only": False,
        "handler": "start_teleop",
        "http": "POST /api/components/{tag_id}/teleop/start",
    },
    PrimitiveId.END_TELEOP: {
        "kind": PrimitiveKind.ATOMIC,
        "read_only": False,
        "handler": "end_teleop",
        "http": "POST /api/components/{tag_id}/teleop/end",
    },
    PrimitiveId.TELEOP_GOTO: {
        "kind": PrimitiveKind.ATOMIC,
        "read_only": False,
        "handler": "teleop_goto",
        "http": "POST /api/components/{tag_id}/telemetry/goto",
    },
    PrimitiveId.TELEOP_JOG: {
        "kind": PrimitiveKind.ATOMIC,
        "read_only": False,
        "handler": "teleop_jog",
        "http": "POST /api/components/{tag_id}/telemetry/jog",
    },
    PrimitiveId.START_LIVE_FEED: {
        "kind": PrimitiveKind.ATOMIC,
        "read_only": False,
        "handler": "start_live_feed",
        "http": "POST /api/components/{tag_id}/telemetry/live-feed/start",
    },
    PrimitiveId.END_LIVE_FEED: {
        "kind": PrimitiveKind.ATOMIC,
        "read_only": False,
        "handler": "end_live_feed",
        "http": "POST /api/components/{tag_id}/telemetry/live-feed/end",
    },
}
