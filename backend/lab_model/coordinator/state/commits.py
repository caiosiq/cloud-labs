"""Per-primitive commit helpers (lab-state writes).

When an in-air primitive (``pick_component``, ``hover_component``,
``place_from_hover``) finishes its hardware step, the orchestrator
commits the result to ``current_state``. The shape of that commit is
identical between the real and mock backends; this module owns the
merge logic so neither subclass repeats it.

Each helper takes the ``state`` dict by reference and mutates it in
place. The orchestrator wraps the call inside ``self._state_lock`` and
follows up with ``self._persist_state()``; helpers do not acquire
locks of their own and do not perform I/O.

Design rule (``communicator_refactor.md`` §6 / §7.2 abstraction 2):
each helper takes the *commanded* pose and an *optional* "actually
achieved" override. ``None`` means the orchestrator should commit the
commanded pose verbatim; a non-None ``LabPose`` (from a hook return
value) wins for ``measurables.pose`` so mock can surface its noise
simulation. Tunables / holding always reflect the commanded pose --
that is the operator's intent and what other parts of the UI key off.

Architectural rule (``communicator_refactor.md`` §5.1): pure stdlib
+ ``lab_model`` only. No ``lab_automation``, no
:mod:`lab_communicator.base`, no per-backend helpers.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from lab_model.language.domain.component import (
    PLACEMENT_MODE_HOVER,
    PLACEMENT_MODE_MANUAL,
    PLACEMENT_MODE_PICK,
    PLACEMENT_MODE_STORAGE,
    PRESENCE_BREADBOARD,
    PRESENCE_STORAGE,
    default_live_feed_channel,
    default_measurables,
    default_tunables,
    live_feed_bucket,
    live_feed_channel,
    measurables_bucket,
    set_presence_and_storage,
    set_reported_pose,
    teleop_bucket,
    tunables_bucket,
)
from lab_model.language.domain.holding import (
    clear_holding,
    get_holding,
    held_tag,
    set_holding,
)

from lab_model.coordinator.state.snapshot import LabPose


def _component_entry(state: Dict[str, Any], target_id: str) -> Optional[Dict[str, Any]]:
    """Lookup helper -- returns the component entry dict or ``None``.

    Helpers below treat a missing entry as a no-op for state writes
    (the holding fields are still updated when relevant). This mirrors
    the pre-Phase-2 behavior where a missing entry simply skipped the
    tunables / measurables block rather than raising.
    """
    components = state.get("components") or {}
    entry = components.get(target_id) if isinstance(components, dict) else None
    return entry if isinstance(entry, dict) else None


def commit_pick(
    state: Dict[str, Any],
    target_id: str,
    *,
    x: float,
    y: float,
    rotation: float,
    settled_z: float,
) -> None:
    """After a successful PICK: update tunables/measurables + set HOLDING.

    The pose ``(x, y, rotation)`` was read from ``measurables.pose`` at
    refusal-check time; ``settled_z`` is the z_lab the lab will hold
    the part at (returned by :meth:`_primitive_pick_component`). All four fields land
    in ``tunables.nominal_pose`` (commanded intent), ``measurables.pose``
    (best estimate of where the part is, since robot precision beats
    top-camera), and ``state["holding"]`` (the cross-cutting "in
    gripper" record).
    """
    pose = {"x": float(x), "y": float(y), "rotation": float(rotation), "z": float(settled_z)}
    entry = _component_entry(state, target_id)
    if entry is not None:
        tun = tunables_bucket(entry)
        meas = measurables_bucket(entry)
        tun["nominal_pose"] = dict(pose)
        tun["placement"] = {"mode": PLACEMENT_MODE_PICK}
        set_reported_pose(entry, pose)
    set_holding(
        state,
        tag_id=target_id,
        x=float(x),
        y=float(y),
        rotation=float(rotation),
        z=float(settled_z),
    )


def commit_hover(
    state: Dict[str, Any],
    target_id: str,
    *,
    x: float,
    y: float,
    rotation: float,
    z: float,
    actual_pose: Optional[LabPose] = None,
) -> None:
    """After a successful HOVER: update tunables/measurables + refresh HOLDING.

    ``actual_pose`` is the hook's optional surface for the
    "what really happened" pose -- mock returns commanded + small
    Gaussian noise so the UI gets a believable measurement-noise
    flicker; real returns ``None`` because robot precision is higher
    than camera-through-gripper. Tunables and the holding record
    always carry the commanded pose -- that's the operator's intent
    and matches the convention in MOVE_COMPONENT.
    """
    cmd = {"x": float(x), "y": float(y), "rotation": float(rotation), "z": float(z)}
    if actual_pose is None:
        achieved = dict(cmd)
    else:
        achieved = {
            "x": float(actual_pose.x),
            "y": float(actual_pose.y),
            "rotation": float(actual_pose.rotation),
            "z": float(actual_pose.z),
        }
    entry = _component_entry(state, target_id)
    if entry is not None:
        tun = tunables_bucket(entry)
        meas = measurables_bucket(entry)
        tun["nominal_pose"] = dict(cmd)
        tun["placement"] = {"mode": PLACEMENT_MODE_HOVER}
        set_reported_pose(entry, achieved)
    set_holding(
        state,
        tag_id=target_id,
        x=float(x),
        y=float(y),
        rotation=float(rotation),
        z=float(z),
    )


def commit_place_from_hover(
    state: Dict[str, Any],
    target_id: str,
    *,
    x: float,
    y: float,
    rotation: float,
) -> None:
    """After a successful PLACE_FROM_HOVER: update placed-pose, clear HOLDING.

    Once a part is placed, ``z`` is no longer meaningful (it's
    resting on the breadboard at z_lab=0 by definition) so it's
    stripped from both ``tunables.nominal_pose`` and
    ``measurables.pose``. Presence is reset to BREADBOARD with no
    storage-slot bookkeeping, and the top-level ``holding`` field is
    cleared. ``placement.mode`` becomes MANUAL because the operator
    chose the target XY directly (vs. PICK = via gripper).
    """
    pose = {"x": float(x), "y": float(y), "rotation": float(rotation)}
    entry = _component_entry(state, target_id)
    if entry is not None:
        tun = tunables_bucket(entry)
        meas = measurables_bucket(entry)
        tun["nominal_pose"] = dict(pose)
        tun["placement"] = {"mode": PLACEMENT_MODE_MANUAL}
        set_reported_pose(entry, pose)
        set_presence_and_storage(entry, PRESENCE_BREADBOARD, in_storage=False, slot=None)
    clear_holding(state)


def commit_move_to_breadboard(
    state: Dict[str, Any],
    target_id: str,
    *,
    x: float,
    y: float,
    rotation: float,
    actual_pose: Optional[LabPose] = None,
) -> None:
    """After a successful MOVE_COMPONENT to breadboard: tunables + measurables.

    Used by ``move_component`` (regular breadboard move) and
    ``place_from_storage`` (transition out of STORAGE). In both cases
    presence becomes BREADBOARD with no slot, ``placement.mode`` is
    MANUAL, and ``z`` is not part of the on-table pose.

    ``actual_pose`` is the hook's optional surface for "what really
    happened": mock returns a pose with small noise so the UI flickers
    realistically; real returns ``None`` because robot precision beats
    top-camera. Tunables always carry the commanded pose.
    """
    cmd = {"x": float(x), "y": float(y), "rotation": float(rotation)}
    if actual_pose is None:
        achieved = dict(cmd)
    else:
        achieved = {
            "x": float(actual_pose.x),
            "y": float(actual_pose.y),
            "rotation": float(actual_pose.rotation),
        }
    entry = _component_entry(state, target_id)
    if entry is not None:
        tun = tunables_bucket(entry)
        meas = measurables_bucket(entry)
        tun["nominal_pose"] = dict(cmd)
        tun["placement"] = {"mode": PLACEMENT_MODE_MANUAL}
        set_reported_pose(entry, achieved)
        set_presence_and_storage(entry, PRESENCE_BREADBOARD, in_storage=False, slot=None)


def commit_move_to_storage(
    state: Dict[str, Any],
    target_id: str,
    *,
    x: float,
    y: float,
    rotation: float,
    slot_i: int,
    slot_j: int,
    actual_pose: Optional[LabPose] = None,
) -> None:
    """After a successful MOVE_COMPONENT into Q3: presence STORAGE + slot.

    Shared by ``store_component``, ``repack_storage_slot``, and
    ``recenter_stored_in_inventory``: all three transition the part
    into a storage slot at a known ``(slot_i, slot_j)`` lattice
    position, and all three set ``placement.mode = STORAGE``. The
    actual XY ``(x, y)`` is the *slot center* in lab frame.
    """
    cmd = {"x": float(x), "y": float(y), "rotation": float(rotation)}
    if actual_pose is None:
        achieved = dict(cmd)
    else:
        achieved = {
            "x": float(actual_pose.x),
            "y": float(actual_pose.y),
            "rotation": float(actual_pose.rotation),
        }
    entry = _component_entry(state, target_id)
    if entry is not None:
        tun = tunables_bucket(entry)
        meas = measurables_bucket(entry)
        tun["nominal_pose"] = dict(cmd)
        tun["placement"] = {"mode": PLACEMENT_MODE_STORAGE}
        set_reported_pose(entry, achieved)
        set_presence_and_storage(
            entry,
            PRESENCE_STORAGE,
            in_storage=True,
            slot={"i": int(slot_i), "j": int(slot_j)},
        )


def commit_affirm_placed(state: Dict[str, Any], target_id: str) -> None:
    """After an AFFIRM_PLACED_AT_CURRENT call: lift STORED -> BREADBOARD.

    The operator hand-moved the part out of Q3 to its placed pose; we
    take the current ``measurables.pose`` (where the camera last saw
    it) as the truth and copy it into ``tunables.nominal_pose``.
    Presence drops to BREADBOARD with no slot, mode becomes MANUAL.
    """
    entry = _component_entry(state, target_id)
    if entry is None:
        return
    meas = measurables_bucket(entry)
    pose = meas.get("pose") or {}
    tun = tunables_bucket(entry)
    tun["nominal_pose"] = {
        "x": float(pose.get("x", 0)),
        "y": float(pose.get("y", 0)),
        "rotation": float(pose.get("rotation", 0)),
    }
    tun["placement"] = {"mode": PLACEMENT_MODE_MANUAL}
    set_presence_and_storage(entry, PRESENCE_BREADBOARD, in_storage=False, slot=None)


def commit_optimization_ensemble_complete(
    state: Dict[str, Any],
    *,
    spec: Any,
    session_id: str,
    final_values: Dict[str, float],
    best_loss: float,
) -> None:
    """After ensemble OPTIMIZE: apply final tunables and per-tag optimization metadata."""
    from lab_model.execution.optimization.paths import VariablePathResolver
    from lab_model.execution.optimization.spec import OptimizeEnsembleParameters

    if not isinstance(spec, OptimizeEnsembleParameters):
        spec = OptimizeEnsembleParameters.model_validate(spec)

    resolver = VariablePathResolver.resolve(state, spec.variables, writable=True)
    for var in spec.variables:
        if var.id in final_values:
            resolver.set(var.id, float(final_values[var.id]))

    touched_tags = sorted({v.tag_id for v in spec.variables})
    loss = float(best_loss)
    for tag_id in touched_tags:
        commit_optimization_complete(
            state,
            tag_id,
            strategy_name="ENSEMBLE",
            score=loss,
        )
    _ = session_id  # reserved for configuration metadata.session_id (Phase 3)


def commit_optimization_complete(
    state: Dict[str, Any],
    target_id: str,
    *,
    strategy_name: str,
    score: float,
    final_pose: Optional[Dict[str, float]] = None,
) -> None:
    """After a successful OPTIMIZE_COMPONENT run: write summary fields.

    The optimization tells the system "this strategy got the part
    aligned" -- we record the strategy mode so the UI badge updates,
    a single scalar score (whatever the strategy considered "done"),
    and a snapshot of the final pose so the operator can compare
    later (``measurables.last_optimized_pose``).

    ``final_pose``:
    - ``None``: copy the current ``measurables.pose`` (Newton on real
      already updated it mid-run via the cloudlab progress callback;
      we just snapshot it).
    - ``dict``: an explicit ``{x, y, rotation}`` (mock returns its
      simulated drift this way; this also lands as the new
      ``measurables.pose``).
    """
    entry = _component_entry(state, target_id)
    if entry is None:
        return
    tun = tunables_bucket(entry)
    meas = measurables_bucket(entry)
    tun["placement"] = {"mode": (strategy_name or "").upper()}
    meas["last_optimization_score"] = float(score)
    if final_pose is not None:
        pose = {
            "x": float(final_pose.get("x", 0.0)),
            "y": float(final_pose.get("y", 0.0)),
            "rotation": float(final_pose.get("rotation", 0.0)),
        }
        set_reported_pose(entry, pose)
        meas["last_optimized_pose"] = dict(pose)
        tun["nominal_pose"] = dict(pose)
    else:
        cur = meas.get("pose") or {}
        if cur:
            meas["last_optimized_pose"] = {
                k: cur[k] for k in ("x", "y", "rotation") if k in cur
            }
            if isinstance(cur, dict):
                try:
                    tun["nominal_pose"] = {
                        "x": float(cur.get("x", 0.0)),
                        "y": float(cur.get("y", 0.0)),
                        "rotation": float(cur.get("rotation", 0.0)),
                    }
                except (TypeError, ValueError):
                    pass


def null_measurables_for_targets(
    state: Dict[str, Any],
    target_ids: List[str],
) -> None:
    """Null stale observations while a target is in motion / optimizing.

    Called on BUSY/OPTIMIZING entry for every motion primitive. While a
    component moves, **reported pose** (tunable family) and true
    **measurables** (camera image, scores) do not reflect settled reality and
    must be advertised as ``null``. Motion commits repopulate
    ``tunables.reported_pose`` (mirrored to legacy ``measurables.pose``);
    camera / score fields return only after an explicit capture /
    ``RECORD_MEASURABLES``.

    Tunables *command* (``nominal_pose``) is untouched — that is canvas-truth
    intent and stays stable across motion.
    """
    components = state.get("components") or {}
    if not isinstance(components, dict):
        return
    for tag_id in target_ids:
        if not isinstance(tag_id, str) or not tag_id:
            continue
        entry = components.get(tag_id)
        if not isinstance(entry, dict):
            continue
        set_reported_pose(entry, None)
        meas = measurables_bucket(entry)
        if not isinstance(meas, dict):
            continue
        meas["camera_image"] = None
        meas["last_optimization_score"] = None
        meas["last_optimized_pose"] = None


def commit_observed_measurables(
    state: Dict[str, Any],
    tag_id: str,
    observed: Dict[str, Any],
) -> None:
    """Merge a partial observation dict after ``RECORD_MEASURABLES``.

    True measurable keys (``camera_image``, ``last_optimization_score``, …)
    land in the measurables bucket. Legacy ``pose`` recalculates
    :func:`set_reported_pose`. Legacy ``motor_rotations`` recalculates
    ``tunables.nominal_motor_positions`` (not a measurable). ``None``
    values are skipped.
    """
    if not observed:
        return
    components = state.setdefault("components", {})
    entry = components.setdefault(tag_id, {})
    if "pose" in observed and observed["pose"] is not None:
        set_reported_pose(entry, observed["pose"])
    if "motor_rotations" in observed and observed["motor_rotations"] is not None:
        raw = observed["motor_rotations"]
        if isinstance(raw, dict):
            tun = tunables_bucket(entry)
            nm = tun.setdefault("nominal_motor_positions", {})
            if not isinstance(nm, dict):
                nm = {}
                tun["nominal_motor_positions"] = nm
            for k, v in raw.items():
                try:
                    nm[str(k)] = float(v)
                except (TypeError, ValueError):
                    continue
    meas = measurables_bucket(entry)
    skip = {"pose", "motor_rotations"}
    for key, val in observed.items():
        if val is None or key in skip:
            continue
        meas[key] = val


def commit_observed_camera_image(
    state: Dict[str, Any],
    tag_id: str,
    *,
    path: str,
    source: str,
    cam_id: int,
    fmt: str = "png",
) -> None:
    """After a successful OBSERVE on an OPTICAL_CAMERA tag: write tensor envelope.

    The hook captured a frame and persisted it on disk; this helper
    materializes a :class:`MeasurableTensor` into ``measurables.camera_image``.
    """
    from lab_model.language.measurables.materialize import materialize_measurable

    components = state.setdefault("components", {})
    entry = components.setdefault(tag_id, {})
    meas = measurables_bucket(entry)
    meas["camera_image"] = materialize_measurable(
        tag_id,
        "camera_image",
        {
            "path": path,
            "source": source,
            "cam_id": int(cam_id),
            "format": fmt,
        },
    ).to_api_dict()


# ---------------------------------------------------------------------------
# Phase 8: per-component TELEOP commit helpers
# ---------------------------------------------------------------------------

def _sync_system_status_for_teleop(state: Dict[str, Any]) -> None:
    """Align top-level ``system_status`` with TeleOp session readiness.

    - ``TELEOP`` only when some component is ``active && ready`` (jog allowed).
    - ``BUSY`` while any session is acquiring (``active && !ready``) so the
      system monitor matches the TeleOp board Loading state.
    - Otherwise HOLDING / IDLE as usual.
    """
    from lab_model.language.domain.component import is_teleop_active, is_teleop_ready
    from lab_model.language.domain.holding import (
        SYSTEM_STATUS_BUSY,
        SYSTEM_STATUS_HOLDING,
        SYSTEM_STATUS_IDLE,
        SYSTEM_STATUS_TELEOP,
        held_tag,
    )

    components = state.get("components") or {}
    any_ready = False
    any_acquiring = False
    if isinstance(components, dict):
        for entry in components.values():
            if not isinstance(entry, dict):
                continue
            if is_teleop_ready(entry):
                any_ready = True
                break
            if is_teleop_active(entry):
                any_acquiring = True
    if any_ready:
        state["system_status"] = SYSTEM_STATUS_TELEOP
    elif any_acquiring:
        state["system_status"] = SYSTEM_STATUS_BUSY
    elif held_tag(state):
        state["system_status"] = SYSTEM_STATUS_HOLDING
    else:
        state["system_status"] = SYSTEM_STATUS_IDLE


def _resolve_teleop_mode(state: Dict[str, Any], tag_id: str) -> str:
    from lab_model.language.domain.holding import held_tag
    from lab_model.language.domain.component import PRESENCE_BREADBOARD, presence_of

    if held_tag(state) == tag_id:
        return "pose3d"
    components = state.get("components") or {}
    entry = components.get(tag_id) if isinstance(components, dict) else None
    if isinstance(entry, dict) and presence_of(entry) == PRESENCE_BREADBOARD:
        return "rz"
    return "pose3d"


def _default_teleop_command() -> Dict[str, Any]:
    from lab_model.language.domain.component import default_teleop_command

    return default_teleop_command()


def commit_teleop_start(
    state: Dict[str, Any],
    tag_id: str,
    *,
    now_ms: float,
) -> bool:
    """Mark ``tag_id`` as teleop-pending (active, not yet ready)."""
    components = state.get("components") or {}
    if not isinstance(components, dict):
        return False
    entry = components.get(tag_id)
    if not isinstance(entry, dict):
        return False
    top = teleop_bucket(entry)
    top["active"] = True
    top["ready"] = False
    top["lease_ts"] = float(now_ms)
    top["last_jog_ts"] = None
    top["last_error"] = None
    top["mode"] = _resolve_teleop_mode(state, tag_id)
    top["command"] = _default_teleop_command()
    _sync_system_status_for_teleop(state)
    return True


def commit_teleop_ready(
    state: Dict[str, Any],
    tag_id: str,
    *,
    now_ms: float,
) -> bool:
    """Lab confirmed live control is available for ``tag_id``."""
    components = state.get("components") or {}
    if not isinstance(components, dict):
        return False
    entry = components.get(tag_id)
    if not isinstance(entry, dict):
        return False
    top = teleop_bucket(entry)
    if not top.get("active"):
        return False
    top["ready"] = True
    top["lease_ts"] = float(now_ms)
    top["last_error"] = None
    # Promote system_status BUSY (acquiring) → TELEOP (ready).
    _sync_system_status_for_teleop(state)
    return True


def commit_teleop_start_failed(
    state: Dict[str, Any],
    tag_id: str,
    *,
    error: str,
) -> None:
    """Abort a pending teleop session after lab setup refused."""
    components = state.get("components") or {}
    if not isinstance(components, dict):
        return
    entry = components.get(tag_id)
    if not isinstance(entry, dict):
        return
    top = teleop_bucket(entry)
    top["active"] = False
    top["ready"] = False
    top["lease_ts"] = None
    top["last_jog_ts"] = None
    top["last_error"] = str(error) if error else "teleop setup failed"
    top["command"] = _default_teleop_command()
    _sync_system_status_for_teleop(state)


def commit_teleop_end(
    state: Dict[str, Any],
    tag_id: str,
) -> None:
    """Release the TELEOP lease for ``tag_id`` (idempotent)."""
    components = state.get("components") or {}
    if not isinstance(components, dict):
        return
    entry = components.get(tag_id)
    if not isinstance(entry, dict):
        return
    top = teleop_bucket(entry)
    top["active"] = False
    top["ready"] = False
    top["lease_ts"] = None
    top["last_jog_ts"] = None
    top["last_error"] = None
    top["mode"] = None
    top["command"] = _default_teleop_command()
    _sync_system_status_for_teleop(state)


def commit_teleop_command_idle(state: Dict[str, Any], tag_id: str) -> bool:
    """Mark teleop command phase idle after motion completes."""
    components = state.get("components") or {}
    if not isinstance(components, dict):
        return False
    entry = components.get(tag_id)
    if not isinstance(entry, dict):
        return False
    top = teleop_bucket(entry)
    cmd = top.setdefault("command", _default_teleop_command())
    cmd["phase"] = "idle"
    return True


def commit_teleop_session_pose(
    state: Dict[str, Any],
    tag_id: str,
    live_pose: Dict[str, Any],
) -> bool:
    """Persist teleop live-control pose into tunables (operator intent).

    TeleOp keeps high-rate pose in an in-memory buffer during the session;
    ``TELEOP_GOTO`` does not mutate ``tunables.nominal_pose`` directly.
    When motion completes or the session ends, the final live-control pose
    is merged into canvas-truth tunables (and ``measurables.pose`` is
    repopulated after the start-of-session nulling).

    * ``rz`` mode — update ``rotation`` only; table xy stay as-is.
    * ``pose3d`` mode — update ``x``, ``y``, ``z``, ``rotation``.
    """
    entry = _component_entry(state, tag_id)
    if entry is None or not isinstance(live_pose, dict):
        return False

    top = teleop_bucket(entry)
    mode = top.get("mode") or _resolve_teleop_mode(state, tag_id)

    tun = tunables_bucket(entry)
    meas = measurables_bucket(entry)
    base = dict(tun.get("nominal_pose") or {})

    if mode == "rz":
        if "rotation" in live_pose:
            base["rotation"] = float(live_pose["rotation"])
    else:
        for key in ("x", "y", "z", "rotation"):
            if key in live_pose:
                base[key] = float(live_pose[key])

    tun["nominal_pose"] = dict(base)
    set_reported_pose(entry, base)

    if held_tag(state) == tag_id:
        holding = get_holding(state)
        hp = dict(holding.get("nominal_pose") or {})
        if mode == "rz":
            if "rotation" in live_pose:
                hp["rotation"] = float(live_pose["rotation"])
        else:
            for key in ("x", "y", "z", "rotation"):
                if key in live_pose:
                    hp[key] = float(live_pose[key])
        holding["nominal_pose"] = hp
        state["holding"] = holding

    return True


def commit_teleop_goto(
    state: Dict[str, Any],
    tag_id: str,
    *,
    now_ms: float,
    target_pose: Dict[str, float],
    speed: Optional[Dict[str, float]] = None,
) -> bool:
    """Record a TeleOp goto command (tunables commit on motion idle / session end)."""
    components = state.get("components") or {}
    if not isinstance(components, dict):
        return False
    entry = components.get(tag_id)
    if not isinstance(entry, dict):
        return False
    top = teleop_bucket(entry)
    if not top.get("active") or not top.get("ready"):
        return False

    mode = top.get("mode") or "pose3d"
    target: Dict[str, float] = {}
    for k, v in target_pose.items():
        if k not in ("x", "y", "rotation", "z"):
            continue
        if mode == "rz" and k != "rotation":
            continue
        try:
            target[k] = float(v)
        except (TypeError, ValueError):
            continue
    if not target:
        return False

    spd_in = speed or {}
    spd = {
        "linear_mm_s": float(spd_in.get("linear_mm_s", 25.0)),
        "angular_deg_s": float(spd_in.get("angular_deg_s", 15.0)),
    }
    cmd = top.setdefault("command", _default_teleop_command())
    cmd["target"] = target
    cmd["speed"] = spd
    cmd["phase"] = "executing"
    top["last_jog_ts"] = float(now_ms)
    top["lease_ts"] = float(now_ms)
    return True


def commit_teleop_jog(
    state: Dict[str, Any],
    tag_id: str,
    *,
    now_ms: float,
    nominal_pose: Optional[Dict[str, float]] = None,
    nominal_motor_positions: Optional[Dict[str, float]] = None,
    target_pose: Optional[Dict[str, float]] = None,
    speed: Optional[Dict[str, float]] = None,
) -> bool:
    """Legacy alias: map jog frames to a single goto (no tunables writes)."""
    if nominal_motor_positions:
        return False
    pose = target_pose if target_pose is not None else nominal_pose
    if not pose:
        return False
    return commit_teleop_goto(
        state,
        tag_id,
        now_ms=now_ms,
        target_pose=pose,
        speed=speed,
    )


def sweep_stale_teleop_leases(
    state: Dict[str, Any],
    *,
    now_ms: float,
    ttl_ms: float,
) -> List[str]:
    """Clear teleop leases with no lease refresh within TTL."""
    if ttl_ms <= 0:
        return []
    components = state.get("components") or {}
    if not isinstance(components, dict):
        return []
    cleared: List[str] = []
    cutoff = float(now_ms) - float(ttl_ms)
    for tag_id, entry in components.items():
        if not isinstance(entry, dict):
            continue
        top = teleop_bucket(entry)
        if not top.get("active"):
            continue
        ts = top.get("lease_ts")
        if ts is None:
            ts = top.get("last_jog_ts")
        if not isinstance(ts, (int, float)) or float(ts) < cutoff:
            top["active"] = False
            top["ready"] = False
            top["lease_ts"] = None
            top["last_jog_ts"] = None
            top["command"] = _default_teleop_command()
            cleared.append(tag_id)
    if cleared:
        _sync_system_status_for_teleop(state)
    return cleared


def commit_live_feed_start(
    state: Dict[str, Any],
    tag_id: str,
    *,
    channel: str,
    backend: str,
    resource_id: Optional[str],
    live_exposure_time_ms: Optional[float] = None,
) -> None:
    """Mark a live-feed channel connected and live."""
    entry = _component_entry(state, tag_id)
    if entry is None:
        return
    ch = live_feed_channel(entry, channel)
    ch["connected"] = True
    ch["live"] = True
    ch["backend"] = backend
    ch["resource_id"] = resource_id
    ch["last_error"] = None
    if live_exposure_time_ms is not None:
        ch["live_exposure_time_ms"] = float(live_exposure_time_ms)


def commit_live_exposure(
    state: Dict[str, Any],
    tag_id: str,
    *,
    channel: str = "stream",
    exposure_time_ms: float,
) -> None:
    """Store preview exposure on the live-feed channel (not science tunables)."""
    entry = _component_entry(state, tag_id)
    if entry is None:
        return
    ch = live_feed_channel(entry, channel)
    ch["live_exposure_time_ms"] = float(exposure_time_ms)


def commit_live_feed_end(
    state: Dict[str, Any],
    tag_id: str,
    *,
    channel: str,
    error: Optional[str] = None,
) -> None:
    """Release a live-feed channel (idempotent)."""
    entry = _component_entry(state, tag_id)
    if entry is None:
        return
    ch = live_feed_channel(entry, channel)
    ch["connected"] = False
    ch["live"] = False
    ch["live_exposure_time_ms"] = None
    if error:
        ch["last_error"] = error
    else:
        ch["last_error"] = None


def end_all_live_feed_channels(state: Dict[str, Any], tag_id: str) -> None:
    """Disconnect every declared live-feed channel for ``tag_id``."""
    entry = _component_entry(state, tag_id)
    if entry is None:
        return
    lf = live_feed_bucket(entry)
    for name in list(lf.keys()):
        commit_live_feed_end(state, tag_id, channel=name)


__all__ = [
    "commit_pick",
    "commit_hover",
    "commit_place_from_hover",
    "commit_move_to_breadboard",
    "commit_move_to_storage",
    "commit_affirm_placed",
    "commit_observed_camera_image",
    "commit_observed_measurables",
    "commit_optimization_ensemble_complete",
    "commit_optimization_complete",
    "null_measurables_for_targets",
    "commit_teleop_start",
    "commit_teleop_ready",
    "commit_teleop_start_failed",
    "commit_teleop_end",
    "commit_teleop_goto",
    "commit_teleop_command_idle",
    "commit_teleop_session_pose",
    "commit_teleop_jog",
    "commit_live_feed_start",
    "commit_live_exposure",
    "commit_live_feed_end",
    "end_all_live_feed_channels",
    "sweep_stale_teleop_leases",
]
