"""
Per-component shape: ``parameters`` + ``statecontrol`` + ``telemetry``.

Parameters:
  - static identity / manufacturer / constants (from catalog; not set by motion)

StateControl:
  - tunables — commanded intent *and* reported values for the same degrees of
    freedom (``nominal_pose`` / ``reported_pose``, motor setpoints / readback).
    A reported value is still the tunable family: refresh it from the lab when
    command and reality may drift. It is *not* a measurable.
  - measurables — observations with no 1:1 tunable counterpart (camera frames,
    kernel scores, …). These must be captured; they can be stochastic.

Telemetry:
  - teleop — fast control lease (analogous to tunables)
  - live_feed — streaming observation sessions (analogous to measurables)
"""
from __future__ import annotations

from typing import Any, Dict, Optional

PRESENCE_BREADBOARD = "breadboard"
PRESENCE_STORAGE = "storage"
PRESENCE_OFF_TABLE = "off_table"

PLACEMENT_MODE_MANUAL = "MANUAL"
PLACEMENT_MODE_STORAGE = "STORAGE"
PLACEMENT_MODE_HOVER = "HOVER"
PLACEMENT_MODE_PICK = "PICK"


def default_tunables() -> Dict[str, Any]:
    return {
        "presence": PRESENCE_BREADBOARD,
        "nominal_pose": {"x": 0.0, "y": 0.0, "rotation": 0.0},
        # Bench-reported pose for the same DOF as nominal_pose (not a measurable).
        "reported_pose": {"x": 0.0, "y": 0.0, "rotation": 0.0},
        "nominal_motor_positions": {},
        "storage": {"in_storage": False, "slot": None},
        "placement": {"mode": "MANUAL"},
    }


def default_measurables() -> Dict[str, Any]:
    return {
        # Legacy mirror of tunables.reported_pose — prefer reported_pose.
        # Kept so older state files and consumers keep working during migration.
        "pose": {"x": 0.0, "y": 0.0, "rotation": 0.0},
        "last_optimization_score": None,
        "last_optimized_pose": None,
        "camera_image": None,
    }


def default_live_feed_channel() -> Dict[str, Any]:
    return {
        "connected": False,
        "live": False,
        "backend": None,
        "resource_id": None,
        "last_error": None,
    }


def default_teleop_command() -> Dict[str, Any]:
    return {
        "target": None,
        "speed": {"linear_mm_s": 25.0, "angular_deg_s": 15.0},
        "phase": "idle",
    }


def default_teleop_session() -> Dict[str, Any]:
    return {
        "active": False,
        "ready": False,
        "lease_ts": None,
        "last_jog_ts": None,
        "last_error": None,
        "mode": None,
        "command": default_teleop_command(),
    }


def default_telemetry() -> Dict[str, Any]:
    return {
        "teleop": default_teleop_session(),
        "live_feed": {
            "stream": default_live_feed_channel(),
        },
    }


def default_statecontrol() -> Dict[str, Any]:
    return {
        "tunables": default_tunables(),
        "measurables": default_measurables(),
    }


def default_parameters() -> Dict[str, Any]:
    """Empty static-identity bag (filled from catalog)."""
    return {}


def _strip_legacy_teleop_from_tunables(tun: Dict[str, Any]) -> tuple[bool, Optional[float]]:
    active = bool(tun.pop("teleop_active", False))
    ts = tun.pop("teleop_last_jog_ts", None)
    last_jog = float(ts) if isinstance(ts, (int, float)) else None
    return active, last_jog


def ensure_component_shape(entry: Dict[str, Any]) -> None:
    """Normalize legacy flat ``tunables``/``measurables`` into statecontrol + telemetry."""
    if not isinstance(entry, dict):
        return

    if not isinstance(entry.get("parameters"), dict):
        entry["parameters"] = default_parameters()

    if "statecontrol" in entry and "telemetry" in entry:
        sc = entry.get("statecontrol")
        if isinstance(sc, dict):
            tun = sc.get("tunables")
            if isinstance(tun, dict):
                active, last_jog = _strip_legacy_teleop_from_tunables(tun)
                if active or last_jog is not None:
                    tel = entry.setdefault("telemetry", default_telemetry())
                    top = tel.setdefault("teleop", {"active": False, "last_jog_ts": None})
                    if active:
                        top["active"] = True
                    if last_jog is not None:
                        top["last_jog_ts"] = last_jog
            # Migrate legacy measurables.pose → tunables.reported_pose
            _migrate_reported_pose_from_legacy(sc)
        tel = entry.setdefault("telemetry", default_telemetry())
        lf = tel.setdefault("live_feed", default_telemetry()["live_feed"])
        for ch in ("stream",):
            lf.setdefault(ch, default_live_feed_channel())
        lf.pop("preview", None)
        top = tel.setdefault("teleop", default_teleop_session())
        if top.get("active") and "ready" not in top:
            top["ready"] = True
        top.setdefault("ready", False)
        top.setdefault("lease_ts", top.get("last_jog_ts"))
        top.setdefault("last_error", None)
        top.setdefault("mode", None)
        cmd = top.setdefault("command", default_teleop_command())
        if not isinstance(cmd, dict):
            top["command"] = default_teleop_command()
        else:
            cmd.setdefault("target", None)
            cmd.setdefault("speed", {"linear_mm_s": 25.0, "angular_deg_s": 15.0})
            cmd.setdefault("phase", "idle")
        entry.pop("tunables", None)
        entry.pop("measurables", None)
        return

    legacy_tun = entry.pop("tunables", None)
    legacy_meas = entry.pop("measurables", None)

    teleop_active = False
    teleop_last_jog_ts: Optional[float] = None
    tun = default_tunables()
    if isinstance(legacy_tun, dict):
        teleop_active, teleop_last_jog_ts = _strip_legacy_teleop_from_tunables(legacy_tun)
        for k, v in legacy_tun.items():
            tun[k] = v

    meas = default_measurables()
    if isinstance(legacy_meas, dict):
        for k, v in legacy_meas.items():
            meas[k] = v

    entry["statecontrol"] = {"tunables": tun, "measurables": meas}
    _migrate_reported_pose_from_legacy(entry["statecontrol"])
    tel = default_telemetry()
    tel["teleop"]["active"] = teleop_active
    tel["teleop"]["ready"] = teleop_active
    tel["teleop"]["last_jog_ts"] = teleop_last_jog_ts
    if teleop_active and teleop_last_jog_ts is not None:
        tel["teleop"]["lease_ts"] = float(teleop_last_jog_ts)
    entry["telemetry"] = tel


