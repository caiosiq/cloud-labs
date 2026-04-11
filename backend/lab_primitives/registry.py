"""
Registry: primitive id → metadata + handler method name on LabCommunicator.

Validation lives in schemas.py (Pydantic models per body).

**Atomic:** one ``LabCommunicator`` method per dispatch step (see ``dispatch._invoke_atomic``).

**Macro:** expanded inside ``dispatch.execute_validated_command`` into atomic primitives (e.g.
``MOTOR_SEND_HOME`` → read angle + ``MOVE_MOTOR``). ``handler`` may still name a method on the
communicator for **direct** Python callers / tests; ``lab_primitives`` bypasses it for macros.
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
    PrimitiveId.OBSERVE_MEASURABLES: {
        "kind": PrimitiveKind.ATOMIC,
        "read_only": False,
        "handler": "observe_measurables_for_tag",
        "http": "POST /api/components/{tag_id}/measurables/observe",
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
}
