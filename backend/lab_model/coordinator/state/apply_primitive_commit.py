"""Apply language ``commit_*`` after successful remote (HTTP/poll) southbound.

In-process mock already commits inside orchestration — skip that transport.
See ``docs/BACKEND_ISOLATION.md`` Phase 3 / Phase 4.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Mapping, Optional, Set

from lab_model.coordinator.state.commits import (
    commit_affirm_placed,
    commit_hover,
    commit_live_exposure,
    commit_live_feed_end,
    commit_live_feed_start,
    commit_move_to_breadboard,
    commit_move_to_storage,
    commit_observed_measurables,
    commit_pick,
    commit_place_from_hover,
    commit_teleop_end,
    commit_teleop_ready,
    commit_teleop_session_pose,
    commit_teleop_start,
    commit_teleop_start_failed,
    end_all_live_feed_channels,
)
from lab_model.coordinator.state.motor_state import set_nominal_motor_angle
from lab_model.coordinator.state.lab_state_store import LabStateStore
from lab_model.coordinator.state.runtime_manager import MutationKind
from lab_model.coordinator.state.snapshot import LabPose
from lab_model.language.domain import motor_rotation_store as motor_rot
from lab_model.language.domain.component import (
    PRESENCE_STORAGE,
    resolve_pick_table_pose,
    tunables_bucket,
)
from lab_model.language.domain.holding import (
    DEFAULT_HOVER_Z_MM,
    confirm_holding_tag,
)
from lab_model.language.primitives.dispatch import RECIPE_ACTION_ALIASES

#: Primitives that update Twin FSM / presence / pose / commanded tunables after remote execute.
REMOTE_COMMIT_ACTIONS: Set[str] = {
    "PICK_COMPONENT",
    "HOVER",
    "PLACE_FROM_HOVER",
    "CONFIRM_HOLDING_TAG",
    "MOVE_COMPONENT",
    "STORE_COMPONENT",
    "PLACE_FROM_STORAGE",
    # Storage tidy: edge returns cell-center pose + slots; Twin must mirror or UI stays off-center.
    "RECENTER_IN_STORAGE",
    "REPACK_STORAGE",
    "AFFIRM_PLACED_AT_CURRENT",
    "RECORD_MEASURABLES",
    "START_TELEOP",
    "END_TELEOP",
    "START_LIVE_FEED",
    "END_LIVE_FEED",
    # Preview VEXP only — does not write science tunables.exposure_time_ms.
    "SET_LIVE_EXPOSURE",
    # Commanded tunables: edge applies hardware; Twin store must mirror so UI / OPTIMIZE
    # hold-for-measure see the committed value (merge does not overlay edge tunables).
    "SET_EXPOSURE",
    "SET_LASER_OUTPUT",
    "MOVE_MOTOR",
    "SET_MOTOR_SETPOINT",
    "MOTOR_SEND_HOME",
    "MOTOR_SET_ZERO",
    # Ceiling / world overwrite → Twin commanded pose (Refresh Pose / SYNC RECORD half).
    "RECORD_TUNABLES",
    "LOCALIZE_COMPONENTS",
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


def _persist_motor_angle(tag_id: str, motor_id: int, angle_deg: float) -> None:
    """Mirror angle into motor_rotation_store when the process has configured it."""
    try:
        motor_rot.set_rotations_for_tag(tag_id, {str(int(motor_id)): float(angle_deg)})
    except RuntimeError:
        # Unit tests / hosts that never called motor_rot.configure — lab-state only.
        return


def _commit_motor_nominal(
    state: Dict[str, Any],
    tag_id: str,
    motor_id: int,
    angle_deg: float,
) -> bool:
    entry = (state.get("components") or {}).get(tag_id)
    if not isinstance(entry, dict):
        return False
    set_nominal_motor_angle(entry, motor_id, float(angle_deg))
    _persist_motor_angle(tag_id, motor_id, float(angle_deg))
    return True


def _resolve_motor_id(
    result: Mapping[str, Any], params: Mapping[str, Any]
) -> Optional[int]:
    return _optional_int(result, "motor_id") or _optional_int(params, "motor_id") or 1


def _resolve_post_move_angle(
    action: str,
    result: Mapping[str, Any],
    params: Mapping[str, Any],
    tag_id: str,
    motor_id: int,
    *,
    state: Optional[Mapping[str, Any]] = None,
) -> Optional[float]:
    """Absolute angle after a motor primitive, preferring edge-reported values."""
    if action == "MOTOR_SET_ZERO":
        return 0.0
    if action == "MOTOR_SEND_HOME":
        if result.get("angle_deg") is not None:
            try:
                return float(result["angle_deg"])
            except (TypeError, ValueError):
                return 0.0
        return 0.0
    if action == "SET_MOTOR_SETPOINT":
        raw = result.get("angle_deg")
        if raw is None:
            raw = params.get("angle_deg")
        if raw is None:
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None
    if action == "MOVE_MOTOR":
        # Degree-native edges (K10CR2) return absolute ``angle_deg`` after the move.
        if result.get("angle_deg") is not None:
            try:
                return float(result["angle_deg"])
            except (TypeError, ValueError):
                pass
        delta = result.get("distance_deg")
        if delta is None and result.get("steps") is not None:
            delta = result.get("steps")
        if delta is None:
            delta = params.get("distance")
        if delta is None:
            delta = params.get("steps")
        if delta is None:
            delta = params.get("angle_deg")
        if delta is None:
            return None
        try:
            d = float(delta)
        except (TypeError, ValueError):
            return None
        cur = 0.0
        try:
            cur = float(motor_rot.get_angle(tag_id, motor_id))
        except RuntimeError:
            if isinstance(state, Mapping):
                entry = (state.get("components") or {}).get(tag_id)
                if isinstance(entry, dict):
                    nmp = tunables_bucket(entry).get("nominal_motor_positions") or {}
                    if isinstance(nmp, dict) and nmp.get(str(motor_id)) is not None:
                        try:
                            cur = float(nmp[str(motor_id)])
                        except (TypeError, ValueError):
                            cur = 0.0
        return cur + d
    return None


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
    nested_pose = edge_result.get("pose")
    pose = nested_pose if isinstance(nested_pose, Mapping) else edge_result
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
    nested_pose = edge_result.get("pose")
    if isinstance(nested_pose, Mapping):
        pose = nested_pose
    elif any(key in edge_result for key in ("x", "y", "rotation")):
        pose = edge_result
    else:
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
    # RECORD / LOCALIZE can be multi-tag (parameters.tag_ids) with no target_id.
    if action in ("RECORD_TUNABLES", "LOCALIZE_COMPONENTS"):
        return _commit_record_tunables(state, command, result)

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

    if action in (
        "MOVE_COMPONENT",
        "PLACE_FROM_STORAGE",
        "STORE_COMPONENT",
        "RECENTER_IN_STORAGE",
        "REPACK_STORAGE",
    ):
        x, y, rotation = _pose_xyr(params, result)
        actual = _actual_pose_from_edge(result, x=x, y=y, rotation=rotation)
        to_storage = action in (
            "STORE_COMPONENT",
            "RECENTER_IN_STORAGE",
            "REPACK_STORAGE",
        ) or (action == "MOVE_COMPONENT" and _wants_storage(params, result))
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

    if action == "RECORD_MEASURABLES":
        nested = result.get("measurables")
        if isinstance(nested, dict) and nested:
            patch = dict(nested)
        elif result.get("path"):
            patch = {
                "camera_image": {
                    "path": str(result["path"]),
                    "source": str(result.get("source") or ""),
                    "cam_id": int(result.get("cam_id") or 0),
                    "format": str(result.get("format") or "png"),
                }
            }
        else:
            print(
                f"[lab_state] source=edge_commit action=RECORD_MEASURABLES "
                f"tag={tag_id!r} skipped: no measurables in edge result "
                f"keys={sorted(result.keys())}",
                flush=True,
            )
            return False
        commit_observed_measurables(state, tag_id, patch)
        fields = sorted(str(k) for k in patch.keys())
        print(
            f"[lab_state] source=edge_commit action=RECORD_MEASURABLES "
            f"tag={tag_id!r} fields={fields}",
            flush=True,
        )
        return True

    if action == "START_TELEOP":
        # Remote edge START can take seconds (arm grab). Phases mirror mock:
        # pending → ready | failed. Default (no phase) = start+ready for tests.
        phase = str(
            command.get("_teleop_phase")
            or params.get("_teleop_phase")
            or "start_ready"
        ).strip().lower()
        now_ms = float(time.time() * 1000.0)
        if phase == "pending":
            if not commit_teleop_start(state, tag_id, now_ms=now_ms):
                print(
                    f"[lab_state] source=edge_commit action=START_TELEOP "
                    f"phase=pending tag={tag_id!r} skipped: commit_teleop_start failed",
                    flush=True,
                )
                return False
            return True
        if phase == "failed":
            err = result.get("error") or params.get("error") or "teleop setup failed"
            commit_teleop_start_failed(state, tag_id, error=str(err))
            return True
        if phase == "ready":
            if not commit_teleop_ready(state, tag_id, now_ms=now_ms):
                # Pending was lost (restart / race) — promote in one step.
                if not commit_teleop_start(state, tag_id, now_ms=now_ms):
                    return False
                commit_teleop_ready(state, tag_id, now_ms=now_ms)
            return True
        # Legacy / unit-test: edge already finished → active+ready together.
        if not commit_teleop_start(state, tag_id, now_ms=now_ms):
            print(
                f"[lab_state] source=edge_commit action=START_TELEOP "
                f"tag={tag_id!r} skipped: commit_teleop_start failed",
                flush=True,
            )
            return False
        commit_teleop_ready(state, tag_id, now_ms=now_ms)
        return True

    if action == "END_TELEOP":
        # Keep where teleop ended: commit final live pose into tunables before
        # clearing the lease (HTTP edges used to drop this and Twin snapped back).
        pose = None
        for key in ("final_pose", "pose"):
            cand = result.get(key)
            if isinstance(cand, dict) and cand:
                pose = cand
                break
        if pose is None:
            nested = result.get("telemetry")
            if isinstance(nested, dict):
                for key in ("final_pose", "pose"):
                    cand = nested.get(key)
                    if isinstance(cand, dict) and cand:
                        pose = cand
                        break
        if isinstance(pose, dict) and pose:
            commit_teleop_session_pose(state, tag_id, pose)
        commit_teleop_end(state, tag_id)
        return True

    if action == "START_LIVE_FEED":
        # Twin catalog channel is always ``stream`` (JPEGPoll / MJPEGViewer gate).
        # Edge execute may have remapped args to ``{tag}.camera_image``.
        twin_channel = str(
            command.get("channel") or params.get("channel") or "stream"
        ).strip()
        if twin_channel in ("", "all") or twin_channel.endswith(".camera_image"):
            twin_channel = "stream"
        backend = str(
            result.get("backend")
            or params.get("backend")
            or "edge"
        )
        resource_id = result.get("resource_id") or result.get("channel") or tag_id
        live_exp = result.get("live_exposure_time_ms")
        if live_exp is None:
            live_exp = params.get("exposure_time_ms")
        commit_live_feed_start(
            state,
            tag_id,
            channel=twin_channel,
            backend=backend,
            resource_id=str(resource_id) if resource_id is not None else tag_id,
            live_exposure_time_ms=(
                float(live_exp) if live_exp is not None else None
            ),
        )
        return True

    if action == "END_LIVE_FEED":
        twin_channel = str(
            command.get("channel") or params.get("channel") or "all"
        ).strip()
        if twin_channel.endswith(".camera_image"):
            twin_channel = "stream"
        if twin_channel in ("", "all"):
            end_all_live_feed_channels(state, tag_id)
        else:
            commit_live_feed_end(state, tag_id, channel=twin_channel)
        return True

    if action == "SET_LIVE_EXPOSURE":
        exp = result.get("live_exposure_time_ms")
        if exp is None:
            exp = result.get("exposure_time_ms")
        if exp is None:
            exp = params.get("exposure_time_ms")
        if exp is None:
            print(
                f"[lab_state] source=edge_commit action=SET_LIVE_EXPOSURE "
                f"tag={tag_id!r} skipped: missing exposure_time_ms",
                flush=True,
            )
            return False
        twin_channel = str(
            command.get("channel") or params.get("channel") or "stream"
        ).strip()
        if twin_channel in ("", "all") or twin_channel.endswith(".camera_image"):
            twin_channel = "stream"
        commit_live_exposure(
            state,
            tag_id,
            channel=twin_channel,
            exposure_time_ms=float(exp),
        )
        return True

    if action == "SET_EXPOSURE":
        exp = result.get("exposure_time_ms")
        if exp is None:
            exp = params.get("exposure_time_ms")
        if exp is None:
            print(
                f"[lab_state] source=edge_commit action=SET_EXPOSURE "
                f"tag={tag_id!r} skipped: missing exposure_time_ms",
                flush=True,
            )
            return False
        entry = (state.get("components") or {}).get(tag_id)
        if not isinstance(entry, dict):
            return False
        tunables_bucket(entry)["exposure_time_ms"] = float(exp)
        return True

    if action == "SET_LASER_OUTPUT":
        power = result.get("output_power_mw")
        if power is None:
            power = params.get("output_power_mw")
        if power is None:
            print(
                f"[lab_state] source=edge_commit action=SET_LASER_OUTPUT "
                f"tag={tag_id!r} skipped: missing output_power_mw",
                flush=True,
            )
            return False
        entry = (state.get("components") or {}).get(tag_id)
        if not isinstance(entry, dict):
            return False
        tunables_bucket(entry)["output_power_mw"] = float(power)
        return True

    if action in (
        "MOVE_MOTOR",
        "SET_MOTOR_SETPOINT",
        "MOTOR_SEND_HOME",
        "MOTOR_SET_ZERO",
    ):
        motor_id = _resolve_motor_id(result, params)
        if motor_id is None:
            print(
                f"[lab_state] source=edge_commit action={action} "
                f"tag={tag_id!r} skipped: missing motor_id",
                flush=True,
            )
            return False
        angle = _resolve_post_move_angle(
            action, result, params, tag_id, int(motor_id), state=state
        )
        if angle is None:
            print(
                f"[lab_state] source=edge_commit action={action} "
                f"tag={tag_id!r} motor={motor_id} skipped: could not resolve angle",
                flush=True,
            )
            return False
        return _commit_motor_nominal(state, tag_id, int(motor_id), float(angle))

    return False


def _commit_record_tunables(
    state: Dict[str, Any],
    command: Mapping[str, Any],
    result: Mapping[str, Any],
) -> bool:
    """Write edge RECORD results into Twin commanded tunables (checked tags only).

    Edge result shape::
        values: { tag_id: { "nominal_pose": {...}, ... } }
        poses:  { tag_id: {...} }   # convenience mirror of nominal_pose
    """
    params = _params(command)
    values = result.get("values") if isinstance(result.get("values"), Mapping) else {}
    poses = result.get("poses") if isinstance(result.get("poses"), Mapping) else {}

    requested = params.get("tag_ids")
    if isinstance(requested, list) and requested:
        tag_ids = [str(t).strip() for t in requested if str(t).strip()]
    else:
        tag_ids = sorted(
            {
                str(t)
                for t in list(values.keys()) + list(poses.keys())
                if str(t).strip()
            }
        )

    applied = False
    for tid in tag_ids:
        entry = (state.get("components") or {}).get(tid)
        if not isinstance(entry, dict):
            continue
        per = values.get(tid) if isinstance(values.get(tid), Mapping) else {}
        pose = per.get("nominal_pose") if isinstance(per, Mapping) else None
        if not isinstance(pose, Mapping):
            pose = poses.get(tid)
        if isinstance(pose, Mapping):
            try:
                written = {
                    "x": float(pose.get("x", 0.0)),
                    "y": float(pose.get("y", 0.0)),
                    "rotation": float(pose.get("rotation", 0.0)),
                }
                if pose.get("z") is not None:
                    written["z"] = float(pose.get("z"))
            except (TypeError, ValueError):
                written = None
            if written is not None:
                tunables_bucket(entry)["nominal_pose"] = written
                applied = True
                print(
                    f"[lab_state] source=edge_commit action=RECORD_TUNABLES "
                    f"tag={tid!r} path=nominal_pose ok",
                    flush=True,
                )

        if isinstance(per, Mapping):
            for path, raw in per.items():
                if path == "nominal_pose" or raw is None:
                    continue
                if path == "exposure_time_ms":
                    try:
                        tunables_bucket(entry)[path] = float(raw)
                        applied = True
                    except (TypeError, ValueError):
                        pass
                elif path == "nominal_motor_positions" and isinstance(raw, Mapping):
                    nmp = tunables_bucket(entry).setdefault("nominal_motor_positions", {})
                    if not isinstance(nmp, dict):
                        nmp = {}
                        tunables_bucket(entry)["nominal_motor_positions"] = nmp
                    for mid, ang in raw.items():
                        try:
                            nmp[str(mid)] = float(ang)
                            applied = True
                        except (TypeError, ValueError):
                            continue

    if not applied:
        print(
            "[lab_state] source=edge_commit action=RECORD_TUNABLES "
            "skipped: no poses/values applied",
            flush=True,
        )
    return applied


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
