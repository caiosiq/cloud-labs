"""Real-backend primitive hardware actions.

Each function in this module implements one primitive operation from
the UI's perspective: pick, hover, place_from_hover, scan_rotate_in_place,
move_component, store_component (via move), motor moves, observe,
optimize, and the optimize-run setup/teardown phases. The functions
are **state-free** -- they receive whatever they need as arguments
(the communicator instance, the commanded ``LabPose``, parsed params)
and dispatch the corresponding ``lab_automation`` call. No state
mutations happen here; the orchestrators in
:mod:`lab_communicator.base` own that path (refusal logic, status
flips, commit helpers in ``lab_model.state.commits``).

Reading this file end-to-end answers the question "what is the
hardware contract for each primitive?" -- which physical-lab API
gets called, with which keyword args, on which thread. The thin
hooks in :class:`RealLabCommunicator` (``_primitive_<name>``)
delegate to these functions one-for-one.

Architectural rules (``lab_communicator/README.md``, ``lab_model.platform``):

- ``self.current_state`` access is forbidden in ``_primitive_*`` hook
  bodies; the orchestrator passes you the data via arguments. This
  module imports nothing from :mod:`lab_communicator.base` (avoids the
  circular-import trap from §5.1 rule 5) and nothing from
  :mod:`lab_communicator.mock` (cross-backend isolation).
- Free to import ``lab_automation`` types and the real-side helpers
  (``coordinate_frames``, ``optimization``, ``video``).
"""

from __future__ import annotations

import asyncio
import inspect
import os
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional

from lab_communicator.real.coordinate_frames import (
    lab_rotation_to_robot_yaw,
    lab_table_xy_to_robot_xy,
)
from lab_model.state.snapshot import LabPose
from lab_communicator.shared.util import optional_float

if TYPE_CHECKING:
    from lab_communicator.real.communicator import RealLabCommunicator


# ---------------------------------------------------------------------------
# Motor primitives
# ---------------------------------------------------------------------------

async def primitive_move_motor(
    communicator: "RealLabCommunicator",
    target_id: str,
    motor_id: int,
    distance: float,
) -> None:
    """Hardware step for ``move_motor``.

    Looks up the motor controller named in the catalog
    (``catalog_map[target_id]['motor_controller']``) and dispatches
    its ``move_motor(motor_id, distance, wait_completion=True)`` on a
    worker thread. The orchestrator owns refusals (STORED gate),
    catalog gate (``motor_ids`` membership), the BUSY/IDLE flip, and
    the post-move ``motor_rotation_store`` bookkeeping.
    """
    meta = communicator.catalog_map.get(target_id) or {}
    controller_name = meta.get("motor_controller")
    if not controller_name:
        raise RuntimeError(
            f"[REAL LAB] Catalog entry for {target_id} has no 'motor_controller'."
        )
    controller = getattr(communicator.experiment, controller_name, None)
    if not controller:
        raise RuntimeError(
            f"[REAL LAB] Controller '{controller_name}' not found on experiment."
        )
    await asyncio.to_thread(
        controller.move_motor,
        motor_id,
        distance,
        wait_completion=True,
    )


# ---------------------------------------------------------------------------
# In-air primitives (PICK / HOVER / PLACE_FROM_HOVER / SCAN_ROTATE)
# ---------------------------------------------------------------------------

async def primitive_pick_component(
    communicator: "RealLabCommunicator",
    target_id: str,
    commanded: LabPose,
    params: Dict[str, Any],
) -> float:
    """Hardware step for ``pick_component``.

    Verifies the :class:`OpticalComponent` cache has a current
    location (catalog ingest must have populated this), parses
    ``safe_z`` from the input dict, and dispatches
    ``experiment.pick_component_cloudlab(component, safe_z=...)`` on
    a worker thread. Returns the z_lab the gripper will hold the part
    at after the retract; the orchestrator commits this into
    ``state["holding"].nominal_pose.z`` and the component's pose
    blocks via :func:`commit_pick`.
    """
    comp = communicator.get_manipulable(target_id)
    exp = communicator.experiment
    if not comp or exp is None:
        raise RuntimeError(f"[REAL LAB] PICK: {target_id} not in component registry.")

    exp.sync_component_location_from_initial_scan(comp)
    if not (comp.current_location or comp.inventory_location):
        raise RuntimeError(
            f"[REAL LAB] no scan pose for {target_id}; "
            f"run scan/rescan before pick."
        )

    from lab_communicator.real.teleop_bridge import warn_if_lab_hardware_pose_diverged

    warn_if_lab_hardware_pose_diverged(communicator, target_id, commanded)

    safe_z = optional_float(params, "safe_z")
    await asyncio.to_thread(
        communicator.experiment.pick_component_cloudlab,
        comp,
        safe_z=safe_z,
    )
    return communicator._intent_hover_z_lab(target_id)


