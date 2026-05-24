"""Per-primitive commit helpers (lab-state writes).

When an in-air primitive (``pick_component``, ``hover_component``,
``place_from_hover``, ``scan_rotate_in_place``) finishes its hardware
step, the orchestrator commits the result to ``current_state``. The
shape of that commit is identical between the real and mock backends;
this module owns the merge logic so neither subclass repeats it.

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

from lab_model.domain.component import (
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
    teleop_bucket,
    tunables_bucket,
)
from lab_model.domain.holding import (
    clear_holding,
    get_holding,
    held_tag,
    set_holding,
)

from lab_model.state.snapshot import LabPose


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
        meas["pose"] = dict(pose)
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
        meas["pose"] = achieved
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
        meas["pose"] = dict(pose)
        set_presence_and_storage(entry, PRESENCE_BREADBOARD, in_storage=False, slot=None)
    clear_holding(state)


def commit_scan_rotation(
    state: Dict[str, Any],
    target_id: str,
    *,
    mode: str,
    x: float,
    y: float,
    rotation: float,
    z: Optional[float],
) -> None:
    """During / after a SCAN_ROTATE_IN_PLACE step: write ``rotation`` only.

    Used both per-step (mock's stepwise UI updates) and once at the
    end (the final commit at ``theta_max``).

    - ``mode == "held"`` keeps the full ``(x, y, rotation, z)`` shape
      on tunables/measurables AND refreshes ``state["holding"]`` --
      the part is in-gripper, so its pose is fully described.
    - ``mode == "placed"`` writes only ``(x, y, rotation)`` (z is
      meaningless for an on-table part) and does not touch ``holding``
      (the gripper is empty during placed-mode rotation -- the arm
      transiently grips, rotates, releases).

    XY are passed in as the *base* pose so a step-by-step caller
    doesn't have to re-read them on every tick.
    """
    rot = float(rotation)
    entry = _component_entry(state, target_id)
    if entry is not None:
        tun = tunables_bucket(entry)
        meas = measurables_bucket(entry)
        if mode == "held":
            base = {"x": float(x), "y": float(y), "rotation": rot}
            if z is not None:
                base["z"] = float(z)
            tun["nominal_pose"] = dict(base)
            meas["pose"] = dict(base)
        else:  # placed
            base = {"x": float(x), "y": float(y), "rotation": rot}
            tun["nominal_pose"] = dict(base)
            meas["pose"] = dict(base)
    if mode == "held":
        held = get_holding(state)
        nominal = dict(held.get("nominal_pose") or {})
        nominal["rotation"] = rot
        if z is not None:
            nominal["z"] = float(z)
        held["nominal_pose"] = nominal
        state["holding"] = held


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
        meas["pose"] = achieved
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
        meas["pose"] = achieved
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
        meas["pose"] = pose
        meas["last_optimized_pose"] = dict(pose)
    else:
        cur = meas.get("pose") or {}
        if cur:
            meas["last_optimized_pose"] = {
                k: cur[k] for k in ("x", "y", "rotation") if k in cur
            }


def null_measurables_for_targets(
    state: Dict[str, Any],
    target_ids: List[str],
) -> None:
    """Phase 3 / Golden Rule: null measurables for the listed targets.

    Called on BUSY/OPTIMIZING entry for every motion primitive (see
    ``universal_component_architecture.md`` §3.2 "Golden Rule of
    Measurables"). The intent: while a component is in motion or
    being teleoperated, its ``measurables.*`` (pose, motor encoder
    readback, camera image, optimization score/pose) do not reflect
    physical reality and must be advertised as ``null`` to every
    consumer. The motion primitive's own commit helper repopulates
    ``measurables.pose`` (commanded pose) on completion; the
    high-fidelity readback is only available after an explicit
    ``RECORD_MEASURABLES`` primitive.

    Implementation notes:

    - We keep ``measurables`` itself as a dict (not ``None``) so
      every downstream consumer that does
      ``(comp.get("measurables") or {}).get("pose")`` keeps working;
      only the leaf fields flip to ``None``.
    - ``measurables.pose`` is set to ``None`` rather than ``{}`` so
      :func:`inject_motor_rotations_into_state` skips the motor
      injection (the ``isinstance(pose, dict)`` guard there). Motor
      rotations are part of the readback, so they should disappear
      from the polled state too.
    - Tunables (intent) are untouched — that's the canvas-truth
      pose and stays stable across motion.
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
        meas = measurables_bucket(entry)
        if not isinstance(meas, dict):
            continue
        meas["pose"] = None
        meas["camera_image"] = None
        meas["last_optimization_score"] = None
        meas["last_optimized_pose"] = None


def commit_observed_measurables(
    state: Dict[str, Any],
    tag_id: str,
    observed: Dict[str, Any],
) -> None:
    """Merge a partial measurables dict after ``RECORD_MEASURABLES``.

    Keys follow catalog ``capabilities.measurables`` field names
    (``camera_image``, ``motor_rotations``, ``last_optimization_score``, …).
    ``None`` values are skipped so callers can omit fields they did not
    observe.
    """
    if not observed:
        return
    components = state.setdefault("components", {})
    entry = components.setdefault(tag_id, {})
    meas = measurables_bucket(entry)
    for key, val in observed.items():
        if val is None:
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
    """After a successful OBSERVE on an OPTICAL_CAMERA tag: write metadata.

    The hook captured a frame and persisted it on disk; this helper
    just writes the pointer into ``measurables.camera_image`` so the
    UI / downstream consumers can fetch it. ``fmt`` is the image
    format (currently always ``"png"``).
    """
    components = state.setdefault("components", {})
    entry = components.setdefault(tag_id, {})
    meas = measurables_bucket(entry)
    meas["camera_image"] = {
        "path": path,
        "source": source,
        "cam_id": int(cam_id),
        "format": fmt,
    }


# ---------------------------------------------------------------------------
# Phase 8: per-component TELEOP commit helpers
# ---------------------------------------------------------------------------

def _sync_system_status_for_teleop(state: Dict[str, Any]) -> None:
    from lab_model.domain.component import is_teleop_active
    from lab_model.domain.holding import (
        SYSTEM_STATUS_HOLDING,
        SYSTEM_STATUS_IDLE,
        SYSTEM_STATUS_TELEOP,
        held_tag,
    )

    components = state.get("components") or {}
    any_teleop = False
    if isinstance(components, dict):
        any_teleop = any(
            isinstance(e, dict) and is_teleop_active(e) for e in components.values()
        )
    if any_teleop:
        state["system_status"] = SYSTEM_STATUS_TELEOP
    elif held_tag(state):
        state["system_status"] = SYSTEM_STATUS_HOLDING
    else:
        state["system_status"] = SYSTEM_STATUS_IDLE


def _resolve_teleop_mode(state: Dict[str, Any], tag_id: str) -> str:
    from lab_model.domain.holding import held_tag
    from lab_model.domain.component import PRESENCE_BREADBOARD, presence_of

    if held_tag(state) == tag_id:
        return "pose3d"
    components = state.get("components") or {}
    entry = components.get(tag_id) if isinstance(components, dict) else None
    if isinstance(entry, dict) and presence_of(entry) == PRESENCE_BREADBOARD:
        return "rz"
    return "pose3d"


def _default_teleop_command() -> Dict[str, Any]:
    from lab_model.domain.component import default_teleop_command

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
    meas["pose"] = dict(base)

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
    "commit_scan_rotation",
    "commit_move_to_breadboard",
    "commit_move_to_storage",
    "commit_affirm_placed",
    "commit_observed_camera_image",
    "commit_observed_measurables",
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
    "commit_live_feed_end",
    "end_all_live_feed_channels",
    "sweep_stale_teleop_leases",
]
