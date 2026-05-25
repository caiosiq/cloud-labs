"""Phase 6: cloud-labs TeleOp ↔ lab_automation LiveControlSession bridge."""
from __future__ import annotations

import inspect
import threading
from typing import Any, Dict, Optional, TYPE_CHECKING

from lab_model.domain.component import meas_pose, nominal_pose, presence_of, PRESENCE_BREADBOARD
from lab_model.domain.holding import held_tag, is_holding
from lab_model.state.commits import _resolve_teleop_mode
from lab_model.state.snapshot import LabPose

if TYPE_CHECKING:
    from lab_communicator.real.communicator import RealLabCommunicator

_HW_SESSION_LOCK = threading.Lock()
_ACTIVE_HW_TAG: Optional[str] = None


def active_hardware_teleop_tag() -> Optional[str]:
    """Return the tag_id currently holding the hardware live-control mutex."""
    with _HW_SESSION_LOCK:
        return _ACTIVE_HW_TAG


def check_gripper_slip_during_teleop(
    communicator: "RealLabCommunicator", tag_id: str
) -> Optional[str]:
    """Return an error string if pose3d TeleOp sees an open gripper (slip)."""
    mode = resolve_teleop_mode(communicator, tag_id)
    if mode != "pose3d":
        return None
    from lab_communicator.real.gripper import get_gripper_status, gripper_probe_available

    gs = get_gripper_status(communicator)
    if not gripper_probe_available(gs):
        return None
    if bool(gs.get("closed")):
        return None
    return (
        "gripper slip detected: pose3d TeleOp requires a closed gripper "
        f"(source={gs.get('source')!r})"
    )


def resolve_teleop_mode(communicator: "RealLabCommunicator", tag_id: str) -> str:
    """``rz`` (on-table) or ``pose3d`` (held)."""
    with communicator._state_lock:
        return _resolve_teleop_mode(communicator.current_state, tag_id)


def _is_placed_from_lab_state(
    communicator: "RealLabCommunicator", tag_id: str, entry: Dict[str, Any]
) -> bool:
    """Breadboard parts are placed; held parts are not."""
    with communicator._state_lock:
        if held_tag(communicator.current_state) == tag_id:
            return False
    return presence_of(entry) == PRESENCE_BREADBOARD


def warn_if_lab_hardware_pose_diverged(
    communicator: "RealLabCommunicator",
    tag_id: str,
    lab_pose: LabPose,
    *,
    threshold_mm: float = 5.0,
) -> None:
    """Log when cloud-labs pose and hardware ``current_location`` disagree."""
    comp = communicator.get_manipulable(tag_id)
    loc = getattr(comp, "current_location", None) if comp is not None else None
    if loc is None:
        return
    from lab_communicator.real.coordinate_frames import robot_table_xy_to_lab_xy

    x_hw, y_hw = robot_table_xy_to_lab_xy(float(loc.x), float(loc.y))
    dx = abs(float(lab_pose.x) - x_hw)
    dy = abs(float(lab_pose.y) - y_hw)
    if dx > threshold_mm or dy > threshold_mm:
        print(
            f"[REAL LAB] pose desync warning for {tag_id}: "
            f"lab=({lab_pose.x:.1f},{lab_pose.y:.1f}) "
            f"hardware=({x_hw:.1f},{y_hw:.1f}) — rescan recommended"
        )


def sync_component_for_teleop_prepare(
    communicator: "RealLabCommunicator", tag_id: str
) -> None:
    """Align hardware before TeleOp without placeholder ``z_lab_to_robot`` grasp Z.

    Uses scan cache / robot-frame Z (same as pick & move), not lab-frame Z sync.
    Logs XY desync vs cloud-labs for operator awareness.
    """
    comp = communicator.get_manipulable(tag_id)
    if comp is None:
        raise RuntimeError(
            f"sync_component_for_teleop_prepare: {tag_id!r} not in component map"
        )
    exp = communicator.experiment
    if exp is None:
        raise RuntimeError("lab_automation experiment not initialized")
    exp.sync_component_location_from_initial_scan(comp)

    with communicator._state_lock:
        entry = (communicator.current_state.get("components") or {}).get(tag_id)
    if not isinstance(entry, dict):
        raise RuntimeError(f"sync_component_for_teleop_prepare: {tag_id!r} not in lab state")

    mp = meas_pose(entry)
    np = nominal_pose(entry)
    src = mp if mp.get("x") is not None or mp.get("y") is not None else np
    lab_pose = LabPose(
        x=float(src.get("x", 0.0)),
        y=float(src.get("y", 0.0)),
        z=float(src.get("z", 0.0)),
        rotation=float(src.get("rotation", 0.0)),
    )
    warn_if_lab_hardware_pose_diverged(communicator, tag_id, lab_pose)


