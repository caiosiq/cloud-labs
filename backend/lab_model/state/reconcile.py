"""Plan primitive commands to reconcile one configuration to another."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping

from lab_model.domain.component import PRESENCE_BREADBOARD, PRESENCE_STORAGE
from lab_model.primitives.ids import PrimitiveId

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


def _target_tunables(target_cfg: Mapping[str, Any], tag_id: str) -> Dict[str, Any]:
    comp = (target_cfg.get("components") or {}).get(tag_id) or {}
    tun = (comp.get("statecontrol") or {}).get("tunables") or {}
    return tun if isinstance(tun, dict) else {}


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
    """
    commands: List[Dict[str, Any]] = []
    changes = configuration_diff(current_cfg, target_cfg)

    # Tags whose presence is changing this reconcile: STORE_COMPONENT /
    # PLACE_FROM_STORAGE own the (re)positioning for these parts, so we must
    # suppress the standalone MOVE_COMPONENT / STORE that the per-field loop
    # would otherwise emit (a MOVE on a STORED part is refused, and an empty
    # PLACE_FROM_STORAGE fails schema validation).
    presence_transition: Dict[str, Any] = {
        str(c.get("tag_id")): c.get("to")
        for c in changes
        if str(c.get("path") or "") == "tunables.presence"
        and c.get("tag_id") != "<holding>"
    }

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
            # Membership transitions. A component that appears in the target but
            # not the current config must be brought onto the table from storage
            # (PLACE_FROM_STORAGE; the executor errors if it is not in storage).
            # A component dropped from the target is packed back into storage
            # (STORE_COMPONENT). This is how version control realizes "add" /
            # "remove" component across commits and across repos.
            if new_val == "added":
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
                # STORE_COMPONENT packs the part into a free inventory slot.
                commands.append(
                    {
                        "action": PrimitiveId.STORE_COMPONENT,
                        "target_id": tag_id,
                        "parameters": {},
                    }
                )
            elif new_val == PRESENCE_BREADBOARD:
                # PLACE_FROM_STORAGE needs the destination breadboard pose.
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
            # A part whose presence is changing is (re)placed by STORE /
            # PLACE_FROM_STORAGE, which already carries the destination pose —
            # skip the standalone move to avoid a refusal / double placement.
            if tag_id in presence_transition:
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
            # Presence-driven STORE already handled this part; only emit a
            # storage-driven STORE when presence itself did not change.
            if tag_id in presence_transition:
                continue
            if new_val.get("in_storage"):
                commands.append(
                    {
                        "action": PrimitiveId.STORE_COMPONENT,
                        "target_id": tag_id,
                        "parameters": {},
                    }
                )
            continue

    return commands