async def primitive_hover_component(
    communicator: "RealLabCommunicator",
    target_id: str,
    commanded: LabPose,
    speed: int,
) -> Optional[LabPose]:
    """Hardware step for ``hover_component``.

    Forward-transforms commanded XY/Z/yaw into the robot frame and
    dispatches ``experiment.hover_component_cloudlab(comp, x_robot,
    y_robot, z_robot, yaw_robot, speed=...)`` on a worker thread.
    Returns ``None`` so the orchestrator commits the commanded pose
    verbatim (robot precision exceeds top-camera-through-gripper
    measurement noise).
    """
    comp = communicator.get_manipulable(target_id)
    if not comp:
        raise RuntimeError(f"[REAL LAB] HOVER: {target_id} not in component_map.")

    tx_robot, ty_robot = lab_table_xy_to_robot_xy(commanded.x, commanded.y)
    tz_robot = communicator._z_lab_to_robot(target_id, commanded.z)
    tyaw_robot = lab_rotation_to_robot_yaw(commanded.rotation)

    await asyncio.to_thread(
        communicator.experiment.hover_component_cloudlab,
        comp,
        tx_robot,
        ty_robot,
        tz_robot,
        tyaw_robot,
        speed=speed,
    )
    return None


async def primitive_place_from_hover(
    communicator: "RealLabCommunicator",
    target_id: str,
    commanded: LabPose,
    params: Dict[str, Any],
) -> None:
    """Hardware step for ``place_from_hover``.

    Forward-transforms commanded XY into the robot frame, computes
    the wrist orientation via ``find_angle([180.0, 0.0, -rotation])``
    (the ``-rotation`` sign is the kept-after-Stage-A convention --
    see ``fixing.md`` §3.1), parses ``safe_z`` from the input dict,
    and dispatches ``experiment.place_from_hover_cloudlab(comp,
    x_robot, y_robot, place_angle, safe_z=...)`` on a worker thread.
    """
    # ``find_angle`` is imported lazily so this module loads cleanly
    # when ``lab_automation`` isn't on the path (mock-only test runs).
    from lab_automation.managers.experiment_manager import find_angle

    comp = communicator.get_manipulable(target_id)
    if not comp:
        raise RuntimeError(
            f"[REAL LAB] PLACE_FROM_HOVER: {target_id} not in component_map."
        )

    tx_robot, ty_robot = lab_table_xy_to_robot_xy(commanded.x, commanded.y)
    place_angle = find_angle([180.0, 0.0, -commanded.rotation])
    safe_z = optional_float(params, "safe_z")

    await asyncio.to_thread(
        communicator.experiment.place_from_hover_cloudlab,
        comp,
        tx_robot,
        ty_robot,
        place_angle,
        safe_z=safe_z,
    )