def _migrate_reported_pose_from_legacy(sc: Dict[str, Any]) -> None:
    """If ``reported_pose`` is missing, lift legacy ``measurables.pose`` into tunables."""
    if not isinstance(sc, dict):
        return
    tun = sc.setdefault("tunables", default_tunables())
    meas = sc.setdefault("measurables", default_measurables())
    if not isinstance(tun, dict) or not isinstance(meas, dict):
        return
    if "reported_pose" in tun and tun.get("reported_pose") is not None:
        # Keep legacy mirror in sync when reported already exists
        if isinstance(tun.get("reported_pose"), dict) and meas.get("pose") is None:
            meas["pose"] = dict(tun["reported_pose"])
        return
    legacy = meas.get("pose")
    if isinstance(legacy, dict):
        tun["reported_pose"] = dict(legacy)
    elif legacy is None and "reported_pose" not in tun:
        tun["reported_pose"] = None
    elif "reported_pose" not in tun:
        tun["reported_pose"] = dict(default_tunables()["reported_pose"])


def tunables_bucket(entry: Dict[str, Any]) -> Dict[str, Any]:
    ensure_component_shape(entry)
    sc = entry.setdefault("statecontrol", default_statecontrol())
    return sc.setdefault("tunables", default_tunables())


def measurables_bucket(entry: Dict[str, Any]) -> Dict[str, Any]:
    ensure_component_shape(entry)
    sc = entry.setdefault("statecontrol", default_statecontrol())
    return sc.setdefault("measurables", default_measurables())


def teleop_bucket(entry: Dict[str, Any]) -> Dict[str, Any]:
    ensure_component_shape(entry)
    tel = entry.setdefault("telemetry", default_telemetry())
    return tel.setdefault("teleop", {"active": False, "last_jog_ts": None})


def live_feed_bucket(entry: Dict[str, Any]) -> Dict[str, Any]:
    ensure_component_shape(entry)
    tel = entry.setdefault("telemetry", default_telemetry())
    return tel.setdefault("live_feed", default_telemetry()["live_feed"])


def live_feed_channel(entry: Dict[str, Any], channel: str) -> Dict[str, Any]:
    lf = live_feed_bucket(entry)
    return lf.setdefault(channel, default_live_feed_channel())


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
    parameters: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    tun = default_tunables()
    tun["presence"] = presence
    tun["nominal_pose"] = dict(nominal_pose)
    tun["reported_pose"] = dict(meas_pose)
    tun["storage"] = {"in_storage": in_storage, "slot": slot}
    tun["placement"] = {"mode": placement_mode}
    meas = default_measurables()
    # Legacy mirror — reported_pose is canonical.
    meas["pose"] = dict(meas_pose)
    return {
        "id": tag_id,
        "type": comp_type,
        "parameters": dict(parameters) if isinstance(parameters, dict) else default_parameters(),
        "statecontrol": {"tunables": tun, "measurables": meas},
        "telemetry": default_telemetry(),
    }


def get_tunables(entry: Dict[str, Any]) -> Dict[str, Any]:
    return dict(tunables_bucket(entry))


def get_measurables(entry: Dict[str, Any]) -> Dict[str, Any]:
    return dict(measurables_bucket(entry))


