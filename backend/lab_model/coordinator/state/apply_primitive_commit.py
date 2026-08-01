"""Apply language ``commit_*`` after successful remote (HTTP/poll) southbound.

In-process mock already commits inside orchestration — skip that transport.
See ``docs/BACKEND_ISOLATION.md`` Phase 3 / Phase 4.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Set

from lab_model.coordinator.state.commits import (
    commit_affirm_placed,
    commit_hover,
    commit_move_to_breadboard,
    commit_move_to_storage,
    commit_pick,
    commit_place_from_hover,
    commit_scan_rotation,
)
from lab_model.coordinator.state.lab_state_store import LabStateStore
from lab_model.coordinator.state.runtime_manager import MutationKind
from lab_model.coordinator.state.snapshot import LabPose
from lab_model.language.domain.component import (
    PRESENCE_STORAGE,
    resolve_pick_table_pose,
)
from lab_model.language.domain.holding import (
    DEFAULT_HOVER_Z_MM,
    confirm_holding_tag,
)
from lab_model.language.primitives.dispatch import RECIPE_ACTION_ALIASES

#: Primitives that update Twin FSM / presence / pose after remote execute.
REMOTE_COMMIT_ACTIONS: Set[str] = {
    "PICK_COMPONENT",
    "HOVER",
    "PLACE_FROM_HOVER",
    "CONFIRM_HOLDING_TAG",
    "MOVE_COMPONENT",
    "STORE_COMPONENT",
    "PLACE_FROM_STORAGE",
    "AFFIRM_PLACED_AT_CURRENT",
    "SCAN_ROTATE_IN_PLACE",
}

# Back-compat alias (Phase 3 name).
IN_AIR_COMMIT_ACTIONS = REMOTE_COMMIT_ACTIONS


def canonical_action(command: Mapping[str, Any]) -> str:
    raw = str(command.get("action") or command.get("primitive") or "").strip()
    if not raw:
        return ""
    return RECIPE_ACTION_ALIASES.get(raw, raw)


def _params(command: Mapping[str, Any]) -> Dict[str, Any]:
    params = command.get("parameters")
    if isinstance(params, dict):
        return dict(params)
    if hasattr(params, "model_dump"):
        try:
            return dict(params.model_dump(exclude_none=True))
        except Exception:  # noqa: BLE001
            return {}
    return {}


def resolve_tag_id(
    command: Mapping[str, Any],
    edge_result: Optional[Mapping[str, Any]] = None,
) -> str:
    params = _params(command)
    for key in ("target_id", "tag_id"):
        val = command.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
        val = params.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    if isinstance(edge_result, Mapping):
        for key in ("tag_id", "held_tag_id", "target_id"):
            val = edge_result.get(key)
            if val is not None and str(val).strip():
                return str(val).strip()
    return ""


def _float_from(mapping: Mapping[str, Any], *keys: str, default: float = 0.0) -> float:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            try:
                return float(mapping[key])
            except (TypeError, ValueError):
                continue
    return float(default)


def _optional_int(mapping: Mapping[str, Any], *keys: str) -> Optional[int]:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            try:
                return int(mapping[key])
            except (TypeError, ValueError):
                continue
    return None


def _pose_xyr(
    params: Mapping[str, Any],
    edge_result: Mapping[str, Any],
) -> tuple[float, float, float]:
    pose = edge_result.get("pose") if isinstance(edge_result.get("pose"), Mapping) else {}
    x = _float_from(params, "target_x", "x", default=_float_from(pose, "x"))
    y = _float_from(params, "target_y", "y", default=_float_from(pose, "y"))
    rotation = _float_from(
        params, "rotation", default=_float_from(pose, "rotation")
    )
    return x, y, rotation


def _actual_pose_from_edge(
    edge_result: Mapping[str, Any],
    *,
    x: float,
    y: float,
    rotation: float,
) -> Optional[LabPose]:
    pose = edge_result.get("pose") if isinstance(edge_result.get("pose"), Mapping) else {}
    if not pose:
        return None
    return LabPose(
        x=_float_from(pose, "x", default=x),
        y=_float_from(pose, "y", default=y),
        z=_float_from(pose, "z", default=0.0),
        rotation=_float_from(pose, "rotation", default=rotation),
    )


def _wants_storage(
    params: Mapping[str, Any],
    edge_result: Mapping[str, Any],
) -> bool:
    presence = str(edge_result.get("presence") or params.get("presence") or "").strip().lower()
    if presence in (PRESENCE_STORAGE, "storage"):
        return True
    mode = str(
        params.get("placement_mode")
        or edge_result.get("placement_mode")
        or ""
    ).strip().upper()
    if mode == "STORAGE":
        return True
    slot_i = _optional_int(edge_result, "slot_i", "i")
    if slot_i is None:
        slot_i = _optional_int(params, "slot_i", "i")
    slot_j = _optional_int(edge_result, "slot_j", "j")
    if slot_j is None:
        slot_j = _optional_int(params, "slot_j", "j")
    return slot_i is not None and slot_j is not None


def apply_remote_commit(
    state: Dict[str, Any],
    action: str,
    command: Mapping[str, Any],
    edge_result: Optional[Mapping[str, Any]] = None,
) -> bool:
    """Mutate ``state`` in place. Returns True when a commit was applied."""
    result = edge_result if isinstance(edge_result, Mapping) else {}
    tag_id = resolve_tag_id(command, result)
    if not tag_id:
        print(
            f"[lab_state] source=edge_commit action={action!r} skipped: missing tag_id",
            flush=True,
        )
        return False

    params = _params(command)
    pose_from_edge = result.get("pose") if isinstance(result.get("pose"), Mapping) else {}

    if action == "PICK_COMPONENT":
        if result.get("holding") is False:
            print(
                f"[lab_state] source=edge_commit action=PICK_COMPONENT "
                f"tag={tag_id!r} skipped: holding=false",
                flush=True,
            )
            return False
        entry = (state.get("components") or {}).get(tag_id) or {}
        pick_xy = resolve_pick_table_pose(entry if isinstance(entry, dict) else {})
        settled_z = _float_from(
            pose_from_edge,
            "z",
            default=_float_from(result, "settled_z", default=DEFAULT_HOVER_Z_MM),
        )
        commit_pick(
            state,
            tag_id,
            x=float(pick_xy["x"]),
            y=float(pick_xy["y"]),
            rotation=float(pick_xy["rotation"]),
            settled_z=settled_z,
        )
        return True

    if action == "HOVER":
        x = _float_from(params, "target_x", "x", default=_float_from(pose_from_edge, "x"))
        y = _float_from(params, "target_y", "y", default=_float_from(pose_from_edge, "y"))
        rotation = _float_from(
            params, "rotation", default=_float_from(pose_from_edge, "rotation")
        )
        z = _float_from(
            params,
            "z",
            "target_z",
            default=_float_from(pose_from_edge, "z", default=DEFAULT_HOVER_Z_MM),
        )
        actual: Optional[LabPose] = None
        if pose_from_edge:
            actual = LabPose(
                x=_float_from(pose_from_edge, "x", default=x),
                y=_float_from(pose_from_edge, "y", default=y),
                z=_float_from(pose_from_edge, "z", default=z),
                rotation=_float_from(pose_from_edge, "rotation", default=rotation),
            )
        commit_hover(
            state,
            tag_id,
            x=x,
            y=y,
            rotation=rotation,
            z=z,
            actual_pose=actual,
        )
        return True

    if action == "PLACE_FROM_HOVER":
        x = _float_from(params, "target_x", "x", default=_float_from(pose_from_edge, "x"))
        y = _float_from(params, "target_y", "y", default=_float_from(pose_from_edge, "y"))
        rotation = _float_from(
            params, "rotation", default=_float_from(pose_from_edge, "rotation")
        )
        commit_place_from_hover(state, tag_id, x=x, y=y, rotation=rotation)
        return True

    if action == "CONFIRM_HOLDING_TAG":
        if result.get("holding") is False:
            print(
                f"[lab_state] source=edge_commit action=CONFIRM_HOLDING_TAG "
                f"tag={tag_id!r} skipped: holding=false",
                flush=True,
            )
            return False
        confirm_holding_tag(state, tag_id)
        return True

    if action in ("MOVE_COMPONENT", "PLACE_FROM_STORAGE", "STORE_COMPONENT"):
        x, y, rotation = _pose_xyr(params, result)
        actual = _actual_pose_from_edge(result, x=x, y=y, rotation=rotation)
        to_storage = action == "STORE_COMPONENT" or (
            action == "MOVE_COMPONENT" and _wants_storage(params, result)
        )
        if to_storage:
            slot_i = _optional_int(result, "slot_i", "i")
            if slot_i is None:
                slot_i = _optional_int(params, "slot_i", "i")
            slot_j = _optional_int(result, "slot_j", "j")
            if slot_j is None:
                slot_j = _optional_int(params, "slot_j", "j")
            if slot_i is None or slot_j is None:
                print(
                    f"[lab_state] source=edge_commit action={action!r} "
                    f"tag={tag_id!r} skipped: missing slot_i/slot_j",
                    flush=True,
                )
                return False
            commit_move_to_storage(
                state,
                tag_id,
                x=x,
                y=y,
                rotation=rotation,
                slot_i=slot_i,
                slot_j=slot_j,
                actual_pose=actual,
            )
            return True
        commit_move_to_breadboard(
            state,
            tag_id,
            x=x,
            y=y,
            rotation=rotation,
            actual_pose=actual,
        )
        return True

    if action == "AFFIRM_PLACED_AT_CURRENT":
        commit_affirm_placed(state, tag_id)
        return True

    if action == "SCAN_ROTATE_IN_PLACE":
        mode = str(params.get("mode") or result.get("mode") or "placed").strip().lower()
        if mode not in ("held", "placed"):
            mode = "placed"
        x, y, rotation = _pose_xyr(params, result)
        z_val = None
        if mode == "held":
            z_raw = params.get("z", result.get("z"))
            if z_raw is None and isinstance(pose_from_edge, Mapping):
                z_raw = pose_from_edge.get("z")
            if z_raw is not None:
                try:
                    z_val = float(z_raw)
                except (TypeError, ValueError):
                    z_val = None
        commit_scan_rotation(
            state,
            tag_id,
            mode=mode,
            x=x,
            y=y,
            rotation=rotation,
            z=z_val,
        )
        return True

    return False


# Back-compat name used by older tests / imports.
apply_in_air_commit = apply_remote_commit


def apply_edge_primitive_commit(
    store: LabStateStore,
    command: Mapping[str, Any],
    *,
    edge_result: Optional[Mapping[str, Any]] = None,
    backend_id: str = "",
) -> bool:
    """Commit into the coordinator working store after a successful remote execute."""
    action = canonical_action(command)
    if action not in REMOTE_COMMIT_ACTIONS:
        return False

    applied = {"ok": False}

    def _mutate(state: Dict[str, Any]) -> None:
        applied["ok"] = apply_remote_commit(state, action, command, edge_result)

    store.mutate(
        _mutate,
        kind=MutationKind.PRIMITIVE_COMMIT,
        source=f"edge:{action}",
    )
    if applied["ok"]:
        tag = resolve_tag_id(command, edge_result)
        status = (store.snapshot() or {}).get("system_status")
        print(
            f"[lab_state] backend={backend_id or store.backend_id!r} "
            f"source=coordinator_commit action={action} tag={tag!r} "
            f"system_status={status!r}",
            flush=True,
        )
    return bool(applied["ok"])


__all__ = [
    "IN_AIR_COMMIT_ACTIONS",
    "REMOTE_COMMIT_ACTIONS",
    "apply_edge_primitive_commit",
    "apply_in_air_commit",
    "apply_remote_commit",
    "canonical_action",
    "resolve_tag_id",
]