async def primitive_scan_rotate_in_place(
    communicator: "RealLabCommunicator",
    *,
    target_id: str,
    mode: str,
    theta_min: float,
    theta_max: float,
    speed: float,
    axis: str,
    base_x: float,  # noqa: ARG001 -- kept for orchestrator-side parity
    base_y: float,  # noqa: ARG001
    base_z: Optional[float],  # noqa: ARG001
    params: Dict[str, Any],
    on_rotation_update: Callable[[float], None],  # noqa: ARG001
) -> None:
    """Hardware step for ``scan_rotate_in_place``.

    Dispatches to the held variant
    (``experiment.scan_rotate_held_cloudlab``) when ``mode == "held"``
    and the placed variant (``experiment.scan_rotate_placed_cloudlab``)
    otherwise. Both run as a single blocking call on a worker thread;
    real does **not** call ``on_rotation_update`` mid-sweep -- the
    cross-wall API has no per-step granularity to surface, so the
    orchestrator commits the terminal ``theta_max`` rotation after
    this hook returns.

    Mock is the backend that uses the per-step callback today (its
    sweep is a Python ``for`` loop with sleep in between).
    """
    if mode == "held":
        real_fn = (
            getattr(communicator.experiment, "scan_rotate_held_cloudlab", None)
            or getattr(communicator.experiment, "scan_rotate_in_place_held_cloudlab", None)
        )
    else:
        real_fn = (
            getattr(communicator.experiment, "scan_rotate_placed_cloudlab", None)
            or getattr(communicator.experiment, "scan_rotate_in_place_placed_cloudlab", None)
        )
    if not callable(real_fn):
        # No hardware hook yet: real is a no-op so the live table
        # doesn't move, but the orchestrator still commits the final
        # rotation (matches pre-Phase-2B behavior when lab_automation
        # lacked the helper).
        return

    comp = communicator.get_manipulable(target_id)
    kwargs: Dict[str, Any] = dict(
        component=comp,
        theta_min=theta_min,
        theta_max=theta_max,
        speed_deg_per_s=speed,
        axis=axis,
    )
    if mode == "placed":
        safe_z = optional_float(params, "safe_z")
        if safe_z is not None:
            kwargs["safe_z"] = safe_z

    def _invoke_scan() -> Any:
        return real_fn(**kwargs)

    await asyncio.to_thread(_invoke_scan)


# ---------------------------------------------------------------------------
# On-table primitives (move / store / place_from_storage / repack / recenter)
# ---------------------------------------------------------------------------

async def primitive_move_component(
    communicator: "RealLabCommunicator",
    target_id: str,
    commanded: LabPose,
) -> Optional[LabPose]:
    """Hardware step for the move-on-table primitive family.

    All five move-family primitives (``move_component``,
    ``store_component``, ``place_from_storage``,
    ``repack_storage_slot``, ``recenter_stored_in_inventory``) share
    this single hook -- the variation lives in the orchestrator
    (refusal gates, slot allocation, BREADBOARD-vs-STORAGE commit
    shape, post-move storage-intent updates).

    Forward-transforms commanded XY into the robot frame and
    dispatches
    ``experiment.place_component_wo_home_specific_xy_cloudlab(comp,
    target_x=..., target_y=..., angle=[-180, 0, -rot])`` on a worker
    thread. The ``-rot`` here is an empirical yaw-sign negation
    specific to this API (``fixing.md`` §3.1 / Stage A4).

    Returns ``None`` -- robot precision exceeds top-camera so the
    orchestrator commits the commanded pose verbatim.
    """
    comp = communicator.get_manipulable(target_id)
    if comp is None:
        raise RuntimeError(
            f"[REAL LAB] primitive_move_component: {target_id} not in map."
        )
    tx_robot, ty_robot = lab_table_xy_to_robot_xy(commanded.x, commanded.y)
    rot = float(commanded.rotation)
    print(
        f"[REAL LAB] Dispatching robot to X={tx_robot}, Y={ty_robot}, "
        f"Rot={rot} (lab X={commanded.x}, Y={commanded.y})"
    )
    await asyncio.to_thread(
        lambda: communicator.experiment.place_component_wo_home_specific_xy_cloudlab(
            component=comp,
            target_x=tx_robot,
            target_y=ty_robot,
            angle=[-180, 0, -rot],
        )
    )
    return None


# ---------------------------------------------------------------------------
# Camera measurement primitive (OPTICAL_CAMERA only)
# ---------------------------------------------------------------------------

