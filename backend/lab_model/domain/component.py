"""
Per-component shape: ``statecontrol`` (formal intent/observe) + ``telemetry`` (live sessions).

StateControl:
  - tunables — commanded intent (nominal pose, exposure, storage, …)
  - measurables — recorded observations (pose, camera_image, scores, …)

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


def _strip_legacy_teleop_from_tunables(tun: Dict[str, Any]) -> tuple[bool, Optional[float]]:
    active = bool(tun.pop("teleop_active", False))
    ts = tun.pop("teleop_last_jog_ts", None)
    last_jog = float(ts) if isinstance(ts, (int, float)) else None
    return active, last_jog


def ensure_component_shape(entry: Dict[str, Any]) -> None:
    """Normalize legacy flat ``tunables``/``measurables`` into statecontrol + telemetry."""
    if not isinstance(entry, dict):
        return

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
    tel = default_telemetry()
    tel["teleop"]["active"] = teleop_active
    tel["teleop"]["ready"] = teleop_active
    tel["teleop"]["last_jog_ts"] = teleop_last_jog_ts
    if teleop_active and teleop_last_jog_ts is not None:
        tel["teleop"]["lease_ts"] = float(teleop_last_jog_ts)
    entry["telemetry"] = tel


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
) -> Dict[str, Any]:
    tun = default_tunables()
    tun["presence"] = presence
    tun["nominal_pose"] = dict(nominal_pose)
    tun["storage"] = {"in_storage": in_storage, "slot": slot}
    tun["placement"] = {"mode": placement_mode}
    meas = default_measurables()
    meas["pose"] = dict(meas_pose)
    return {
        "id": tag_id,
        "type": comp_type,
        "statecontrol": {"tunables": tun, "measurables": meas},
        "telemetry": default_telemetry(),
    }


def get_tunables(entry: Dict[str, Any]) -> Dict[str, Any]:
    return dict(tunables_bucket(entry))


def get_measurables(entry: Dict[str, Any]) -> Dict[str, Any]:
    return dict(measurables_bucket(entry))


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


def meas_pose(entry: Dict[str, Any]) -> Dict[str, Any]:
    return dict(get_measurables(entry).get("pose") or {})


def nominal_pose(entry: Dict[str, Any]) -> Dict[str, Any]:
    np = get_tunables(entry).get("nominal_pose")
    if isinstance(np, dict):
        return dict(np)
    return {}


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
