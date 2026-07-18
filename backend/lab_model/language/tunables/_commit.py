"""Shared tunable commit helpers (intent-only state writes)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Mapping

from lab_model.language.domain.component import tunables_bucket


def commit_tunable_value(bridge: Any, tag_id: str, field: str, value: Any) -> bool:
    """Set ``statecontrol.tunables[field]`` and persist. Returns False if tag missing."""
    with bridge._state_lock:
        entry = (bridge.current_state.get("components") or {}).get(tag_id)
        if not isinstance(entry, dict):
            return False
        tun = tunables_bucket(entry)
        tun[field] = value
        bridge.current_state["last_updated"] = datetime.now().isoformat()
    bridge._persist_state()
    return True


def commit_nominal_pose(bridge: Any, tag_id: str, pose: Mapping[str, Any]) -> bool:
    """Merge ``x``, ``y``, ``rotation`` (and optional ``z``) into ``tunables.nominal_pose``."""
    if not pose:
        return False
    allowed = ("x", "y", "z", "rotation")
    merged: Dict[str, float] = {}
    with bridge._state_lock:
        entry = (bridge.current_state.get("components") or {}).get(tag_id)
        if not isinstance(entry, dict):
            return False
        tun = tunables_bucket(entry)
        cur = tun.get("nominal_pose")
        if isinstance(cur, dict):
            merged.update({k: float(v) for k, v in cur.items() if k in allowed})
        for key in allowed:
            if key in pose and pose[key] is not None:
                merged[key] = float(pose[key])
        if not merged:
            return False
        tun["nominal_pose"] = merged
        bridge.current_state["last_updated"] = datetime.now().isoformat()
    bridge._persist_state()
    return True
