"""
Top-level lab-state helpers for the ``holding`` field and ``HOLDING`` system status.

Unlike ``tunables`` / ``measurables`` (per-component), the fact that the robot is
currently holding a part in mid-air is a **top-level** attribute of the lab state:

    {
      "system_status": "HOLDING" | "IDLE" | "BUSY" | "OPTIMIZING",
      "holding": {
         "tag_id": "<tag>" | null,
         "nominal_pose": {"x": .., "y": .., "rotation": .., "z": ..},
         "requires_operator_confirm": false
      },
      ...
    }

``requires_operator_confirm`` is set when ``RealLabCommunicator`` boots with the
hardware gripper closed but no matching software snapshot exists
(see ``new_primitives.md`` §6.3). The UI must lock cross-part commands until a
``CONFIRM_HOLDING_TAG`` primitive clears the flag.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

SYSTEM_STATUS_IDLE = "IDLE"
SYSTEM_STATUS_BUSY = "BUSY"
SYSTEM_STATUS_OPTIMIZING = "OPTIMIZING"
SYSTEM_STATUS_HOLDING = "HOLDING"
#: Phase 8 per-component TELEOP.
#:
#: Informational status reported at the top level when *any* component
#: in ``state['components']`` has ``tunables.teleop_active == True``.
#: Per §16.5 of ``universal_component_architecture.md`` per-component
#: concurrency is allowed by default -- TELEOP does *not* block
#: optimization on a different component -- so the actual gate is the
#: per-component ``teleop_active`` field, not this status. The status
#: exists only so the UI / API consumers can spot "something is being
#: teleoped somewhere" at a glance.
SYSTEM_STATUS_TELEOP = "TELEOP"

BREADBOARD_SURFACE_Z_LAB_MM = 215.0
"""Height of the breadboard surface above the lab floor (``z_lab`` convention)."""

DEFAULT_HOVER_Z_MM = 245.0
"""Default safe hover height (mm above **lab floor**): breadboard (~215 mm) + ~30 mm clearance."""


def empty_holding() -> Dict[str, Any]:
    """Initial/cleared holding block (no part in gripper, no confirm required)."""
    return {
        "tag_id": None,
        "nominal_pose": None,
        "requires_operator_confirm": False,
    }


def get_holding(state: Dict[str, Any]) -> Dict[str, Any]:
    """Return the ``holding`` dict, inserting the default shape if missing."""
    h = state.get("holding")
    if not isinstance(h, dict):
        h = empty_holding()
        state["holding"] = h
    else:
        # Normalize keys so downstream code can rely on them.
        h.setdefault("tag_id", None)
        h.setdefault("nominal_pose", None)
        h.setdefault("requires_operator_confirm", False)
    return h


def is_holding(state: Dict[str, Any]) -> bool:
    """True if ``system_status`` is HOLDING (whether or not the tag is confirmed)."""
    return state.get("system_status") == SYSTEM_STATUS_HOLDING


def held_tag(state: Dict[str, Any]) -> Optional[str]:
    """Return the held tag_id if set, else None."""
    return get_holding(state).get("tag_id") or None


def requires_operator_confirm(state: Dict[str, Any]) -> bool:
    return bool(get_holding(state).get("requires_operator_confirm"))


def set_holding(
    state: Dict[str, Any],
    tag_id: Optional[str],
    *,
    x: float,
    y: float,
    rotation: float = 0.0,
    z: float = DEFAULT_HOVER_Z_MM,
    requires_operator_confirm_flag: bool = False,
) -> None:
    """Write the top-level HOLDING fields (pose + tag + flags) and flip status."""
    state["system_status"] = SYSTEM_STATUS_HOLDING
    state["holding"] = {
        "tag_id": tag_id,
        "nominal_pose": {
            "x": float(x),
            "y": float(y),
            "rotation": float(rotation),
            "z": float(z),
        },
        "requires_operator_confirm": bool(requires_operator_confirm_flag),
    }


def clear_holding(state: Dict[str, Any]) -> None:
    """After a PLACE_FROM_HOVER (or equivalent): not holding anything; status IDLE."""
    state["system_status"] = SYSTEM_STATUS_IDLE
    state["holding"] = empty_holding()


def confirm_holding_tag(state: Dict[str, Any], tag_id: str) -> None:
    """Operator confirms which tag is actually in the gripper; clears the confirm flag."""
    h = get_holding(state)
    h["tag_id"] = str(tag_id)
    h["requires_operator_confirm"] = False
    state["holding"] = h
    state["system_status"] = SYSTEM_STATUS_HOLDING
