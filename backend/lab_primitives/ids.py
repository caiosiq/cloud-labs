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
    RECORD_MEASURABLES = "RECORD_MEASURABLES"

    MOVE_COMPONENT = "MOVE_COMPONENT"
    MOVE_MOTOR = "MOVE_MOTOR"
    SET_MOTOR_SETPOINT = "SET_MOTOR_SETPOINT"
    MOTOR_SEND_HOME = "MOTOR_SEND_HOME"
    MOTOR_SET_ZERO = "MOTOR_SET_ZERO"
    SET_EXPOSURE = "SET_EXPOSURE"
    APPLY_TUNABLES_PATCH = "APPLY_TUNABLES_PATCH"
    OPTIMIZE = "OPTIMIZE"
    STORE_COMPONENT = "STORE_COMPONENT"
    PLACE_FROM_STORAGE = "PLACE_FROM_STORAGE"
    AFFIRM_PLACED_AT_CURRENT = "AFFIRM_PLACED_AT_CURRENT"
    REPACK_STORAGE = "REPACK_STORAGE"
    RECENTER_IN_STORAGE = "RECENTER_IN_STORAGE"
    SCAN = "SCAN"
    REMOVE = "REMOVE"

    # --- In-air manipulation (see new_primitives.md) ---
    PICK_COMPONENT = "PICK_COMPONENT"
    HOVER = "HOVER"
    PLACE_FROM_HOVER = "PLACE_FROM_HOVER"
    SCAN_ROTATE_IN_PLACE = "SCAN_ROTATE_IN_PLACE"
    CONFIRM_HOLDING_TAG = "CONFIRM_HOLDING_TAG"

    # --- Per-component TELEOP (Phase 8 / universal_component_architecture §16.5) ---
    #: Acquire the per-component TELEOP lease. Sets ``tunables.teleop_active``
    #: True for the target and nulls its measurables (Golden Rule). Refused if
    #: the target is in storage, in motion (BUSY), or already teleoped by
    #: someone else. Refused lab-wide if ``teleop_safety.require_lab_idle`` is
    #: configured and the lab isn't IDLE.
    START_TELEOP = "START_TELEOP"
    #: Release the per-component TELEOP lease. Idempotent: ending an already-
    #: released session is not an error (the typical disconnect path).
    END_TELEOP = "END_TELEOP"
    #: Push one jog frame: an absolute ``nominal_pose`` and/or
    #: ``nominal_motor_positions`` for the component currently under TELEOP.
    #: Each frame also stamps ``tunables.teleop_last_jog_ts`` so the
    #: stale-lease sweeper can clear abandoned sessions.
    TELEOP_JOG = "TELEOP_JOG"


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
        PrimitiveId.APPLY_TUNABLES_PATCH,
    }
)
