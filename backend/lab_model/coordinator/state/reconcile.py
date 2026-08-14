"""Plan primitive commands to reconcile one configuration to another."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Tuple

from lab_model.language.domain.component import PRESENCE_BREADBOARD, PRESENCE_STORAGE
from lab_model.language.primitives.ids import PrimitiveId

from .diff import configuration_diff


def _target_nominal_pose(
    target_cfg: Mapping[str, Any],
    tag_id: str,
) -> Dict[str, Any]:
    """Target ``tunables.nominal_pose`` for ``tag_id`` (empty dict if absent)."""
    comp = (target_cfg.get("components") or {}).get(tag_id) or {}
    tun = ((comp.get("statecontrol") or {}).get("tunables") or {})
    pose = tun.get("nominal_pose")
    return pose if isinstance(pose, dict) else {}


def _entry_tunables(cfg: Mapping[str, Any], tag_id: str) -> Dict[str, Any]:
    comp = (cfg.get("components") or {}).get(tag_id) or {}
    tun = (comp.get("statecontrol") or {}).get("tunables") or {}
    return tun if isinstance(tun, dict) else {}


def _target_tunables(target_cfg: Mapping[str, Any], tag_id: str) -> Dict[str, Any]:
    return _entry_tunables(target_cfg, tag_id)


def _cfg_presence(cfg: Mapping[str, Any], tag_id: str) -> str:
    tun = _entry_tunables(cfg, tag_id)
    presence = tun.get("presence", PRESENCE_BREADBOARD)
    if presence == PRESENCE_STORAGE:
        return PRESENCE_STORAGE
    storage = tun.get("storage") or {}
    if isinstance(storage, dict) and storage.get("in_storage"):
        return PRESENCE_STORAGE
    return PRESENCE_BREADBOARD


def _slot_ij(storage_val: Any) -> Optional[Tuple[int, int]]:
    if not isinstance(storage_val, dict):
        return None
    slot = storage_val.get("slot")
    if not isinstance(slot, dict):
        return None
    if slot.get("i") is None or slot.get("j") is None:
        return None
    try:
        return int(slot["i"]), int(slot["j"])
    except (TypeError, ValueError):
        return None


def _store_params_from_storage(storage_val: Any) -> Dict[str, Any]:
    """Optional explicit cell for STORE_COMPONENT (empty → edge autopack)."""
    ij = _slot_ij(storage_val)
    if ij is None:
        return {}
    return {"slot_i": ij[0], "slot_j": ij[1]}


def _store_params_for_tag(target_cfg: Mapping[str, Any], tag_id: str) -> Dict[str, Any]:
    return _store_params_from_storage(_target_tunables(target_cfg, tag_id).get("storage"))


def _place_extras(target_cfg: Mapping[str, Any], tag_id: str) -> List[Dict[str, Any]]:
    """Motor + exposure setpoints to fully realize a freshly placed component.

    A component added from storage is absent from the *current* config, so the
    per-field diff never emits its motor/exposure tunables; restore them here so
    the placed part matches its target node, not just its pose.
    """
    extras: List[Dict[str, Any]] = []
    tun = _target_tunables(target_cfg, tag_id)
    motors = tun.get("nominal_motor_positions")
    if isinstance(motors, dict):
        for motor_id, angle in motors.items():
            try:
                extras.append(
                    {
                        "action": PrimitiveId.SET_MOTOR_SETPOINT,
                        "target_id": tag_id,
                        "parameters": {
                            "motor_id": int(motor_id),
                            "angle_deg": float(angle),
                        },
                    }
                )
            except (TypeError, ValueError):
                continue
    exposure = tun.get("exposure_time_ms")
    if exposure is not None:
        try:
            extras.append(
                {
                    "action": PrimitiveId.SET_EXPOSURE,
                    "target_id": tag_id,
                    "parameters": {"exposure_time_ms": float(exposure)},
                }
            )
        except (TypeError, ValueError):
            pass
    return extras


def _pose_params(pose: Mapping[str, Any]) -> Dict[str, Any]:
    params: Dict[str, Any] = {
        "target_x": float(pose.get("x", 0.0)),
        "target_y": float(pose.get("y", 0.0)),
        "rotation": float(pose.get("rotation", 0.0)),
    }
    if pose.get("z") is not None:
        params["z"] = float(pose["z"])
    return params


def plan_reconcile(
    current_cfg: Mapping[str, Any],
    target_cfg: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    """Return command envelopes that move from ``current_cfg`` toward ``target_cfg``.

    Optimizer history is collapsed: only target tunables matter. Each envelope
    matches ``POST /api/command`` shape: ``action``, ``target_id``, ``parameters``.

    Storage inventory is versioned when present in the configs: STORE uses
    explicit ``slot_i``/``slot_j`` when known; same-slot pose drift recenters.
    """
    commands: List[Dict[str, Any]] = []
    changes = configuration_diff(current_cfg, target_cfg)

    # Tags whose presence is changing this reconcile: STORE_COMPONENT /
    # PLACE_FROM_STORAGE own the (re)positioning for these parts, so we must
    # suppress the standalone MOVE_COMPONENT / STORE / RECENTER that the
    # per-field loop would otherwise emit.
    presence_transition: Dict[str, Any] = {
        str(c.get("tag_id")): c.get("to")
        for c in changes
        if str(c.get("path") or "") == "tunables.presence"
        and c.get("tag_id") != "<holding>"
    }

    # Slot moves (same presence=storage) are owned by STORE with explicit cell.
    storage_slot_transition: set[str] = set()
    for c in changes:
        if str(c.get("path") or "") != "tunables.storage":
            continue
        tag = c.get("tag_id")
        if not isinstance(tag, str) or tag.startswith("<"):
            continue
        if tag in presence_transition:
            continue
        old_ij = _slot_ij(c.get("from"))
        new_ij = _slot_ij(c.get("to"))
        if new_ij is not None and new_ij != old_ij:
            storage_slot_transition.add(tag)

    for change in changes:
        tag_id = change.get("tag_id")
        path = str(change.get("path") or "")
        new_val = change.get("to")

        # Sentinel "tags" (<holding>, <guides>, <laser>) are not hardware:
        # holding intent is realized by the per-part primitives, and alignment
        # overlays (guides / laser lines) are pure UI geometry that require no
        # robot motion. Skip them all here.
        if isinstance(tag_id, str) and tag_id.startswith("<"):
            continue

        if path == "component":
            # Membership transitions across the full lab layout (table + storage).
            if new_val == "added":
                if _cfg_presence(target_cfg, str(tag_id)) == PRESENCE_STORAGE:
                    commands.append(
                        {
                            "action": PrimitiveId.STORE_COMPONENT,
                            "target_id": tag_id,
                            "parameters": _store_params_for_tag(target_cfg, str(tag_id)),
                        }
                    )
                else:
                    commands.append(
                        {
                            "action": PrimitiveId.PLACE_FROM_STORAGE,
                            "target_id": tag_id,
                            "parameters": _pose_params(
                                _target_nominal_pose(target_cfg, str(tag_id))
                            ),
                        }
                    )
                    commands.extend(_place_extras(target_cfg, str(tag_id)))
            elif change.get("from") == "present" and new_val is None:
                # Dropped from target entirely. Table → autopack store. Already
                # stored inventory not listed in the target (legacy nodes) stays
                # put — no hardware step.
                if _cfg_presence(current_cfg, str(tag_id)) == PRESENCE_STORAGE:
                    continue
                commands.append(
                    {
                        "action": PrimitiveId.STORE_COMPONENT,
                        "target_id": tag_id,
                        "parameters": {},
                    }
                )
            continue

        if path == "tunables.presence":
            if new_val == PRESENCE_STORAGE:
                commands.append(
                    {
                        "action": PrimitiveId.STORE_COMPONENT,
                        "target_id": tag_id,
                        "parameters": _store_params_for_tag(target_cfg, str(tag_id)),
                    }
                )
            elif new_val == PRESENCE_BREADBOARD:
                commands.append(
                    {
                        "action": PrimitiveId.PLACE_FROM_STORAGE,
                        "target_id": tag_id,
                        "parameters": _pose_params(
                            _target_nominal_pose(target_cfg, str(tag_id))
                        ),
                    }
                )
            continue

        if path == "tunables.exposure_time_ms" and new_val is not None:
            commands.append(
                {
                    "action": PrimitiveId.SET_EXPOSURE,
                    "target_id": tag_id,
                    "parameters": {"exposure_time_ms": float(new_val)},
                }
            )
            continue

        if path == "tunables.nominal_motor_positions" and isinstance(new_val, dict):
            old_val = change.get("from")
            old_map = old_val if isinstance(old_val, dict) else {}
            for motor_id, angle in new_val.items():
                if old_map.get(motor_id) == angle:
                    continue
                commands.append(
                    {
                        "action": PrimitiveId.SET_MOTOR_SETPOINT,
                        "target_id": tag_id,
                        "parameters": {
                            "motor_id": int(motor_id),
                            "angle_deg": float(angle),
                        },
                    }
                )
            continue

        if path == "tunables.nominal_pose" and isinstance(new_val, dict):
            # Presence / slot transitions already carry destination pose.
            # Stored inventory is slot-only in VC — never MOVE/RECENTER from a
            # pose field on a storage entry (legacy commits may still embed one).
            if tag_id in presence_transition or tag_id in storage_slot_transition:
                continue
            if _cfg_presence(target_cfg, str(tag_id)) == PRESENCE_STORAGE:
                continue
            commands.append(
                {
                    "action": PrimitiveId.MOVE_COMPONENT,
                    "target_id": tag_id,
                    "parameters": _pose_params(new_val),
                }
            )
            continue

        if path == "tunables.storage" and isinstance(new_val, dict):
            if tag_id in presence_transition:
                continue
            if not new_val.get("in_storage"):
                continue
            # Same storage presence, new cell (or first-time slot assignment).
            old_ij = _slot_ij(change.get("from"))
            new_ij = _slot_ij(new_val)
            if new_ij is not None and new_ij != old_ij:
                commands.append(
                    {
                        "action": PrimitiveId.STORE_COMPONENT,
                        "target_id": tag_id,
                        "parameters": _store_params_from_storage(new_val),
                    }
                )
            continue

    return commands