def sync_component_from_lab_state(
    communicator: "RealLabCommunicator", tag_id: str
) -> None:
    """Push cloud-labs meas/nominal pose into ``OpticalComponent.current_location``."""
    with communicator._state_lock:
        entry = (communicator.current_state.get("components") or {}).get(tag_id)
    if not isinstance(entry, dict):
        raise RuntimeError(f"sync_component_from_lab_state: {tag_id!r} not in lab state")

    mp = meas_pose(entry)
    np = nominal_pose(entry)
    src = mp if mp.get("x") is not None or mp.get("y") is not None else np
    lab_pose = LabPose(
        x=float(src.get("x", 0.0)),
        y=float(src.get("y", 0.0)),
        z=float(src.get("z", 0.0)),
        rotation=float(src.get("rotation", 0.0)),
    )
    is_placed = _is_placed_from_lab_state(communicator, tag_id, entry)
    warn_if_lab_hardware_pose_diverged(communicator, tag_id, lab_pose)
    communicator._apply_loaded_pose_to_hardware(
        tag_id, lab_pose, is_placed=is_placed
    )


def hardware_pose_to_lab(
    communicator: "RealLabCommunicator",
    tag_id: str,
    raw: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Map lab_automation live buffer (robot frame) → cloud-labs live pose."""
    if not raw or not isinstance(raw, dict):
        return None
    from lab_communicator.real.coordinate_frames import robot_table_xy_to_lab_xy

    x_lab, y_lab = robot_table_xy_to_lab_xy(float(raw.get("x", 0.0)), float(raw.get("y", 0.0)))
    z_lab = communicator._z_robot_to_lab(tag_id, float(raw.get("z", 0.0)))
    rot_lab = float(raw.get("rotation", 0.0))
    out: Dict[str, Any] = {
        "x": x_lab,
        "y": y_lab,
        "z": z_lab,
        "rotation": rot_lab,
        "executing": bool(raw.get("executing", False)),
    }
    if raw.get("ts_ms") is not None:
        out["ts_ms"] = float(raw["ts_ms"])
    return out


def lab_target_to_hardware(
    communicator: "RealLabCommunicator",
    tag_id: str,
    target: Dict[str, Any],
    *,
    mode: str,
) -> Dict[str, Any]:
    """Map cloud-labs goto target → ``OpticalExperiment.set_target`` body."""
    if mode == "rz":
        rot = target.get("rotation")
        if rot is None:
            return {}
        return {"rotation": float(rot)}

    out: Dict[str, Any] = {}
    if "rotation" in target:
        out["rotation"] = float(target["rotation"])
    if "x" in target and "y" in target:
        from lab_communicator.real.coordinate_frames import lab_table_xy_to_robot_xy

        xr, yr = lab_table_xy_to_robot_xy(float(target["x"]), float(target["y"]))
        out["x"] = xr
        out["y"] = yr
    if "z" in target:
        out["z"] = communicator._z_lab_to_robot(tag_id, float(target["z"]))
    return out


def _read_lab_rz_rotations(
    communicator: "RealLabCommunicator", tag_id: str
) -> Dict[str, Optional[float]]:
    """Return ``{"meas": …, "nominal": …}`` Rz (deg) for ``tag_id`` or Nones.

    Used as the cloud-labs side of the TeleOp Rz handshake: the **measured**
    rotation is passed to :meth:`OpticalExperiment.start_table_rotation` as a
    *hint* (``lab_rz_initial=…``). :class:`LiveControlSession.enter_table_rotation`
    then inverts the post-grasp ``locked_pose`` via
    :func:`lab_automation.utils.angles.lab_table_rz_from_arm_rpy` (using this
    hint to disambiguate the multivalued inverse) and seeds ``_lab_rotation``
    from THAT, so the value the UI sees on entry comes from the actual TCP
    orientation — not from cloud-labs state — and the robot does not jolt.
    """
    with communicator._state_lock:
        entry = (communicator.current_state.get("components") or {}).get(tag_id)

    def _pick(d: Any) -> Optional[float]:
        if not isinstance(d, dict):
            return None
        v = d.get("rotation")
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    if not isinstance(entry, dict):
        return {"meas": None, "nominal": None}
    return {
        "meas": _pick(meas_pose(entry)),
        "nominal": _pick(nominal_pose(entry)),
    }


def _call_start_table_rotation(
    exp: Any,
    component: Any,
    *,
    safe_z: Optional[float],
    lab_rz_initial: Optional[float],
) -> None:
    """Forward to ``start_table_rotation`` with graceful kwarg degradation.

    Older ``lab_automation`` builds did not accept ``lab_rz_initial``; falling
    back to the legacy signature keeps cloud-labs runnable against them (with
    the original brittle inverse seeding of ``_lab_rotation``).
    """
    try:
        sig = inspect.signature(exp.start_table_rotation)
        if "lab_rz_initial" in sig.parameters and lab_rz_initial is not None:
            exp.start_table_rotation(
                component, safe_z=safe_z, lab_rz_initial=lab_rz_initial
            )
            return
    except (TypeError, ValueError):
        pass
    exp.start_table_rotation(component, safe_z=safe_z)


def start_hardware_session(
    communicator: "RealLabCommunicator",
    tag_id: str,
    *,
    safe_z: Optional[float] = None,
) -> None:
    """Blocking enter — call from ``asyncio.to_thread`` only."""
    global _ACTIVE_HW_TAG
    if not communicator.experiment:
        raise RuntimeError("lab_automation experiment not initialized")
    comp = communicator.get_manipulable(tag_id)
    if comp is None:
        raise RuntimeError(f"no OpticalComponent for {tag_id!r}")

    with _HW_SESSION_LOCK:
        if _ACTIVE_HW_TAG is not None and _ACTIVE_HW_TAG != tag_id:
            raise RuntimeError(
                f"hardware teleop mutex: {_ACTIVE_HW_TAG!r} already active"
            )
        _ACTIVE_HW_TAG = tag_id

    try:
        sync_component_for_teleop_prepare(communicator, tag_id)
        mode = resolve_teleop_mode(communicator, tag_id)
        exp = communicator.experiment

        if mode == "rz":
            rotations = _read_lab_rz_rotations(communicator, tag_id)
            meas_rz = rotations["meas"]
            nominal_rz = rotations["nominal"]
            lab_rz_initial = meas_rz if meas_rz is not None else nominal_rz
            diff = (
                (meas_rz - nominal_rz)
                if (meas_rz is not None and nominal_rz is not None)
                else None
            )
            bar = "=" * 78
            print(bar)
            print(
                f"[TELEOP-DEBUG] cloud-labs start_hardware_session  "
                f"tag={tag_id}  mode=rz"
            )
            print(bar)
            meas_fmt = (
                f"{meas_rz:+9.3f}" if meas_rz is not None else "    (none)"
            )
            nom_fmt = (
                f"{nominal_rz:+9.3f}" if nominal_rz is not None else "    (none)"
            )
            diff_fmt = (
                f"{diff:+9.3f}" if diff is not None else "    (n/a)"
            )
            hint_fmt = (
                f"{lab_rz_initial:+9.3f}"
                if lab_rz_initial is not None
                else "    (none)"
            )
            print(
                f"[TELEOP-DEBUG]   measurables.pose.rotation (UI 'current') = "
                f"{meas_fmt} deg"
            )
            print(
                f"[TELEOP-DEBUG]   nominal_pose.rotation                    = "
                f"{nom_fmt} deg"
            )
            print(
                f"[TELEOP-DEBUG]   diff (meas - nominal)                    = "
                f"{diff_fmt} deg"
            )
            print(
                f"[TELEOP-DEBUG]   → lab_rz_initial hint sent to lab_auto   = "
                f"{hint_fmt} deg  (measured if available else nominal)"
            )
            print(
                f"[TELEOP-DEBUG]   (lab_automation will INVERT post-grasp locked_pose"
            )
            print(
                f"[TELEOP-DEBUG]    to seed _lab_rotation; this hint disambiguates only.)"
            )
            print(bar)
            _call_start_table_rotation(
                exp, comp, safe_z=safe_z, lab_rz_initial=lab_rz_initial
            )
        else:
            cloud_holding = False
            with communicator._state_lock:
                cloud_holding = (
                    is_holding(communicator.current_state)
                    and held_tag(communicator.current_state) == tag_id
                )
            if not getattr(exp, "is_physically_holding", False):
                if cloud_holding:
                    raise RuntimeError(
                        f"cloud-labs reports HOLDING {tag_id!r} but the robot "
                        f"is not in a physical holding session — run PICK or "
                        f"CONFIRM_HOLDING_TAG after verifying the gripper"
                    )
                raise RuntimeError(
                    "pose3d TeleOp requires PICK first (robot not holding)"
                )
            exp.start_held_cartesian(comp)
    except Exception:
        with _HW_SESSION_LOCK:
            if _ACTIVE_HW_TAG == tag_id:
                _ACTIVE_HW_TAG = None
        raise


def stop_hardware_session(communicator: "RealLabCommunicator") -> None:
    """Blocking exit — call from worker thread."""
    global _ACTIVE_HW_TAG
    if communicator.experiment and communicator.experiment.live_control.active:
        communicator.experiment.exit_live_control()
    with _HW_SESSION_LOCK:
        _ACTIVE_HW_TAG = None


def enqueue_hardware_goto(
    communicator: "RealLabCommunicator",
    tag_id: str,
    target: Dict[str, Any],
    speed: Dict[str, Any],
) -> None:
    """Non-blocking goto on the live-control queue."""
    if not communicator.experiment or not communicator.experiment.live_control.active:
        raise RuntimeError("hardware live session not active")
    mode = resolve_teleop_mode(communicator, tag_id)
    body = lab_target_to_hardware(communicator, tag_id, target, mode=mode)
    print(
        f"[TELEOP-DEBUG] cloud-labs enqueue_hardware_goto  tag={tag_id}  "
        f"mode={mode}  ui_target={target}  → lab_automation body={body}  "
        f"speed={speed}"
    )
    if not body:
        return
    communicator.experiment.set_target(body, speed=speed or {})


def read_hardware_live_pose(
    communicator: "RealLabCommunicator", tag_id: str
) -> Optional[Dict[str, Any]]:
    if not communicator.experiment:
        return None
    raw = communicator.experiment.read_pose()
    return hardware_pose_to_lab(communicator, tag_id, raw)
