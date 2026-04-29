"""Shared state-machine refusal helpers.

Every primitive that's allowed to mutate ``current_state`` first walks
through a small list of refusals: "is this the right system_status?",
"is the right tag held?", "is this part STORED and therefore off-limits
for direct moves?", etc. These checks are identical between the real
and mock backends and were previously duplicated inside every
primitive method.

Each helper here returns a ``RefusalResult`` -- either ``ok()`` (no
refusal) or ``refuse(reason)`` with a human-friendly message the
orchestrator logs. The orchestrator decides what to do with a refusal
(early-return, raise, surface to the HTTP caller); the helpers are
pure (no logging, no side effects) so they're trivially testable.

Phase 2A populates this module with only the helpers that motor
primitives use (``refuse_if_stored``). Phase 2B/2C add helpers as the
in-air and heavy-state primitives migrate to template methods. See
``communicator_refactor.md`` §6 for the full inventory of which
primitive uses which refusal.

Architectural rule (``communicator_refactor.md`` §5.1): no
``lab_automation`` import, no :mod:`lab_communicator.base` import, no
real/mock backends. ``lab_model`` is fine.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from lab_model.component_model import PRESENCE_BREADBOARD, is_stored
from lab_model.holding import (
    SYSTEM_STATUS_HOLDING,
    SYSTEM_STATUS_IDLE,
    held_tag,
    is_holding,
)
from lab_model.storage_region import is_storage_region


@dataclass(frozen=True)
class RefusalResult:
    """Tiny tagged value: either "ok, proceed" or "refused with reason"."""

    refused: bool
    reason: Optional[str] = None

    def __bool__(self) -> bool:  # ``if refusal: ...`` reads naturally
        return self.refused


def ok() -> RefusalResult:
    """Return a non-refusal sentinel."""
    return RefusalResult(refused=False, reason=None)


def refuse(reason: str) -> RefusalResult:
    """Return a refusal with a human-readable reason."""
    return RefusalResult(refused=True, reason=reason)


def refuse_if_stored(
    current_state: Dict[str, Any],
    target_id: str,
    *,
    primitive_name: str = "operation",
) -> RefusalResult:
    """Refuse when ``target_id`` is currently STORED.

    Used by primitives that move components in the lab/breadboard
    frame -- ``move_component``, ``move_motor``, ``optimize_component``,
    ``pick_component`` -- where targeting a stored part is a UX bug
    (the user should ``place_from_storage`` first). ``primitive_name``
    is interpolated into the reason string for clearer logs.
    """
    components = current_state.get("components") or {}
    entry = components.get(target_id) if isinstance(components, dict) else None
    if isinstance(entry, dict) and is_stored(entry):
        return refuse(
            f"{target_id} is STORED; cannot {primitive_name}. "
            f"Use place_from_storage first."
        )
    return ok()


def refuse_if_holding(
    current_state: Dict[str, Any],
    *,
    primitive_name: str = "operation",
) -> RefusalResult:
    """Refuse when the gripper is already HOLDING something.

    Used by ``pick_component`` -- you can't pick a second part while
    one is already in the gripper. The held tag is included in the
    refusal reason so the operator can see what's blocking.
    """
    if is_holding(current_state):
        return refuse(
            f"already HOLDING {held_tag(current_state) or '<unknown>'}; "
            f"cannot {primitive_name}. PLACE_FROM_HOVER first."
        )
    return ok()


def refuse_if_not_holding(
    current_state: Dict[str, Any],
    *,
    primitive_name: str = "operation",
) -> RefusalResult:
    """Refuse when the gripper is NOT currently HOLDING.

    Used by ``hover_component`` and ``place_from_hover`` -- both
    require an in-gripper part to manipulate. The reason guides the
    operator to PICK_COMPONENT first.
    """
    if not is_holding(current_state):
        return refuse(
            f"not HOLDING; cannot {primitive_name}. PICK_COMPONENT first."
        )
    return ok()


def refuse_if_holding_other_tag(
    current_state: Dict[str, Any],
    target_id: str,
    *,
    primitive_name: str = "operation",
) -> RefusalResult:
    """Refuse when the gripper is HOLDING a tag different from ``target_id``.

    Same-tag gate for HOVER, PLACE_FROM_HOVER, SCAN_ROTATE_IN_PLACE
    (held mode). Permissive when ``held_tag`` is ``None`` (e.g.
    HOLDING_UNCONFIRMED) -- the operator may not have confirmed the
    tag yet, and we don't want to silently lock them out.
    """
    held = held_tag(current_state)
    if held and held != target_id:
        return refuse(
            f"currently holding {held}, cannot {primitive_name} {target_id}."
        )
    return ok()


def refuse_if_not_in_state(
    current_state: Dict[str, Any],
    target_id: str,
    *,
    primitive_name: str = "operation",
) -> RefusalResult:
    """Refuse when ``target_id`` has no entry in ``state['components']``.

    Used as a precondition for any primitive that reads the part's
    pose / measurables from state (PICK, SCAN_ROTATE placed-mode, etc).
    Catches typos and stale UI selections before we forward to hardware.
    """
    components = current_state.get("components") or {}
    if not isinstance(components, dict) or target_id not in components:
        return refuse(
            f"{target_id} not found in lab state; cannot {primitive_name}."
        )
    return ok()


def refuse_if_in_storage_quadrant(
    x: float,
    y: float,
    *,
    primitive_name: str = "operation",
) -> RefusalResult:
    """Refuse when an intended XY pose falls inside the storage quadrant.

    The storage region (Q3 in the breadboard frame) is reserved for
    parts intentionally placed via ``store_component`` / ``repack``,
    which carry quadrant-aware rotation + slot bookkeeping. A naive
    PLACE_FROM_HOVER into Q3 would clobber that bookkeeping; the
    operator should use ``store_component`` instead.
    """
    if is_storage_region(float(x), float(y)):
        return refuse(
            f"target ({x:.1f},{y:.1f}) is in storage quadrant; "
            f"cannot {primitive_name}. Use STORE_COMPONENT instead."
        )
    return ok()


def refuse_if_z_lab_out_of_bounds(
    z_lab: float,
    *,
    max_safe_z_lab_mm: float,
    primitive_name: str = "operation",
) -> RefusalResult:
    """Refuse when ``z_lab`` is outside the safe hover band ``[0, max]``.

    Catches runaway HTTP payloads (e.g. someone sending a robot-frame
    z=600 by mistake) before the orchestrator forward-transforms and
    hands it to the robot. ``z_lab`` is the height of the component
    base above the breadboard surface, in millimeters.
    """
    if not (0.0 <= float(z_lab) <= float(max_safe_z_lab_mm)):
        return refuse(
            f"z_lab={float(z_lab):.1f} mm outside safe range "
            f"[0, {float(max_safe_z_lab_mm):.1f}]; cannot {primitive_name}. "
            f"Interpret z as height of the component base above the table."
        )
    return ok()


def refuse_if_not_stored(
    current_state: Dict[str, Any],
    target_id: str,
    *,
    primitive_name: str = "operation",
) -> RefusalResult:
    """Refuse when ``target_id`` is NOT currently STORED.

    Used by ``place_from_storage``, ``repack_storage_slot``,
    ``recenter_stored_in_inventory``, ``affirm_placed_at_current`` --
    all of which require the part to currently be in storage. Distinct
    from :func:`refuse_if_stored`, which is its inverse.
    """
    components = current_state.get("components") or {}
    entry = components.get(target_id) if isinstance(components, dict) else None
    if not isinstance(entry, dict) or not is_stored(entry):
        return refuse(
            f"{target_id} must be STORED to {primitive_name} (got entry "
            f"={entry is not None and bool(entry)})."
        )
    return ok()


def refuse_if_not_on_breadboard(
    current_state: Dict[str, Any],
    target_id: str,
    *,
    primitive_name: str = "operation",
) -> RefusalResult:
    """Refuse when ``target_id`` is not on the breadboard.

    Used by ``scan_rotate_in_place`` (placed mode) and any other
    primitive that requires the part to be sitting on the table.
    Differs from :func:`refuse_if_stored`: that one specifically
    rejects STORAGE; this one rejects anything that isn't BREADBOARD
    (STORAGE, OFF_TABLE, missing entry, etc).
    """
    components = current_state.get("components") or {}
    entry = components.get(target_id) if isinstance(components, dict) else None
    presence = ((entry or {}).get("tunables") or {}).get("presence")
    if presence != PRESENCE_BREADBOARD:
        return refuse(
            f"{target_id} presence={presence!r}; cannot {primitive_name} "
            f"(need on breadboard)."
        )
    return ok()


def refuse_if_status_not_idle(
    current_state: Dict[str, Any],
    *,
    primitive_name: str = "operation",
) -> RefusalResult:
    """Refuse when ``system_status`` is not IDLE.

    Used by primitives that demand a clean idle system (e.g.
    ``scan_rotate_in_place`` placed-mode -- it needs the table to be
    quiescent before it can pick up, rotate, and put down the part).
    Status normalization handles older snapshots that pre-date the
    holding fields and have ``status=None``.
    """
    status = current_state.get("system_status") or SYSTEM_STATUS_IDLE
    if status != SYSTEM_STATUS_IDLE:
        return refuse(
            f"system_status={status!r}, need IDLE to {primitive_name}."
        )
    return ok()


__all__ = [
    "RefusalResult",
    "ok",
    "refuse",
    "refuse_if_stored",
    "refuse_if_holding",
    "refuse_if_not_holding",
    "refuse_if_holding_other_tag",
    "refuse_if_not_in_state",
    "refuse_if_in_storage_quadrant",
    "refuse_if_z_lab_out_of_bounds",
    "refuse_if_not_on_breadboard",
    "refuse_if_status_not_idle",
    "SYSTEM_STATUS_HOLDING",
    "SYSTEM_STATUS_IDLE",
]
