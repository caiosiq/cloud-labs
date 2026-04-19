"""
Per-component tunables vs measurables (see refactor.md).

Tunables: what we command (nominal pose, storage intent, placement mode).
Measurables: what the lab reports (measured pose, optimization score, camera ref).
"""
from __future__ import annotations

from typing import Any, Dict, Optional

PRESENCE_BREADBOARD = "breadboard"
PRESENCE_STORAGE = "storage"
PRESENCE_OFF_TABLE = "off_table"

# placement.mode values (tunables.placement.mode) — non-exhaustive; free-form strings
# allowed for forward compatibility with new optimizer names.
PLACEMENT_MODE_MANUAL = "MANUAL"
PLACEMENT_MODE_STORAGE = "STORAGE"
PLACEMENT_MODE_HOVER = "HOVER"
"""Set by HOVER (component is held in mid-air at nominal pose incl. z, not placed)."""
PLACEMENT_MODE_PICK = "PICK"
"""Set by PICK_COMPONENT on first grasp before any HOVER relocation."""


def default_tunables() -> Dict[str, Any]:
    return {
        "presence": PRESENCE_BREADBOARD,
        "nominal_pose": {"x": 0.0, "y": 0.0, "rotation": 0.0},
        "nominal_motor_positions": {},
        "storage": {"in_storage": False, "slot": None},
        "placement": {"mode": "MANUAL"},
    }


def default_measurables() -> Dict[str, Any]:
    return {
        "pose": {"x": 0.0, "y": 0.0, "rotation": 0.0},
        "last_optimization_score": None,
        "last_optimized_pose": None,
        "camera_image": None,
    }


def new_component_entry(
    tag_id: str,
    comp_type: str,
    *,
    presence: str,
    nominal_pose: Dict[str, float],
    meas_pose: Dict[str, float],
    placement_mode: str = "MANUAL",
    in_storage: bool = False,
    slot: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    tun = default_tunables()
    tun["presence"] = presence
    tun["nominal_pose"] = dict(nominal_pose)
    tun["storage"] = {"in_storage": in_storage, "slot": slot}
    tun["placement"] = {"mode": placement_mode}
    meas = default_measurables()
    meas["pose"] = dict(meas_pose)
    return {"id": tag_id, "type": comp_type, "tunables": tun, "measurables": meas}


def get_tunables(entry: Dict[str, Any]) -> Dict[str, Any]:
    return dict(entry.get("tunables") or {})


def get_measurables(entry: Dict[str, Any]) -> Dict[str, Any]:
    return dict(entry.get("measurables") or {})


def presence_of(entry: Dict[str, Any]) -> str:
    t = entry.get("tunables") or {}
    p = t.get("presence")
    if p in (PRESENCE_BREADBOARD, PRESENCE_STORAGE, PRESENCE_OFF_TABLE):
        return p
    return PRESENCE_BREADBOARD


def is_stored(entry: Dict[str, Any]) -> bool:
    if presence_of(entry) == PRESENCE_STORAGE:
        return True
    st = (entry.get("tunables") or {}).get("storage") or {}
    return bool(st.get("in_storage"))


def is_off_table(entry: Dict[str, Any]) -> bool:
    return presence_of(entry) == PRESENCE_OFF_TABLE


def is_on_table(entry: Dict[str, Any]) -> bool:
    """Physically on the layout canvas (breadboard area or storage Q3)."""
    return presence_of(entry) in (PRESENCE_BREADBOARD, PRESENCE_STORAGE)


def meas_pose(entry: Dict[str, Any]) -> Dict[str, Any]:
    return dict((entry.get("measurables") or {}).get("pose") or {})


def nominal_pose(entry: Dict[str, Any]) -> Dict[str, Any]:
    np = (entry.get("tunables") or {}).get("nominal_pose")
    if np is None:
        return {}
    if isinstance(np, dict):
        return dict(np)
    return {}


def storage_slot(entry: Dict[str, Any]) -> Optional[Dict[str, int]]:
    sl = (entry.get("tunables") or {}).get("storage") or {}
    slot = sl.get("slot")
    if isinstance(slot, dict) and "i" in slot and "j" in slot:
        return {"i": int(slot["i"]), "j": int(slot["j"])}
    return None


def set_presence_and_storage(
    entry: Dict[str, Any],
    presence: str,
    *,
    in_storage: bool,
    slot: Optional[Dict[str, int]] = None,
) -> None:
    tun = entry.setdefault("tunables", default_tunables())
    tun["presence"] = presence
    s = tun.setdefault("storage", {"in_storage": False, "slot": None})
    s["in_storage"] = in_storage
    s["slot"] = slot


def placement_mode(entry: Dict[str, Any]) -> str:
    pl = (entry.get("tunables") or {}).get("placement") or {}
    m = pl.get("mode")
    if isinstance(m, str) and m:
        return m.upper()
    return "MANUAL"
