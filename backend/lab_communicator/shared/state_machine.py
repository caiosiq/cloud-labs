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

from lab_model.component_model import is_stored


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
    ``hover_component`` -- where targeting a stored part is a UX bug
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
