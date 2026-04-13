"""Primitive identifiers and classification (see primitives.md)."""
from __future__ import annotations

try:
    from enum import StrEnum
except ImportError:  # Python < 3.11
    from enum import Enum

    class StrEnum(str, Enum):
        """Subset of stdlib StrEnum (3.11+); enough for string-valued enums here."""

        pass


class PrimitiveKind(StrEnum):
    """
    **ATOMIC:** one ``LabCommunicator`` method per step in ``dispatch._invoke_atomic``.

    **MACRO:** expanded in ``dispatch`` into other primitives (e.g. ``MOTOR_SEND_HOME`` → ``MOVE_MOTOR``).
    Do not use MACRO for “heavy work inside ``lab_automation``” only—that stays ATOMIC (e.g. ``OPTIMIZE``).
    """

    ATOMIC = "atomic"
    MACRO = "macro"


class PrimitiveId(StrEnum):
    """Includes POST /api/command actions and read primitives (GET per-tag slices)."""

    GET_TUNABLES = "GET_TUNABLES"
    GET_MEASURABLES = "GET_MEASURABLES"
    OBSERVE_MEASURABLES = "OBSERVE_MEASURABLES"

    MOVE_COMPONENT = "MOVE_COMPONENT"
    MOVE_MOTOR = "MOVE_MOTOR"
    MOTOR_SEND_HOME = "MOTOR_SEND_HOME"
    MOTOR_SET_ZERO = "MOTOR_SET_ZERO"
    OPTIMIZE = "OPTIMIZE"
    STORE_COMPONENT = "STORE_COMPONENT"
    PLACE_FROM_STORAGE = "PLACE_FROM_STORAGE"
    AFFIRM_PLACED_AT_CURRENT = "AFFIRM_PLACED_AT_CURRENT"
    REPACK_STORAGE = "REPACK_STORAGE"
    RECENTER_IN_STORAGE = "RECENTER_IN_STORAGE"
    SCAN = "SCAN"
    REMOVE = "REMOVE"


# Read primitives: not POST /api/command; used by GET routes + `fetch_read_primitive`.
READ_PRIMITIVE_IDS: frozenset[PrimitiveId] = frozenset(
    {
        PrimitiveId.GET_TUNABLES,
        PrimitiveId.GET_MEASURABLES,
    }
)

# Implemented by composing other primitives in ``dispatch`` (not a single LabCommunicator wrapper).
MACRO_PRIMITIVE_IDS: frozenset[PrimitiveId] = frozenset(
    {
        PrimitiveId.MOTOR_SEND_HOME,
    }
)