async def primitive_record_measurables(
    communicator: "RealLabCommunicator",
    tag_id: str,
    catalog_meta: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Hardware step — delegates to :mod:`lab_model.measurables`."""
    from lab_model import measurables  # noqa: F401 — register plugins
    from lab_model.measurables.record import observe_for_tag

    return await observe_for_tag(communicator, tag_id, catalog_meta)


# ---------------------------------------------------------------------------
# Optimization primitives (prepare / run / finalize)
# ---------------------------------------------------------------------------

def primitive_prepare_optimization_run(
    communicator: "RealLabCommunicator",
    target_id: str,  # noqa: ARG001 -- kept for symmetry with other primitives
    strategy_name: str,
) -> Optional[str]:
    """Backend setup for one optimization run.

    Builds a per-run subdirectory under ``Camera_Images``
    (``opt_<ts>_<strategy>/``) so the optimization-step file watcher
    (the background thread started in
    :meth:`RealLabCommunicator.__init__`) can scope to one run --
    otherwise residual PNGs from earlier runs leak into the step
    counter. Returns the basename for
    ``current_state["optimization_run_dir"]``.
    """
    run_dir = communicator._make_optimization_run_dir(strategy_name)
    communicator._active_optimization_image_dir = run_dir
    communicator._last_optimization_image_basename = None
    return os.path.basename(run_dir)


async def primitive_optimize_component(
    communicator: "RealLabCommunicator",
    *,
    target_id: str,
    strategy_name: str,
    params: Dict[str, Any],
    progress_callback: Callable[..., None],  # noqa: ARG001 -- file watcher drives steps on real
) -> Optional[Dict[str, Any]]:
    """Hardware step for ``optimize_component``.

    Builds the strategy (``NewtonPlacementStrategy_cloudlab`` or
    ``CobylaAlignmentStrategy_cloudlab``), wires the cloudlab Newton
    place-UI hook (which drives ghost / physical updates for the
    canvas), and dispatches
    ``experiment.optimize_component(comp, strategy)`` on a worker
    thread. The strategy run itself is synchronous and can take
    minutes -- if we held the event loop the lab-state poller would
    starve.

    ``progress_callback`` is the orchestrator-built closure for
    ``optimization_step`` updates. Real does **not** call it
    explicitly: the file watcher started in
    :func:`lab_communicator.real.optimization.monitor_optimization_dir`
    parses ``stepNN`` from PNG filenames and writes the step counter
    directly under the state lock (the file watcher isn't a
    ``_primitive_*`` hook, so the architectural lint allows it).

    Returns ``{"score": 1.0, "final_pose": None}`` so the
    orchestrator's :func:`commit_optimization_complete` snapshots
    whatever pose the strategy left in ``measurables.pose``.
    """
    from lab_automation.objects.strategies import (
        CobylaAlignmentStrategy_cloudlab,
        NewtonPlacementStrategy_cloudlab,
    )

    comp = communicator.get_manipulable(target_id)
    if comp is None:
        raise RuntimeError(
            f"[REAL LAB] primitive_optimize_component: {target_id} not in map."
        )

    run_dir = communicator._active_optimization_image_dir
    if run_dir is None:
        raise RuntimeError(
            "[REAL LAB] primitive_optimize_component: missing run dir; "
            "primitive_prepare_optimization_run was not called?"
        )

    strategy = None
    if strategy_name == "NEWTON":
        communicator._install_cloudlab_place_ui_hook(target_id)

        try:
            _nexp = float(params.get("exposure", 0.2))
        except (TypeError, ValueError):
            _nexp = 0.2
        _nexp = max(0.001, min(30.0, _nexp))

        newton_kw: Dict[str, Any] = dict(
            camera_number=params["camera_number"],
            target_x_pixel=params["target_x_pixel"],
            tolerance_ratio=params["tolerance_ratio"],
            axis=params["axis"],
            initial_move=-0.2,
            do_repositioning=False,
            video_exposure=_nexp,
            capture_exposure=_nexp,
        )
        try:
            init_sig = inspect.signature(NewtonPlacementStrategy_cloudlab.__init__)
            if "progress_callback" in init_sig.parameters:
                newton_kw["progress_callback"] = communicator._cloudlab_progress_callback(target_id)
        except (TypeError, ValueError):
            pass

        communicator._apply_optimization_output_dir_kw(
            NewtonPlacementStrategy_cloudlab, newton_kw, run_dir
        )
        strategy = NewtonPlacementStrategy_cloudlab(**newton_kw)
        print("[REAL LAB] DOING NEWTON STRATEGY")
    elif strategy_name == "COBYLA":
        motor_ids = params.get("motor_ids")
        if not motor_ids:
            raise ValueError("COBYLA strategy requires 'motor_ids' parameter.")

        meta = communicator.catalog_map.get(target_id)
        if not meta or not meta.get("motor_controller"):
            raise ValueError(
                f"COBYLA requires 'motor_controller' in component_catalog "
                f"for {target_id} (e.g. \"wifi_stepper1\")."
            )
        motor_controller = meta["motor_controller"]

        try:
            _exp = float(params.get("exposure", 0.2))
        except (TypeError, ValueError):
            _exp = 0.2
        _exp = max(0.001, min(30.0, _exp))

        cobyla_kw: Dict[str, Any] = {
            "motor_controller": motor_controller,
            "camera_number": params.get("camera_number", 1),
            "motor_ids": motor_ids,
            "objective_threshold": params.get("objective_threshold", 100.0),
            "video_exposure": _exp,
            "capture_exposure": _exp,
        }
        from lab_communicator.real.optimization import load_cobyla_reference_bgr_from_state

        ref_copy = load_cobyla_reference_bgr_from_state(
            communicator,
            camera_number=int(params.get("camera_number", 1)),
        )
        if ref_copy is not None:
            try:
                sig = inspect.signature(CobylaAlignmentStrategy_cloudlab.__init__)
                if "reference_image" in sig.parameters:
                    cobyla_kw["reference_image"] = ref_copy
            except (TypeError, ValueError):
                cobyla_kw["reference_image"] = ref_copy
        else:
            print(
                "[REAL LAB] COBYLA: no reference from measurables.camera_image; "
                "strategy will use its own fallback if any."
            )

        communicator._apply_optimization_output_dir_kw(
            CobylaAlignmentStrategy_cloudlab, cobyla_kw, run_dir
        )
        strategy = CobylaAlignmentStrategy_cloudlab(**cobyla_kw)
    else:
        print(
            f"[REAL LAB] primitive_optimize_component: unrecognized "
            f"strategy {strategy_name!r}; no run dispatched."
        )
        return None

    await asyncio.to_thread(communicator.experiment.optimize_component, comp, strategy)
    return {"score": 1.0, "final_pose": None}


def primitive_finalize_optimization_run(
    communicator: "RealLabCommunicator",
) -> None:
    """Backend teardown for one optimization run.

    Idempotent -- ``_remove_cloudlab_place_ui_hook`` is a no-op when
    no hook is installed (so a COBYLA run that never set one up
    cleans up cleanly). The ``_active_optimization_image_dir`` reset
    reverts the file watcher to the global Camera_Images directory so
    subsequent ad-hoc captures still light up the UI badge.
    """
    try:
        communicator._remove_cloudlab_place_ui_hook()
    finally:
        communicator._active_optimization_image_dir = None


# ---------------------------------------------------------------------------
# Add component primitive (real refuses; real inventory comes from scans)
# ---------------------------------------------------------------------------

async def primitive_add_component_to_state(
    communicator: "RealLabCommunicator",  # noqa: ARG001
    component_data: Dict[str, Any],
    existing_components: Dict[str, Any],  # noqa: ARG001
) -> Optional[Dict[str, Any]]:
    """Real backend: refuse API-level adds; defer to physical scan.

    Returns ``None`` so the base orchestrator skips the state
    insertion. The real inventory is rebuilt from physical scans
    (see :meth:`RealLabCommunicator._initialize_state`); we just print
    the operator instruction here.
    """
    tag_id = (component_data or {}).get("tag_id")
    print(
        f"[REAL LAB] User requested to add {tag_id}. Please place it on "
        f"the table and Rescan."
    )
    return None


async def primitive_reactivate_off_table_component(
    communicator: "RealLabCommunicator",  # noqa: ARG001
    tag_id: str,
    existing_entry: Dict[str, Any],  # noqa: ARG001
    component_data: Dict[str, Any],  # noqa: ARG001
    existing_components: Dict[str, Any],  # noqa: ARG001
) -> Optional[Dict[str, Any]]:
    """Real backend: refuse inventory reactivation via API."""
    print(
        f"[REAL LAB] User requested to add {tag_id} from inventory. "
        f"Please place it on the table and Rescan."
    )
    return None


__all__ = [
    "primitive_move_motor",
    "primitive_pick_component",
    "primitive_hover_component",
    "primitive_place_from_hover",
    "primitive_scan_rotate_in_place",
    "primitive_move_component",
    "primitive_record_measurables",
    "primitive_prepare_optimization_run",
    "primitive_optimize_component",
    "primitive_finalize_optimization_run",
    "primitive_add_component_to_state",
    "primitive_reactivate_off_table_component",
]