def get_parameters(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Static identity bag on a runtime component entry (may be empty)."""
    ensure_component_shape(entry)
    bag = entry.get("parameters")
    return dict(bag) if isinstance(bag, dict) else default_parameters()


def set_parameters(entry: Dict[str, Any], parameters: Dict[str, Any]) -> None:
    """Replace the mirrored parameters bag (catalog remains source of truth)."""
    ensure_component_shape(entry)
    entry["parameters"] = dict(parameters) if isinstance(parameters, dict) else default_parameters()


def get_telemetry(entry: Dict[str, Any]) -> Dict[str, Any]:
    ensure_component_shape(entry)
    import json

    return json.loads(json.dumps(entry.get("telemetry") or default_telemetry()))


def get_statecontrol(entry: Dict[str, Any]) -> Dict[str, Any]:
    ensure_component_shape(entry)
    sc = entry.get("statecontrol") or default_statecontrol()
    return {"tunables": dict(sc.get("tunables") or {}), "measurables": dict(sc.get("measurables") or {})}


def presence_of(entry: Dict[str, Any]) -> str:
    t = get_tunables(entry)
    p = t.get("presence")
    if p in (PRESENCE_BREADBOARD, PRESENCE_STORAGE, PRESENCE_OFF_TABLE):
        return p
    return PRESENCE_BREADBOARD


def is_stored(entry: Dict[str, Any]) -> bool:
    if presence_of(entry) == PRESENCE_STORAGE:
        return True
    st = get_tunables(entry).get("storage") or {}
    return bool(st.get("in_storage"))


def is_off_table(entry: Dict[str, Any]) -> bool:
    return presence_of(entry) == PRESENCE_OFF_TABLE


def is_on_table(entry: Dict[str, Any]) -> bool:
    return presence_of(entry) in (PRESENCE_BREADBOARD, PRESENCE_STORAGE)


def reported_pose(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Bench-reported pose for the pose tunable (not a measurable).

    Prefers ``tunables.reported_pose``; falls back to legacy ``measurables.pose``.
    """
    ensure_component_shape(entry)
    rp = get_tunables(entry).get("reported_pose")
    if isinstance(rp, dict):
        return dict(rp)
    if rp is None:
        return {}
    legacy = get_measurables(entry).get("pose")
    return dict(legacy) if isinstance(legacy, dict) else {}


def set_reported_pose(entry: Dict[str, Any], pose: Optional[Dict[str, Any]]) -> None:
    """Write reported pose under tunables; mirror to legacy ``measurables.pose``."""
    tun = tunables_bucket(entry)
    meas = measurables_bucket(entry)
    if pose is None:
        tun["reported_pose"] = None
        meas["pose"] = None
        return
    payload = dict(pose)
    tun["reported_pose"] = payload
    meas["pose"] = dict(payload)


def meas_pose(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Alias for :func:`reported_pose` (historical name; pose is not a measurable)."""
    return reported_pose(entry)


def nominal_pose(entry: Dict[str, Any]) -> Dict[str, Any]:
    np = get_tunables(entry).get("nominal_pose")
    if isinstance(np, dict):
        return dict(np)
    return {}


def resolve_pick_table_pose(entry: Dict[str, Any]) -> Dict[str, float]:
    """XY + rotation for PICK: reported pose when present, else tunables nominal."""
    mp = reported_pose(entry)
    if mp.get("x") is not None or mp.get("y") is not None:
        src = mp
    else:
        src = nominal_pose(entry)
    return {
        "x": float(src.get("x", 0.0)),
        "y": float(src.get("y", 0.0)),
        "rotation": float(src.get("rotation", 0.0)),
    }


def storage_slot(entry: Dict[str, Any]) -> Optional[Dict[str, int]]:
    sl = get_tunables(entry).get("storage") or {}
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
    tun = tunables_bucket(entry)
    tun["presence"] = presence
    s = tun.setdefault("storage", {"in_storage": False, "slot": None})
    s["in_storage"] = in_storage
    s["slot"] = slot


def placement_mode(entry: Dict[str, Any]) -> str:
    pl = get_tunables(entry).get("placement") or {}
    m = pl.get("mode")
    if isinstance(m, str) and m:
        return m.upper()
    return "MANUAL"


def is_teleop_active(entry: Dict[str, Any]) -> bool:
    return bool(teleop_bucket(entry).get("active"))


def is_teleop_ready(entry: Dict[str, Any]) -> bool:
    top = teleop_bucket(entry)
    return bool(top.get("active") and top.get("ready"))


def teleop_last_jog_ts(entry: Dict[str, Any]) -> Optional[float]:
    ts = teleop_bucket(entry).get("last_jog_ts")
    if isinstance(ts, (int, float)):
        return float(ts)
    return None


def teleop_command_phase(entry: Dict[str, Any]) -> str:
    cmd = teleop_bucket(entry).get("command") or {}
    phase = cmd.get("phase")
    return str(phase) if isinstance(phase, str) else "idle"


def is_teleop_executing(entry: Dict[str, Any]) -> bool:
    return teleop_command_phase(entry) == "executing"


def is_live_feed_active(entry: Dict[str, Any], channel: str = "stream") -> bool:
    ch = live_feed_channel(entry, channel)
    return bool(ch.get("connected") and ch.get("live"))


def normalize_components_map(components: Dict[str, Any]) -> None:
    if not isinstance(components, dict):
        return
    for entry in components.values():
        if isinstance(entry, dict):
            ensure_component_shape(entry)
