import atexit
import inspect
import json
import math
import os
import re
import subprocess
import sys
import threading
import time
import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
import numpy as np
from scipy.spatial.transform import Rotation as R

from lab_model import motor_rotation_store as motor_rot
from lab_model.component_model import (
    PLACEMENT_MODE_HOVER,
    PLACEMENT_MODE_MANUAL,
    PLACEMENT_MODE_PICK,
    PRESENCE_BREADBOARD,
    PRESENCE_OFF_TABLE,
    PRESENCE_STORAGE,
    default_measurables,
    default_tunables,
    is_on_table,
    is_stored,
    set_presence_and_storage,
    storage_slot,
)
from lab_model.holding import (
    DEFAULT_HOVER_Z_MM,
    SYSTEM_STATUS_BUSY,
    SYSTEM_STATUS_HOLDING,
    SYSTEM_STATUS_IDLE,
    clear_holding,
    confirm_holding_tag as _confirm_holding_tag,
    empty_holding,
    get_holding,
    held_tag,
    is_holding,
    set_holding,
)
from lab_model.storage_region import (
    STORAGE_NOMINAL_ROTATION_DEG,
    find_storage_slot_and_center,
    is_placed_region,
    is_storage_region,
    nominal_center_pose_for_stored_entry,
)

from lab_communicator.base import LabCommunicator
from lab_communicator.shared.snapshot import LabPose

# Configuration for External Lab Automation Library
# LAB_AUTOMATION_PATH = path to the lab_automation package folder (repo root).
# For "from lab_automation.managers..." we must add its parent to sys.path so the package name resolves.
LAB_AUTOMATION_PATH = os.getenv("LAB_AUTOMATION_PATH")
if LAB_AUTOMATION_PATH and os.path.exists(LAB_AUTOMATION_PATH):
    _lab_parent = os.path.dirname(LAB_AUTOMATION_PATH)
    if _lab_parent not in sys.path:
        sys.path.insert(0, _lab_parent)
    print(f"[REAL LAB] Added parent {_lab_parent} to sys.path (package lab_automation at {LAB_AUTOMATION_PATH})")
else:
    print("[REAL LAB] Warning: LAB_AUTOMATION_PATH not set or invalid.")

# Import Real Lab Automation
try:
    from lab_automation.managers.experiment_manager import OpticalExperiment, find_angle
    from lab_automation.objects.base import OpticalComponent, Pose
    from lab_automation.objects.strategies import NewtonPlacementStrategy_cloudlab, CobylaAlignmentStrategy_cloudlab

    LAB_LIB_AVAILABLE = True
except ImportError as e:
    print(f"[REAL LAB] Critical Error: Failed to import lab_automation: {e}")
    LAB_LIB_AVAILABLE = False

try:
    from lab_automation.managers.recorder_capture_helpers import activate_cam_and_capture
    RECORDER_CAPTURE_AVAILABLE = True
except ImportError:
    activate_cam_and_capture = None
    RECORDER_CAPTURE_AVAILABLE = False

# --- Coordinate frames + utility helpers (Phase 1 of the communicator
# refactor; see ``communicator_refactor.md`` §10) ---
#
# All cross-wall coordinate-frame logic now lives in
# ``real/coordinate_frames.py``: XY rotation, Z transforms, yaw
# transforms, calibration constants, and the safety bounds. The two
# generic utilities (env-float parsing, optional-float HTTP-param
# parsing) live in ``shared/util.py``. We re-export the public names
# here so existing references inside this file keep working unchanged.
from lab_communicator.real.coordinate_frames import (  # noqa: F401  re-exports
    DEFAULT_COMPONENT_HEIGHT_MM,
    GRASP_OFFSET_MM,
    LAB_ROBOT_TABLE_ROTATION_RAD,
    MAX_SAFE_HOVER_Z_LAB_MM,
    TABLE_Z0_ROBOT_MM,
    lab_rotation_to_robot_yaw,
    lab_table_xy_to_robot_xy,
    robot_table_xy_to_lab_xy,
    robot_yaw_to_lab_rotation,
    z_lab_to_robot as _z_lab_to_robot_pure,
    z_robot_to_lab as _z_robot_to_lab_pure,
)
from lab_communicator.shared.util import (  # noqa: F401
    env_float as _env_float_shared,
    optional_float as _optional_float,
)


# Back-compat alias preserving the old ``[REAL LAB]`` log prefix on
# stale-env-var warnings without forcing every call site to pass it.
def _env_float(name: str, default: float) -> float:
    return _env_float_shared(name, default, log_prefix="[REAL LAB]")


class RealLabCommunicator(LabCommunicator):
    log_prefix = "[REAL LAB]"

    def __init__(self):
        if not LAB_LIB_AVAILABLE:
            raise RuntimeError("lab_automation library not available. Cannot start RealLabCommunicator.")

        # Base seeds ``current_state`` (with empty components / IDLE),
        # ``catalog_map``, and the state lock. Phase 2A made base the
        # owner of all three -- see ``communicator_refactor.md`` §5.2.
        super().__init__()

        print("[REAL LAB] Initializing OpticalExperiment...")
        # Initialize the experiment manager
        self.experiment = OpticalExperiment(mock=False)
        self.experiment.initialize_robot()

        # Cache of OpticalComponent objects: { "tag_22": OpticalComponent(...) }
        self.component_map: Dict[str, OpticalComponent] = {}

        # Catalog path (real physical inventory). Mock mode uses a different
        # file -- see ``schemas/component_catalog.mock.json`` and README.
        # File now at ``backend/lab_communicator/real/communicator.py``;
        # ``schemas/`` is three levels up. (Was two levels up when the
        # class lived in ``backend/lab_communicator/real.py``.)
        self.catalog_file = os.path.abspath(
            os.path.join(
                os.path.dirname(__file__), "..", "..", "..", "schemas",
                "component_catalog.real.json",
            )
        )

        # Stage 6 feature flag: when set (HOVER_PLACEHOLDER_STATE=1), the
        # placeholder implementations of pick/hover/place_from_hover/
        # scan_rotate_in_place MUTATE current_state so the Cloud-Labs UI can
        # exercise the HOLDING workflow without moving real hardware. When
        # unset, the methods only LOG "[REAL LAB] PLACEHOLDER: ..." and
        # return -- safer default for a live table.
        self._hover_placeholder_state = (
            os.getenv("HOVER_PLACEHOLDER_STATE", "").strip().lower()
            in ("1", "true", "yes", "on")
        )
        self._place_cloudlab_orig: Any = None
        self._place_from_storage_tag: Optional[str] = None
        self._store_component_tag: Optional[str] = None
        self._store_pending_slot: Optional[Tuple[int, int]] = None

        # CobylaAlignmentStrategy_cloudlab.reference_image (BGR ndarray, same family as table-cam / capture_image)
        self._cobyla_ref_lock = threading.Lock()
        self._cobyla_reference_bgr: Optional[Any] = None  # np.ndarray when set

        # Tags intentionally in storage (tag_id -> {i,j}); persisted under states/real_lab_stored_intent.json
        self._stored_intent: Dict[str, Dict[str, int]] = {}
        self._load_stored_intent_from_disk()

        self._initialize_state()
        # Boot-time gripper reconciliation (see new_primitives.md #6.3).
        # MUST run AFTER _initialize_state so component poses exist for the
        # "best-effort hold pose" fallback, and BEFORE the recorder / monitor
        # threads so any HOLDING_UNCONFIRMED is visible on the very first
        # GET /api/lab-state that the UI issues.
        self._reconcile_holding_on_boot()
        self._recorder_procs: List[subprocess.Popen] = []
        self._start_recorder_processes()
        # Fallback when image names have no stepNN: count once per new basename (avoids double bumps on mtime+size).
        self._last_optimization_image_basename: Optional[str] = None
        # During OPTIMIZING, watch only this run's subdirectory (see _make_optimization_run_dir).
        self._active_optimization_image_dir: Optional[str] = None

        # Start a background thread to monitor optimization steps reliably
        self._opt_monitor_thread = threading.Thread(target=self._monitor_optimization_dir, daemon=True)
        self._opt_monitor_thread.start()

    def _camera_images_base_dir(self) -> str:
        """Canonical Camera_Images root for new optimization run folders.

        Thin wrapper -- body in :func:`lab_communicator.real.video.camera_images_base_dir`.
        """
        from lab_communicator.real.video import camera_images_base_dir
        return camera_images_base_dir()

    def _make_optimization_run_dir(self, strategy_name: str) -> str:
        """Create a per-run ``Camera_Images`` subdirectory.

        Thin wrapper -- body in :func:`lab_communicator.real.optimization.make_optimization_run_dir`.
        """
        from lab_communicator.real.optimization import make_optimization_run_dir
        return make_optimization_run_dir(self, strategy_name)

    @staticmethod
    def _apply_optimization_output_dir_kw(
        strategy_cls: Any, kw: Dict[str, Any], run_dir: str,
    ) -> None:
        """Inject ``run_dir`` into the strategy kwargs under whatever name it accepts.

        Thin wrapper -- body in
        :func:`lab_communicator.real.optimization.apply_optimization_output_dir_kw`.
        """
        from lab_communicator.real.optimization import (
            apply_optimization_output_dir_kw,
        )
        apply_optimization_output_dir_kw(strategy_cls, kw, run_dir)

    def _get_optimization_watch_dirs(self) -> List[str]:
        """Directories the file watcher / video stream should poll for new PNGs.

        Thin wrapper -- body in
        :func:`lab_communicator.real.optimization.get_optimization_watch_dirs`.
        """
        from lab_communicator.real.optimization import get_optimization_watch_dirs
        return get_optimization_watch_dirs(self)

    def _get_latest_optimization_png(self) -> Tuple[Optional[str], int]:
        """Find the most recently modified image under the watch dirs.

        Thin wrapper -- body in
        :func:`lab_communicator.real.optimization.get_latest_optimization_png`.
        """
        from lab_communicator.real.optimization import get_latest_optimization_png
        return get_latest_optimization_png(self)

    @staticmethod
    def _optimization_step_from_image_path(path: str) -> Optional[int]:
        """Parse ``stepNN`` from a PNG basename. Returns ``None`` when absent.

        Thin wrapper -- body in
        :func:`lab_communicator.real.optimization.optimization_step_from_image_path`.
        """
        from lab_communicator.real.optimization import (
            optimization_step_from_image_path,
        )
        return optimization_step_from_image_path(path)

    def _monitor_optimization_dir(self):
        """Background task -- watches the latest PNG and bumps ``optimization_step``.

        Thin wrapper -- body in
        :func:`lab_communicator.real.optimization.monitor_optimization_dir`.
        Used as the ``threading.Thread.target`` in ``__init__``; the
        wrapper layer is transparent to the thread.
        """
        from lab_communicator.real.optimization import monitor_optimization_dir
        monitor_optimization_dir(self)

    def _send_recorder_cmd(self, port: int, cmd: str) -> None:
        """Send a one-line command to a recorder process on the given port.

        Thin wrapper -- body in :func:`lab_communicator.real.video.send_recorder_cmd`.
        """
        from lab_communicator.real.video import send_recorder_cmd
        send_recorder_cmd(port, cmd)

    def _start_recorder_processes(self) -> None:
        """Start the two recorder subprocesses to warm up table cameras.

        Thin wrapper -- body in :func:`lab_communicator.real.video.start_recorder_processes`.
        """
        from lab_communicator.real.video import start_recorder_processes
        start_recorder_processes(self)

    def _shutdown_recorders(self) -> None:
        """Send EXIT to recorder ports and wait for processes. Called on backend exit.

        Thin wrapper -- body in :func:`lab_communicator.real.video.shutdown_recorders`.
        """
        from lab_communicator.real.video import shutdown_recorders
        shutdown_recorders(self)

    def _stored_intent_path(self) -> str:
        """Persisted map of which catalog tags are in inventory storage and at which grid cell."""
        # File now at ``backend/lab_communicator/real/communicator.py``;
        # ``states/`` is three levels up. (Was two levels up before the
        # Phase 1 folder restructure.)
        return os.path.abspath(
            os.path.join(
                os.path.dirname(__file__), "..", "..", "..", "states",
                "real_lab_stored_intent.json",
            )
        )

    def _load_stored_intent_from_disk(self) -> None:
        """Hydrate ``self._stored_intent`` from
        ``states/real_lab_stored_intent.json``.

        Thin wrapper -- file I/O lives in
        :func:`lab_communicator.shared.storage_intent.load_stored_intent`.
        """
        from lab_communicator.shared.storage_intent import load_stored_intent
        self._stored_intent = load_stored_intent(
            self._stored_intent_path(), log_prefix="[REAL LAB]"
        )

    def _save_stored_intent_to_disk(self) -> None:
        """Persist ``self._stored_intent`` to disk.

        Thin wrapper -- serialization lives in
        :func:`lab_communicator.shared.storage_intent.save_stored_intent`.
        """
        from lab_communicator.shared.storage_intent import save_stored_intent
        save_stored_intent(
            self._stored_intent_path(),
            self._stored_intent,
            log_prefix="[REAL LAB]",
        )

    def _stored_intent_set_slot(self, tag_id: str, i: int, j: int) -> None:
        with self._state_lock:
            self._stored_intent[tag_id] = {"i": int(i), "j": int(j)}
        self._save_stored_intent_to_disk()

    def _stored_intent_remove(self, tag_id: str) -> None:
        with self._state_lock:
            self._stored_intent.pop(tag_id, None)
        self._save_stored_intent_to_disk()

    def _rebuild_stored_intent_from_lab_state(self, components: Dict[str, Any]) -> None:
        """After loading a snapshot, align the manifest with STORED entries in state.

        Derivation lives in
        :func:`lab_communicator.shared.storage_intent.rebuild_intent_from_components`.
        We acquire the state lock here (around the swap) and persist
        outside the lock to keep the disk write off the hot path.
        """
        from lab_communicator.shared.storage_intent import (
            rebuild_intent_from_components,
        )
        new_m = rebuild_intent_from_components(components)
        with self._state_lock:
            self._stored_intent = new_m
        self._save_stored_intent_to_disk()

    def get_stored_intent_for_layout(self) -> Dict[str, Dict[str, int]]:
        """Copy for ``analyze_layout_issues`` (layout-conflicts API)."""
        with self._state_lock:
            return {k: dict(v) for k, v in self._stored_intent.items()}

    def _initialize_state(self):
        """Scan the table based on the catalog and populate the component map.

        Thin wrapper -- the body lives in
        :func:`lab_communicator.real.scan.initialize_state`.
        """
        from lab_communicator.real.scan import initialize_state
        initialize_state(self)

    def refresh_pose_from_camera(self):
        """Re-scan the table (camera-driven) and rebuild measurables.pose for each component.

        Thin wrapper -- the body lives in
        :func:`lab_communicator.real.scan.refresh_pose_from_camera`.
        """
        from lab_communicator.real.scan import refresh_pose_from_camera
        refresh_pose_from_camera(self)

    def refresh_state(self):
        """Deprecated name; use :meth:`refresh_pose_from_camera`."""
        self.refresh_pose_from_camera()

    # ``set_lab_state`` and ``get_lab_state`` now live on the base
    # template class (Phase 2A of the communicator refactor). Real
    # provides the ``_apply_loaded_pose_to_hardware`` /
    # ``_post_apply_snapshot`` hooks below; everything else (merge
    # logic, holding reset, status normalization, lock acquisition,
    # motor-rotation injection on read) is shared in
    # ``lab_communicator.base`` + ``lab_communicator.shared.snapshot``.

    def _apply_loaded_pose_to_hardware(
        self, tag_id: str, lab_pose: LabPose, *, is_placed: bool
    ) -> None:
        """Push a loaded snapshot pose into the ``lab_automation`` component.

        This is the **marquee Stage C exemption** -- the only method
        allowed to write ``OpticalComponent.current_location``. The
        architectural lint in
        :class:`backend.tests.test_lab_primitives.StageCInvariantsTests`
        enforces this. Snapshot poses are lab / UI frame; the
        ``lab_automation`` library expects robot frame. All three axes
        are transformed through the dedicated helpers so the cross-wall
        convention stays in one place (``fixing.md`` §3, §3.1, §5):

        - **XY**       :func:`lab_table_xy_to_robot_xy` (calibrated rotation)
        - **Z**        :meth:`_z_lab_to_robot`            (per-tag; uses catalog ``height_mm``)
        - **rotation** :func:`lab_rotation_to_robot_yaw`  (identity today; see module comment)

        We write only ``current_location`` and ``is_placed``;
        ``inventory_location`` is owned elsewhere in ``lab_automation``
        (see ``labautomation_new_primitives.md`` §5).
        """
        comp = self.component_map.get(tag_id)
        if not comp:
            # Missing from map, skip; UI will still render but the
            # robot won't know about this part.
            return

        x_robot, y_robot = lab_table_xy_to_robot_xy(lab_pose.x, lab_pose.y)
        z_robot = self._z_lab_to_robot(tag_id, lab_pose.z)
        yaw_robot = lab_rotation_to_robot_yaw(lab_pose.rotation)

        comp.current_location = Pose(
            x=x_robot, y=y_robot, z=z_robot, roll=180, pitch=0, yaw=yaw_robot
        )
        comp.is_placed = bool(is_placed)

    def _post_apply_snapshot(self, components: Dict[str, Any]) -> None:
        """Real backend re-syncs the persistent stored-intent file.

        Mock has no separate stored-intent file (its storage tracking
        lives in the in-memory ``components`` dict directly); this hook
        is the no-op default on base.
        """
        self._rebuild_stored_intent_from_lab_state(components)

    def set_cobyla_reference_from_png_bytes(self, data: bytes) -> Tuple[bool, str]:
        """Decode PNG bytes to BGR and store for the next COBYLA optimize run.

        Thin wrapper -- body in
        :func:`lab_communicator.real.optimization.set_cobyla_reference_from_png_bytes`.
        """
        from lab_communicator.real.optimization import (
            set_cobyla_reference_from_png_bytes,
        )
        return set_cobyla_reference_from_png_bytes(self, data)

    def clear_cobyla_reference(self) -> None:
        """Drop any stored cobyla reference image.

        Thin wrapper -- body in :func:`lab_communicator.real.optimization.clear_cobyla_reference`.
        """
        from lab_communicator.real.optimization import clear_cobyla_reference
        clear_cobyla_reference(self)

    def get_cobyla_reference_status(self) -> Dict[str, Any]:
        """Status dict for the UI's cobyla-reference badge.

        Thin wrapper -- body in
        :func:`lab_communicator.real.optimization.get_cobyla_reference_status`.
        """
        from lab_communicator.real.optimization import get_cobyla_reference_status
        return get_cobyla_reference_status(self)

    def get_cobyla_reference_png_bytes(self) -> Optional[bytes]:
        """Re-encode the stored reference back to PNG bytes (download).

        Thin wrapper -- body in
        :func:`lab_communicator.real.optimization.get_cobyla_reference_png_bytes`.
        """
        from lab_communicator.real.optimization import get_cobyla_reference_png_bytes
        return get_cobyla_reference_png_bytes(self)

    def _tag_id_for_component(self, comp: Any) -> Optional[str]:
        """Reverse-lookup ``OpticalComponent`` -> tag id.

        Thin wrapper around
        :func:`lab_communicator.shared.util.tag_id_for_component`.
        """
        from lab_communicator.shared.util import tag_id_for_component
        return tag_id_for_component(self.component_map, comp)

    def _ui_pose_for_placement_tick(
        self, tag_id: str, target_x: Optional[float], target_y: Optional[float]
    ) -> Dict[str, float]:
        """Build the lab-frame pose dict for one Newton sub-move.

        Thin wrapper -- body in
        :func:`lab_communicator.shared.placement_ui.ui_pose_for_placement_tick`.
        Real passes :func:`lab_communicator.real.coordinate_frames.robot_table_xy_to_lab_xy`
        as the XY transform; mock passes the identity transform from
        ``mock/coordinate_frames.py``.
        """
        from lab_communicator.shared.placement_ui import ui_pose_for_placement_tick
        return ui_pose_for_placement_tick(
            self.current_state,
            self._state_lock,
            tag_id,
            target_x,
            target_y,
            xy_robot_to_lab=robot_table_xy_to_lab_xy,
        )

    def _apply_placement_ui_phase(
        self, tag_id: str, phase: str, pose: Dict[str, float]
    ) -> None:
        """Mutate ``current_state`` for one phase of a Newton sub-move.

        Thin wrapper -- body in
        :func:`lab_communicator.shared.placement_ui.apply_placement_ui_phase`.
        """
        from lab_communicator.shared.placement_ui import apply_placement_ui_phase
        apply_placement_ui_phase(
            self.current_state, self._state_lock, tag_id, phase, pose
        )

    def _cloudlab_progress_callback(self, target_tag_id: str):
        """Build the ``progress_callback`` for ``NewtonPlacementStrategy_cloudlab``.

        Thin wrapper -- body in
        :func:`lab_communicator.real.optimization.cloudlab_progress_callback`.
        """
        from lab_communicator.real.optimization import cloudlab_progress_callback
        return cloudlab_progress_callback(self, target_tag_id)

    def _install_cloudlab_place_ui_hook(self, target_tag_id: str) -> None:
        """Wrap ``place_component_wo_home_specific_xy_cloudlab`` for UI updates.

        Thin wrapper -- body in
        :func:`lab_communicator.real.optimization.install_cloudlab_place_ui_hook`.
        """
        from lab_communicator.real.optimization import install_cloudlab_place_ui_hook
        install_cloudlab_place_ui_hook(self, target_tag_id)

    def _remove_cloudlab_place_ui_hook(self) -> None:
        """Restore the original ``place_component_wo_home_specific_xy_cloudlab``.

        Thin wrapper -- body in
        :func:`lab_communicator.real.optimization.remove_cloudlab_place_ui_hook`.
        """
        from lab_communicator.real.optimization import remove_cloudlab_place_ui_hook
        remove_cloudlab_place_ui_hook(self)

    def get_rotation_from_angle(self, robot_angle: List[float]) -> float:
        """
        Reverses the logic of find_angle to recover the Z-rotation (theta)
        from the robot's orientation vector (rx, ry, rz).
        Assumes robot_angle is a Rotation Vector in degrees.
        """
        try:
            # 1. Convert degrees to radians
            # Based on forward logic: target = rotvec_rad * 180/pi
            # So rotvec_rad = target * pi/180
            v_rad = np.array(robot_angle) * np.pi / 180.0
            
            # 2. Create Rotation object
            r = R.from_rotvec(v_rad)
            
            # 3. Get Euler angles [Rx, Ry, Rz]
            # We expect [180, 0, theta] roughly
            euler = r.as_euler('xyz', degrees=True)
            
            # 4. Extract Z rotation
            # euler[2] is the rotation around Z
            return float(euler[2])
            
        except Exception as e:
            print(f"[REAL LAB] Error in get_rotation_from_angle: {e}")
            return 0.0

    def _catalog_wh(self, tag_id: str) -> Tuple[float, float]:
        """Thin wrapper around
        :func:`lab_communicator.shared.catalog_lookup.catalog_wh`."""
        from lab_communicator.shared.catalog_lookup import catalog_wh
        return catalog_wh(self.catalog_map.get, tag_id)

    # --- Z-frame transforms (see module-level comment for the convention) ---
    def _component_height_mm(self, tag_id: str) -> float:
        """Physical height of a component (base to top, mm).

        Thin wrapper around
        :func:`lab_communicator.shared.catalog_lookup.component_height_mm`.
        Defaults to ``DEFAULT_COMPONENT_HEIGHT_MM`` (env-tunable) on a
        missing/malformed catalog entry.
        """
        from lab_communicator.shared.catalog_lookup import component_height_mm
        return component_height_mm(
            (self.catalog_map or {}).get,
            tag_id,
            default_mm=DEFAULT_COMPONENT_HEIGHT_MM,
            log_prefix="[REAL LAB]",
        )

    def _z_lab_to_robot(self, tag_id: str, z_lab: float) -> float:
        """Forward transform: cloud-labs z_lab -> lab_automation z_robot.

        Thin wrapper -- the actual math lives in
        :func:`lab_communicator.real.coordinate_frames.z_lab_to_robot`.
        We look up ``height_mm`` from the catalog here (per-component
        data) and pass it through; the pure transform owns the
        per-setup constants.
        """
        return _z_lab_to_robot_pure(
            float(z_lab), self._component_height_mm(tag_id)
        )

    def _z_robot_to_lab(self, tag_id: str, z_robot: float) -> float:
        """Inverse transform: lab_automation z_robot -> cloud-labs z_lab.

        Thin wrapper -- see :meth:`_z_lab_to_robot` and
        :func:`lab_communicator.real.coordinate_frames.z_robot_to_lab`.
        """
        return _z_robot_to_lab_pure(
            float(z_robot), self._component_height_mm(tag_id)
        )

    def _intent_hover_z_lab(self, tag_id: str) -> float:
        """
        Return the z_lab the robot *intends* to settle at after a PICK.

        Asks ``lab_automation.compute_intent_hover_z_lab(component)`` when
        that math-only helper is available (preferred -- it keeps
        ``safe_z_retract_robot`` fully encapsulated on the lab side). Falls
        back to ``DEFAULT_HOVER_Z_MM`` (conservative safe clearance) when
        the helper is missing or raises.

        No motion is ever dispatched here. The returned value is written
        to ``tunables.nominal_pose.z`` and ``holding.nominal_pose.z`` so
        the UI round-trips: if the user clicks HOVER without editing the z
        field, the forward transform produces the same z_robot the robot
        is already at -- no vertical motion.
        """
        comp = self.component_map.get(tag_id)
        for name in ("compute_intent_hover_z_lab", "get_intent_hover_z_lab"):
            fn = getattr(self.experiment, name, None)
            if callable(fn) and comp is not None:
                try:
                    return float(fn(comp))
                except Exception as e:
                    print(
                        f"[REAL LAB] {name}({tag_id}) raised {e!r}; "
                        f"falling back to DEFAULT_HOVER_Z_MM={DEFAULT_HOVER_Z_MM}"
                    )
                    break
        return float(DEFAULT_HOVER_Z_MM)

    async def move_component(self, target_id: str, params: Dict[str, Any]):
        print(f"[REAL LAB] Moving {target_id}...")

        tx = params.get("target_x")
        ty = params.get("target_y")
        rot = params.get("rotation", 0)
        tx_lab = float(tx)
        ty_lab = float(ty)
        rot = float(rot)

        with self._state_lock:
            entry = (self.current_state.get("components") or {}).get(target_id)
        if entry and is_stored(entry) and self._place_from_storage_tag != target_id:
            print(f"[REAL LAB] Refusing move: {target_id} is STORED (use place from storage).")
            return
        if (
            entry
            and (entry.get("tunables") or {}).get("presence") == PRESENCE_BREADBOARD
            and is_storage_region(tx_lab, ty_lab)
            and self._store_component_tag != target_id
        ):
            print(f"[REAL LAB] Refusing move into storage quadrant (use Store).")
            return

        # 1. Update Status
        with self._state_lock:
            self.current_state["system_status"] = "BUSY"

        # 2. Get Component
        if target_id not in self.component_map:
            print(f"[REAL LAB] Error: Component {target_id} not found in map.")
            with self._state_lock:
                self.current_state["system_status"] = "IDLE"
            return

        comp = self.component_map[target_id]

        tx_robot, ty_robot = lab_table_xy_to_robot_xy(tx_lab, ty_lab)
        
        # 4. Execute Move
        try:
            print(
                f"[REAL LAB] Dispatching robot to X={tx_robot}, Y={ty_robot}, Rot={rot} "
                f"(lab X={tx_lab}, Y={ty_lab})"
            )
            
            if not comp.current_location:
                print(f"[REAL LAB] Warning: {target_id} current_location unknown. Assuming it's at previous location or 0,0")
            
            # Worker thread: place blocks for a long time; must not block the event loop or lab-state polls stall.
            #
            # Known convention disagreement (fixing.md §3.1 / Stage A4):
            # ``-rot`` is an inline, ad-hoc yaw negation that has been
            # empirically correct for this hardware setup. Other cross-wall
            # rotation sites (``set_lab_state``, ``hover_component``) go
            # through ``lab_rotation_to_robot_yaw`` which is identity today.
            # Once we can test on the physical robot we'll either fold the
            # negation into ``lab_rotation_to_robot_yaw`` (and remove the
            # minus here) or document why it must stay outside. Do NOT
            # change this expression without a physical test -- the current
            # form is what actually places parts correctly today.
            await asyncio.to_thread(
                lambda: self.experiment.place_component_wo_home_specific_xy_cloudlab(
                    component=comp,
                    target_x=tx_robot,
                    target_y=ty_robot,
                    angle=[-180, 0, -rot],
                )
            )
            
            # 5. Update State
            #UPDATE TO GET REFORCE-SCAM
            place_from = self._place_from_storage_tag
            with self._state_lock:
                if target_id in self.current_state["components"]:
                    ce = self.current_state["components"][target_id]
                    meas = ce.setdefault("measurables", default_measurables())
                    tun = ce.setdefault("tunables", default_tunables())
                    meas["pose"] = {
                        "x": tx_lab,
                        "y": ty_lab,
                        "rotation": rot
                    }
                    if self._store_component_tag == target_id:
                        if self._store_pending_slot is not None:
                            si, sj = self._store_pending_slot
                            set_presence_and_storage(
                                ce, PRESENCE_STORAGE, in_storage=True, slot={"i": int(si), "j": int(sj)}
                            )
                            self._stored_intent_set_slot(target_id, si, sj)
                        else:
                            set_presence_and_storage(ce, PRESENCE_STORAGE, in_storage=True, slot=None)
                        tun["placement"] = {"mode": "STORAGE"}
                    else:
                        set_presence_and_storage(ce, PRESENCE_BREADBOARD, in_storage=False, slot=None)
                        tun["placement"] = {"mode": "MANUAL"}
                        if place_from == target_id:
                            self._stored_intent_remove(target_id)

                    tun["nominal_pose"] = {
                        "x": tx_lab, "y": ty_lab, "rotation": rot
                    }
                    self.current_state["last_updated"] = datetime.now().isoformat()
                    self._store_pending_slot = None
            print(comp)
        except Exception as e:
            print(f"[REAL LAB] Move Failed: {e}")

        finally:
            with self._state_lock:
                self.current_state["system_status"] = "IDLE"
                self.current_state["optimization_step"] = 0
                self.current_state["optimization_run_dir"] = None
                self.current_state["last_updated"] = datetime.now().isoformat()
                self._store_pending_slot = None

    async def _do_move_motor(
        self, target_id: str, motor_id: int, distance: float
    ) -> None:
        """Cross-wall hook for :meth:`LabCommunicator.move_motor`.

        The orchestrator handled refusals (STORED gate), the catalog
        gate (``motor_ids`` membership), the BUSY/IDLE status flip, and
        the post-move ``motor_rotation_store`` bookkeeping. This hook
        just dispatches the concrete ``experiment.<motor_controller>.move_motor``
        call -- exceptions propagate up so the orchestrator's
        ``finally`` can roll status back to IDLE.
        """
        meta = self.catalog_map.get(target_id) or {}
        controller_name = meta.get("motor_controller")
        if not controller_name:
            # Catalog is missing ``motor_controller`` despite passing
            # the orchestrator's ``motor_ids`` gate -- this is a
            # catalog data error, not a runtime UX bug.
            raise RuntimeError(
                f"[REAL LAB] Catalog entry for {target_id} has no 'motor_controller'."
            )
        controller = getattr(self.experiment, controller_name, None)
        if not controller:
            raise RuntimeError(
                f"[REAL LAB] Controller '{controller_name}' not found on experiment."
            )
        # Worker thread: same event-loop issue as optimize / place.
        await asyncio.to_thread(
            controller.move_motor,
            motor_id,
            distance,
            wait_completion=True,
        )

    async def optimize_component(self, target_id: str, strategy_name: str, params: Dict[str, Any]):
        print(f"[REAL LAB] Optimizing {target_id} with {strategy_name}...")

        if target_id not in self.component_map:
            return
        with self._state_lock:
            ent = (self.current_state.get("components") or {}).get(target_id)
        if ent and is_stored(ent):
            print(f"[REAL LAB] Refusing optimize: {target_id} is STORED.")
            return

        run_dir = self._make_optimization_run_dir(strategy_name)
        self._active_optimization_image_dir = run_dir
        self._last_optimization_image_basename = None

        comp = self.component_map[target_id]
        newton_place_hook_installed = False

        try:
            with self._state_lock:
                self.current_state["system_status"] = "OPTIMIZING"
                self.current_state["optimization_step"] = 0
                self.current_state["optimization_run_dir"] = os.path.basename(run_dir)

            # 1. Select Strategy
            strategy = None
            if strategy_name == "NEWTON":
                # Live UI: ghost follows planned targets; solid follows completed places (see _cloudlab hooks).
                self._install_cloudlab_place_ui_hook(target_id)
                newton_place_hook_installed = True

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
                        newton_kw["progress_callback"] = self._cloudlab_progress_callback(target_id)
                except (TypeError, ValueError):
                    pass

                self._apply_optimization_output_dir_kw(NewtonPlacementStrategy_cloudlab, newton_kw, run_dir)
                strategy = NewtonPlacementStrategy_cloudlab(**newton_kw)
                print("DOING NEWTON STRATEGY")
            elif strategy_name == "COBYLA":
                motor_ids = params.get("motor_ids")
                if target_id in self.current_state["components"]:
                    pass

                if not motor_ids:
                    raise ValueError("COBYLA strategy requires 'motor_ids' parameter.")

                meta = self.catalog_map.get(target_id)
                if not meta or not meta.get("motor_controller"):
                    raise ValueError(
                        f"COBYLA requires 'motor_controller' in component_catalog for {target_id} "
                        '(e.g. "wifi_stepper1").'
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
                with self._cobyla_ref_lock:
                    ref_copy = None if self._cobyla_reference_bgr is None else self._cobyla_reference_bgr.copy()
                if ref_copy is not None:
                    try:
                        sig = inspect.signature(CobylaAlignmentStrategy_cloudlab.__init__)
                        if "reference_image" in sig.parameters:
                            cobyla_kw["reference_image"] = ref_copy
                    except (TypeError, ValueError):
                        cobyla_kw["reference_image"] = ref_copy
                else:
                    print("[REAL LAB] COBYLA: no reference image set via UI; strategy will use its own fallback if any.")

                self._apply_optimization_output_dir_kw(CobylaAlignmentStrategy_cloudlab, cobyla_kw, run_dir)
                strategy = CobylaAlignmentStrategy_cloudlab(**cobyla_kw)

            if strategy:
                # 2. Execute off the event loop. optimize_component() in lab_automation is synchronous and
                # can run for minutes; if we block here, GET /api/lab-state never runs and the UI never
                # sees system_status=OPTIMIZING or optimization_step updates (mock works because it awaits sleep).
                await asyncio.to_thread(self.experiment.optimize_component, comp, strategy)

                # 3. Update State
                with self._state_lock:
                    if target_id in self.current_state["components"]:
                        ce = self.current_state["components"][target_id]
                        tun = ce.setdefault("tunables", default_tunables())
                        meas = ce.setdefault("measurables", default_measurables())
                        tun["placement"] = {"mode": strategy_name.upper()}
                        meas["last_optimization_score"] = 1.0
                        mp = (meas.get("pose") or {}).copy()
                        if mp:
                            meas["last_optimized_pose"] = {
                                k: mp[k] for k in ("x", "y", "rotation") if k in mp
                            }
                        self.current_state["last_updated"] = datetime.now().isoformat()

        except Exception as e:
            print(f"[REAL LAB] Optimization Failed: {e}")

        finally:
            if newton_place_hook_installed:
                self._remove_cloudlab_place_ui_hook()
            self._active_optimization_image_dir = None
            with self._state_lock:
                self.current_state["system_status"] = "IDLE"
                self.current_state["optimization_step"] = 0
                self.current_state["optimization_run_dir"] = None
                self.current_state["last_updated"] = datetime.now().isoformat()

    async def remove_component(self, target_id: str):
         print(f"[REAL LAB] Remove requested for {target_id} (Not implemented)")
         pass

    def get_video_feed_status(self):
        # TODO: Check actual camera connection
        return {"connected": True, "source": "/api/video-feed/stream"} 

    def get_video_stream(self, fps: int = 10):
        """Yield MJPEG frames from the ceiling camera.

        Thin wrapper around the synchronous generator
        :func:`lab_communicator.real.video.get_video_stream`. The
        delegation uses ``yield from`` so the generator semantics
        (lazy iteration, caller-driven termination) are preserved.
        """
        from lab_communicator.real.video import get_video_stream
        yield from get_video_stream(self, fps)

    def get_optimization_stream(self, fps: int = 5):
        """Yield MJPEG frames by watching the Camera_Images directory.

        Thin wrapper around
        :func:`lab_communicator.real.video.get_optimization_stream`.
        """
        from lab_communicator.real.video import get_optimization_stream
        yield from get_optimization_stream(self, fps)

    def capture_table_cam(self, cam_id: int, exposure: float = 0.2):
        """Capture one image from a table recorder camera (1 or 2). Returns PNG bytes or ``None``.

        Thin wrapper -- body in :func:`lab_communicator.real.video.capture_table_cam`.
        """
        from lab_communicator.real.video import capture_table_cam
        return capture_table_cam(self, cam_id, exposure)

    async def observe_measurables_for_tag(self, tag_id: str) -> Dict[str, Any]:
        with self._state_lock:
            entry = (self.current_state.get("components") or {}).get(tag_id)
        if not isinstance(entry, dict):
            return {}
        meta = (self.catalog_map or {}).get(tag_id) or {}
        ctype = meta.get("type") or ""
        if ctype != "OPTICAL_CAMERA":
            return self.return_measurables_for_tag(tag_id)
        png = self.capture_table_cam(1, exposure=0.2)
        if not png:
            return self.return_measurables_for_tag(tag_id)
        base = self._camera_images_base_dir()
        os.makedirs(base, exist_ok=True)
        path = os.path.join(base, f"{tag_id}_observe.png")
        with open(path, "wb") as f:
            f.write(png)
        with self._state_lock:
            comps = self.current_state.setdefault("components", {})
            comp = comps.setdefault(tag_id, {})
            meas = comp.setdefault("measurables", default_measurables())
            meas["camera_image"] = {
                "path": path,
                "source": "real_table_cam",
                "cam_id": 1,
                "format": "png",
            }
            self.current_state["last_updated"] = datetime.now().isoformat()
        return self.return_measurables_for_tag(tag_id)

    async def store_component(self, target_id: str):
        print(f"[REAL LAB] store_component {target_id}...")
        with self._state_lock:
            entry = (self.current_state.get("components") or {}).get(target_id)
        if not entry or (entry.get("tunables") or {}).get("presence") != PRESENCE_BREADBOARD:
            print(f"[REAL LAB] store_component: {target_id} must be on breadboard intent.")
            return
        w, h = self._catalog_wh(target_id)
        with self._state_lock:
            comps = dict(self.current_state.get("components") or {})
        slot = find_storage_slot_and_center(comps, target_id, w, h, lambda tid: self._catalog_wh(tid))
        if not slot:
            print("[REAL LAB] No free storage slot in Q3.")
            return
        sx, sy, si, sj = slot
        rot = STORAGE_NOMINAL_ROTATION_DEG
        self._store_pending_slot = (si, sj)
        self._store_component_tag = target_id
        try:
            await self.move_component(
                target_id,
                {"target_x": sx, "target_y": sy, "rotation": rot},
            )
        finally:
            self._store_component_tag = None

    async def affirm_placed_at_current(self, target_id: str):
        print(f"[REAL LAB] affirm_placed_at_current {target_id}...")
        with self._state_lock:
            entry = (self.current_state.get("components") or {}).get(target_id)
            if not entry or not is_stored(entry):
                print(f"[REAL LAB] affirm: {target_id} must be STORED.")
                return
            comp = self.current_state["components"][target_id]
            meas = comp.setdefault("measurables", default_measurables())
            pose = meas.get("pose") or {}
            tun = comp.setdefault("tunables", default_tunables())
            set_presence_and_storage(comp, PRESENCE_BREADBOARD, in_storage=False, slot=None)
            tun["nominal_pose"] = {
                "x": float(pose.get("x", 0)),
                "y": float(pose.get("y", 0)),
                "rotation": float(pose.get("rotation", 0)),
            }
            tun["placement"] = {"mode": "MANUAL"}
            self.current_state["last_updated"] = datetime.now().isoformat()
        self._stored_intent_remove(target_id)
        cobj = self.component_map.get(target_id)
        if cobj is not None:
            cobj.is_placed = True

    async def repack_storage_slot(self, target_id: str):
        print(f"[REAL LAB] repack_storage_slot {target_id}...")
        with self._state_lock:
            entry = (self.current_state.get("components") or {}).get(target_id)
        if not entry or not is_stored(entry):
            print(f"[REAL LAB] repack: {target_id} must be STORED.")
            return
        w, h = self._catalog_wh(target_id)
        with self._state_lock:
            comps = dict(self.current_state.get("components") or {})
        slot = find_storage_slot_and_center(comps, target_id, w, h, lambda tid: self._catalog_wh(tid))
        if not slot:
            print("[REAL LAB] repack: no free storage slot.")
            return
        sx, sy, si, sj = slot
        rot = STORAGE_NOMINAL_ROTATION_DEG
        self._store_pending_slot = (si, sj)
        self._store_component_tag = target_id
        try:
            await self.move_component(
                target_id,
                {"target_x": sx, "target_y": sy, "rotation": rot},
            )
        finally:
            self._store_component_tag = None

    async def recenter_stored_in_inventory(self, target_id: str):
        print(f"[REAL LAB] recenter_stored_in_inventory {target_id}...")
        with self._state_lock:
            entry = (self.current_state.get("components") or {}).get(target_id)
        if not entry or not is_stored(entry):
            print(f"[REAL LAB] recenter: {target_id} must be STORED.")
            return
        nom = nominal_center_pose_for_stored_entry(entry)
        if nom is None:
            print("[REAL LAB] recenter: could not resolve storage cell (need slot metadata or pose in Q3).")
            return
        sx, sy, si, sj = nom
        self._store_pending_slot = (si, sj)
        self._store_component_tag = target_id
        try:
            await self.move_component(
                target_id,
                {"target_x": sx, "target_y": sy, "rotation": STORAGE_NOMINAL_ROTATION_DEG},
            )
        finally:
            self._store_component_tag = None

    async def place_from_storage(self, target_id: str, params: Dict[str, Any]):
        print(f"[REAL LAB] place_from_storage {target_id}...")
        with self._state_lock:
            entry = (self.current_state.get("components") or {}).get(target_id)
        if not entry or not is_stored(entry):
            print(f"[REAL LAB] place_from_storage: {target_id} not STORED.")
            return
        tx = float(params.get("target_x", params.get("x", 0)))
        ty = float(params.get("target_y", params.get("y", 0)))
        if not is_placed_region(tx, ty):
            print("[REAL LAB] Target must be outside storage quadrant (Q3).")
            return
        self._place_from_storage_tag = target_id
        try:
            await self.move_component(target_id, params)
        finally:
            self._place_from_storage_tag = None

    async def add_component_to_state(self, component_data: Dict[str, Any]):
        print(f"[REAL LAB] User requested to add {component_data.get('tag_id')}. Please place it on the table and Rescan.")

    # --- In-air manipulation (see ``new_primitives.md`` §6 and §8.3) ---
    #
    # Stage 8 wiring is live: ``lab_automation`` ships
    # ``pick_component_cloudlab``, ``hover_component_cloudlab``,
    # ``place_from_hover_cloudlab``, ``scan_rotate_held_cloudlab``,
    # ``scan_rotate_placed_cloudlab`` (see
    # ``labautomation_new_primitives.md`` §2). Each primitive below dispatches
    # to the corresponding method via ``asyncio.to_thread`` and updates
    # ``current_state`` on success.
    #
    # ``HOVER_PLACEHOLDER_STATE=1`` keeps a debug escape hatch that bypasses
    # the real call and only mutates ``current_state`` (matches the
    # mock-lab behavior for UI testing without moving the robot). Left in
    # place intentionally per ``labautomation_new_primitives.md`` §6
    # item 3 ("remove or keep as debug escape hatch").

    def _reconcile_holding_on_boot(self) -> None:
        """Boot-time HOLDING reconciliation (see ``new_primitives.md`` §6.3).

        Thin wrapper -- the body lives in
        :func:`lab_communicator.real.gripper.reconcile_holding_on_boot`.
        """
        from lab_communicator.real.gripper import reconcile_holding_on_boot
        reconcile_holding_on_boot(self)

    def get_gripper_status(self) -> Dict[str, Any]:
        """Real-lab override of :meth:`LabCommunicator.get_gripper_status`.

        Thin wrapper -- the probe-priority logic lives in
        :func:`lab_communicator.real.gripper.get_gripper_status`.
        """
        from lab_communicator.real.gripper import get_gripper_status
        return get_gripper_status(self)

    def _holding_placeholder_log(
        self, action: str, target_id: Optional[str], extra: Optional[Dict[str, Any]] = None
    ) -> None:
        # "DISPATCH" when the real ``lab_automation`` path is active;
        # "PLACEHOLDER" only when ``HOVER_PLACEHOLDER_STATE=1`` is forcing
        # the state-mutation-only stub. Historically this always said
        # "PLACEHOLDER" because the real paths were dormant until
        # lab_automation shipped the ``*_cloudlab`` methods (Stage 8).
        bits = [f"action={action}", f"target_id={target_id or '<none>'}"]
        if extra:
            for k, v in extra.items():
                bits.append(f"{k}={v}")
        tag = "PLACEHOLDER" if self._hover_placeholder_state else "DISPATCH"
        print(f"[REAL LAB] {tag}: {' '.join(bits)} "
              f"(HOVER_PLACEHOLDER_STATE={'on' if self._hover_placeholder_state else 'off'})")

    async def pick_component(self, target_id: str, params: Dict[str, Any]):
        self._holding_placeholder_log("PICK_COMPONENT", target_id, {"params": dict(params or {})})
        with self._state_lock:
            entry = (self.current_state.get("components") or {}).get(target_id)
            already_holding = is_holding(self.current_state)
        if already_holding:
            print(f"[REAL LAB] refusing PICK; already HOLDING {held_tag(self.current_state)}.")
            return
        if not entry:
            print(f"[REAL LAB] PICK: {target_id} not in state.")
            return

        pose = ((entry.get("measurables") or {}).get("pose") or {})
        px = float(pose.get("x", 0.0))
        py = float(pose.get("y", 0.0))
        prot = float(pose.get("rotation", 0.0))

        if self._hover_placeholder_state:
            # Placeholder path: no lab_automation available -> assume the
            # retract will settle at DEFAULT_HOVER_Z_MM. Update measurables
            # to the intent pose, matching the convention used by
            # MOVE_COMPONENT (robot placement precision beats top-camera
            # reads, so the commanded pose is the best estimate of the
            # current pose until vision says otherwise).
            z_lab_intent = float(DEFAULT_HOVER_Z_MM)
            with self._state_lock:
                self.current_state["system_status"] = SYSTEM_STATUS_BUSY
            await asyncio.sleep(0.25)
            with self._state_lock:
                ce = self.current_state["components"][target_id]
                tun = ce.setdefault("tunables", default_tunables())
                meas = ce.setdefault("measurables", default_measurables())
                tun["nominal_pose"] = {"x": px, "y": py, "rotation": prot, "z": z_lab_intent}
                tun["placement"] = {"mode": PLACEMENT_MODE_PICK}
                meas["pose"] = {"x": px, "y": py, "rotation": prot, "z": z_lab_intent}
                set_holding(
                    self.current_state,
                    tag_id=target_id,
                    x=px,
                    y=py,
                    rotation=prot,
                    z=z_lab_intent,
                )
                self.current_state["last_updated"] = datetime.now().isoformat()
            print(f"[REAL LAB] PLACEHOLDER: HOLDING {target_id} @ z_lab={z_lab_intent:.1f} mm (no motion)")
            return

        comp = self.component_map.get(target_id)
        if not comp or not comp.current_location:
            print(f"[REAL LAB] PICK: no robot current_location for {target_id}; rescan or check catalog.")
            return

        safe_z = _optional_float(params, "safe_z")
        with self._state_lock:
            self.current_state["system_status"] = SYSTEM_STATUS_BUSY
        try:
            await asyncio.to_thread(
                self.experiment.pick_component_cloudlab,
                comp,
                safe_z=safe_z,
            )
        except Exception as e:
            print(f"[REAL LAB] pick_component: lab_automation failed: {e!r}")
            with self._state_lock:
                self.current_state["system_status"] = SYSTEM_STATUS_IDLE
            raise

        # Post-pick: ask lab_automation (math-only, no motion) which z_lab
        # it intends to hold the part at. Falls back to DEFAULT_HOVER_Z_MM
        # if the helper is missing (see _intent_hover_z_lab docstring).
        # Measurables are updated to the intent pose -- same convention as
        # MOVE_COMPONENT. Robot placement precision is higher than top-
        # camera reads, so the commanded pose is the best available
        # estimate of where the part actually is; if/when a higher-
        # precision vision pipeline contradicts it, it can overwrite.
        z_lab_intent = self._intent_hover_z_lab(target_id)

        with self._state_lock:
            ce = self.current_state["components"][target_id]
            tun = ce.setdefault("tunables", default_tunables())
            meas = ce.setdefault("measurables", default_measurables())
            tun["nominal_pose"] = {"x": px, "y": py, "rotation": prot, "z": z_lab_intent}
            tun["placement"] = {"mode": PLACEMENT_MODE_PICK}
            meas["pose"] = {"x": px, "y": py, "rotation": prot, "z": z_lab_intent}
            set_holding(
                self.current_state,
                tag_id=target_id,
                x=px,
                y=py,
                rotation=prot,
                z=z_lab_intent,
            )
            self.current_state["last_updated"] = datetime.now().isoformat()
        print(f"[REAL LAB] PICK complete: HOLDING {target_id} @ z_lab={z_lab_intent:.1f} mm")

    async def hover_component(self, target_id: str, target_pose: Dict[str, float]):
        self._holding_placeholder_log("HOVER", target_id, {"target_pose": dict(target_pose or {})})
        with self._state_lock:
            holding_now = is_holding(self.current_state)
            held = held_tag(self.current_state)
        if not holding_now:
            print("[REAL LAB] refusing HOVER; not HOLDING.")
            return
        if held and held != target_id:
            print(f"[REAL LAB] refusing HOVER; currently holding {held}.")
            return

        tx = float(target_pose.get("target_x", target_pose.get("x", 0.0)))
        ty = float(target_pose.get("target_y", target_pose.get("y", 0.0)))
        trot = float(target_pose.get("rotation", 0.0))
        tz = float(target_pose.get("z", DEFAULT_HOVER_Z_MM))
        try:
            speed = int(target_pose.get("speed", 100))
        except (TypeError, ValueError):
            speed = 100

        # Bounds check in the lab frame (z_lab). This catches runaway HTTP
        # payloads (e.g. someone accidentally sending a robot-frame z=600)
        # before we forward-transform and hand it to the robot.
        if not (0.0 <= tz <= MAX_SAFE_HOVER_Z_LAB_MM):
            print(
                f"[REAL LAB] refusing HOVER; z_lab={tz:.1f} mm outside safe "
                f"range [0, {MAX_SAFE_HOVER_Z_LAB_MM:.1f}]. Interpret z as "
                f"height of the component base above the table."
            )
            return

        if self._hover_placeholder_state:
            # Placeholder path: commit intent to tunables, holding, AND
            # measurables (same convention as MOVE_COMPONENT -- the robot's
            # commanded pose is the best estimate of the actual pose).
            with self._state_lock:
                self.current_state["system_status"] = SYSTEM_STATUS_BUSY
            await asyncio.sleep(0.25)
            with self._state_lock:
                comp = (self.current_state.get("components") or {}).get(target_id)
                if isinstance(comp, dict):
                    tun = comp.setdefault("tunables", default_tunables())
                    meas = comp.setdefault("measurables", default_measurables())
                    tun["nominal_pose"] = {"x": tx, "y": ty, "rotation": trot, "z": tz}
                    tun["placement"] = {"mode": PLACEMENT_MODE_HOVER}
                    meas["pose"] = {"x": tx, "y": ty, "rotation": trot, "z": tz}
                set_holding(self.current_state, tag_id=target_id, x=tx, y=ty, rotation=trot, z=tz)
                self.current_state["last_updated"] = datetime.now().isoformat()
            return

        comp = self.component_map.get(target_id)
        if not comp:
            print(f"[REAL LAB] HOVER: {target_id} not in component_map.")
            return

        # Forward-transform XY (lab -> robot table rotation) and Z
        # (z_lab -> z_robot). lab_automation only ever sees robot-frame
        # coordinates; cloud-labs state stays in z_lab. Rotation also goes
        # through the dedicated helper for parity with set_lab_state, even
        # though lab_rotation_to_robot_yaw is identity today (fixing.md §3.1).
        tx_robot, ty_robot = lab_table_xy_to_robot_xy(tx, ty)
        tz_robot = self._z_lab_to_robot(target_id, tz)
        tyaw_robot = lab_rotation_to_robot_yaw(trot)
        with self._state_lock:
            self.current_state["system_status"] = SYSTEM_STATUS_BUSY
        try:
            await asyncio.to_thread(
                self.experiment.hover_component_cloudlab,
                comp,
                tx_robot,
                ty_robot,
                tz_robot,
                tyaw_robot,
                speed=speed,
            )
        except Exception as e:
            print(f"[REAL LAB] hover_component: lab_automation failed: {e!r}")
            with self._state_lock:
                self.current_state["system_status"] = SYSTEM_STATUS_HOLDING
            raise

        # Commit intent to tunables, holding, and measurables. We write
        # measurables.pose here (same as MOVE_COMPONENT) because robot
        # placement accuracy is higher than what the top camera can
        # measure through the gripper -- so the commanded pose is the
        # best available estimate of where the part actually is.
        with self._state_lock:
            ce = (self.current_state.get("components") or {}).get(target_id)
            if isinstance(ce, dict):
                tun = ce.setdefault("tunables", default_tunables())
                meas = ce.setdefault("measurables", default_measurables())
                tun["nominal_pose"] = {"x": tx, "y": ty, "rotation": trot, "z": tz}
                tun["placement"] = {"mode": PLACEMENT_MODE_HOVER}
                meas["pose"] = {"x": tx, "y": ty, "rotation": trot, "z": tz}
            set_holding(self.current_state, tag_id=target_id, x=tx, y=ty, rotation=trot, z=tz)
            self.current_state["last_updated"] = datetime.now().isoformat()
        print(
            f"[REAL LAB] HOVER complete: HOLDING {target_id} @ "
            f"x={tx:.1f} y={ty:.1f} rot={trot:.1f} z_lab={tz:.1f} mm"
        )

    async def place_from_hover(self, target_id: str, target_pose: Dict[str, float]):
        self._holding_placeholder_log(
            "PLACE_FROM_HOVER", target_id, {"target_pose": dict(target_pose or {})}
        )
        with self._state_lock:
            holding_now = is_holding(self.current_state)
            held = held_tag(self.current_state)
        if not holding_now:
            print("[REAL LAB] refusing PLACE_FROM_HOVER; not HOLDING.")
            return
        if held and held != target_id:
            print(f"[REAL LAB] refusing PLACE_FROM_HOVER; currently holding {held}.")
            return
        tx = float(target_pose.get("target_x", target_pose.get("x", 0.0)))
        ty = float(target_pose.get("target_y", target_pose.get("y", 0.0)))
        trot = float(target_pose.get("rotation", 0.0))
        if is_storage_region(tx, ty):
            print("[REAL LAB] refusing PLACE_FROM_HOVER into storage quadrant.")
            return

        if self._hover_placeholder_state:
            with self._state_lock:
                self.current_state["system_status"] = SYSTEM_STATUS_BUSY
            await asyncio.sleep(0.25)
            with self._state_lock:
                comp = (self.current_state.get("components") or {}).get(target_id)
                if isinstance(comp, dict):
                    tun = comp.setdefault("tunables", default_tunables())
                    meas = comp.setdefault("measurables", default_measurables())
                    tun["nominal_pose"] = {"x": tx, "y": ty, "rotation": trot}
                    tun["placement"] = {"mode": PLACEMENT_MODE_MANUAL}
                    meas["pose"] = {"x": tx, "y": ty, "rotation": trot}
                    set_presence_and_storage(comp, PRESENCE_BREADBOARD, in_storage=False, slot=None)
                clear_holding(self.current_state)
                self.current_state["last_updated"] = datetime.now().isoformat()
            return

        comp = self.component_map.get(target_id)
        if not comp:
            print(f"[REAL LAB] PLACE_FROM_HOVER: {target_id} not in component_map.")
            return

        tx_robot, ty_robot = lab_table_xy_to_robot_xy(tx, ty)
        place_angle = find_angle([180.0, 0.0, -trot])
        safe_z = _optional_float(target_pose, "safe_z")

        with self._state_lock:
            self.current_state["system_status"] = SYSTEM_STATUS_BUSY
        try:
            await asyncio.to_thread(
                self.experiment.place_from_hover_cloudlab,
                comp,
                tx_robot,
                ty_robot,
                place_angle,
                safe_z=safe_z,
            )
        except Exception as e:
            print(f"[REAL LAB] place_from_hover: lab_automation failed: {e!r}")
            with self._state_lock:
                self.current_state["system_status"] = SYSTEM_STATUS_HOLDING
            raise

        with self._state_lock:
            ce = (self.current_state.get("components") or {}).get(target_id)
            if isinstance(ce, dict):
                tun = ce.setdefault("tunables", default_tunables())
                meas = ce.setdefault("measurables", default_measurables())
                tun["nominal_pose"] = {"x": tx, "y": ty, "rotation": trot}
                tun["placement"] = {"mode": PLACEMENT_MODE_MANUAL}
                meas["pose"] = {"x": tx, "y": ty, "rotation": trot}
                set_presence_and_storage(ce, PRESENCE_BREADBOARD, in_storage=False, slot=None)
            clear_holding(self.current_state)
            self.current_state["last_updated"] = datetime.now().isoformat()
        print(
            f"[REAL LAB] PLACE_FROM_HOVER complete: IDLE, placed {target_id} @ "
            f"x={tx:.1f} y={ty:.1f} rot={trot:.1f}"
        )

    async def scan_rotate_in_place(self, target_id: str, params: Dict[str, Any]):
        """
        Constant-rate theta sweep. User-facing intent is identical in both
        branches (rotate ``target_id`` from ``theta_min`` to ``theta_max`` at
        ``speed_deg_per_s``), but the underlying ``lab_automation`` call
        depends on whether the part is currently held or sitting on the
        table:

        - ``system_status == "HOLDING"`` and held tag matches ``target_id``
          -> ``scan_rotate_held_cloudlab`` (rotate the part that is already
          gripped by the arm, XY + Z locked; system stays HOLDING).
        - ``system_status == "IDLE"`` and the part is on the breadboard
          -> ``scan_rotate_placed_cloudlab`` (arm transiently grips the
          placed part, rotates the wrist/end-effector through the sweep,
          then releases and retracts -- the part stays on the table at
          the new rotation; system returns to IDLE). The arm-grip path is
          used instead of a per-part motor because the arm's rotation
          range is much larger, and because it works for any component in
          the catalog regardless of whether it has a motorized mount.
          From cloud-labs' point of view this primitive is atomic: we do
          NOT set ``HOLDING`` or populate ``holding`` during the sweep.

        When methods are missing on ``OpticalExperiment``, falls back to
        placeholder timing (if ``HOVER_PLACEHOLDER_STATE=1``) or no-op.
        """
        self._holding_placeholder_log(
            "SCAN_ROTATE_IN_PLACE", target_id, {"params": dict(params or {})}
        )
        with self._state_lock:
            holding_now = is_holding(self.current_state)
            held = held_tag(self.current_state)
            status = self.current_state.get("system_status")
            comp_snapshot = (self.current_state.get("components") or {}).get(target_id)

        # --- Decide which branch (held vs placed) ---------------------------
        # These refusals are real input-validation errors -- they fire
        # regardless of ``HOVER_PLACEHOLDER_STATE``, so the log lines don't
        # carry the PLACEHOLDER tag.
        if holding_now:
            if held and held != target_id:
                print(
                    f"[REAL LAB] refusing SCAN_ROTATE_IN_PLACE; "
                    f"currently holding {held}, not {target_id}."
                )
                return
            mode = "held"
        else:
            if status != SYSTEM_STATUS_IDLE:
                print(
                    f"[REAL LAB] refusing SCAN_ROTATE_IN_PLACE; "
                    f"system_status={status}, need IDLE or HOLDING."
                )
                return
            if not comp_snapshot:
                print(f"[REAL LAB] SCAN_ROTATE_IN_PLACE: {target_id} not in state.")
                return
            presence = ((comp_snapshot.get("tunables") or {}).get("presence"))
            if presence != PRESENCE_BREADBOARD:
                print(
                    f"[REAL LAB] refusing SCAN_ROTATE_IN_PLACE; "
                    f"{target_id} presence={presence} (need on breadboard)."
                )
                return
            mode = "placed"

        # --- Parse params ---------------------------------------------------
        try:
            theta_min = float(params.get("theta_min", 0.0))
            theta_max = float(params.get("theta_max", 0.0))
            speed = float(params.get("speed_deg_per_s", 1.0))
        except (TypeError, ValueError):
            print("[REAL LAB] SCAN_ROTATE_IN_PLACE: non-numeric params.")
            return
        if speed <= 0:
            print("[REAL LAB] SCAN_ROTATE_IN_PLACE: speed must be > 0.")
            return
        axis = str(params.get("axis", "z"))
        print(
            f"[REAL LAB] SCAN_ROTATE_IN_PLACE mode={mode} target={target_id} "
            f"theta_min={theta_min:.2f} theta_max={theta_max:.2f} speed={speed:g} axis={axis}"
        )

        # --- lab_automation dispatch -----------------------------------------
        if mode == "held":
            real_fn = (
                getattr(self.experiment, "scan_rotate_held_cloudlab", None)
                or getattr(self.experiment, "scan_rotate_in_place_held_cloudlab", None)
            )
        else:
            real_fn = (
                getattr(self.experiment, "scan_rotate_placed_cloudlab", None)
                or getattr(self.experiment, "scan_rotate_in_place_placed_cloudlab", None)
            )
        if callable(real_fn):
            comp = self.component_map.get(target_id)
            with self._state_lock:
                self.current_state["system_status"] = SYSTEM_STATUS_BUSY
            scan_ok = False
            try:
                kwargs = dict(
                    component=comp,
                    theta_min=theta_min,
                    theta_max=theta_max,
                    speed_deg_per_s=speed,
                    axis=axis,
                )
                if mode == "placed":
                    sz = _optional_float(params, "safe_z")
                    if sz is not None:
                        kwargs["safe_z"] = sz

                def _invoke_scan():
                    return real_fn(**kwargs)

                await asyncio.to_thread(_invoke_scan)
                scan_ok = True
            except Exception as e:
                print(f"[REAL LAB] scan_rotate_in_place ({mode}) raised {e!r}")
                with self._state_lock:
                    self.current_state["system_status"] = (
                        SYSTEM_STATUS_HOLDING if mode == "held" else SYSTEM_STATUS_IDLE
                    )
                    self.current_state["last_updated"] = datetime.now().isoformat()
                raise
            finally:
                if scan_ok:
                    with self._state_lock:
                        self.current_state["system_status"] = (
                            SYSTEM_STATUS_HOLDING if mode == "held" else SYSTEM_STATUS_IDLE
                        )
                        comp_state = (self.current_state.get("components") or {}).get(target_id)
                        if isinstance(comp_state, dict):
                            tun = comp_state.setdefault("tunables", default_tunables())
                            meas = comp_state.setdefault("measurables", default_measurables())
                            np_ = dict(tun.get("nominal_pose") or {})
                            np_["rotation"] = float(theta_max)
                            tun["nominal_pose"] = np_
                            mp_ = dict(meas.get("pose") or {})
                            mp_["rotation"] = float(theta_max)
                            meas["pose"] = mp_
                        if mode == "held":
                            hld = get_holding(self.current_state)
                            hnp = dict(hld.get("nominal_pose") or {})
                            hnp["rotation"] = float(theta_max)
                            hld["nominal_pose"] = hnp
                            self.current_state["holding"] = hld
                        self.current_state["last_updated"] = datetime.now().isoformat()
            if scan_ok:
                final_status = "HOLDING" if mode == "held" else "IDLE"
                print(
                    f"[REAL LAB] SCAN_ROTATE_IN_PLACE complete ({mode}): "
                    f"{final_status}, {target_id} @ rotation={theta_max:.2f}"
                )
            return

        # --- Placeholder-only path (no lab_automation hook yet) -------------
        if not self._hover_placeholder_state:
            # Safe default: no state mutation. Live table: no motion either.
            return

        total = abs(theta_max - theta_min)
        duration = max(0.1, total / speed)
        steps = max(1, int(duration / 0.1))
        with self._state_lock:
            self.current_state["system_status"] = SYSTEM_STATUS_BUSY
        for i in range(steps + 1):
            theta = theta_min + (theta_max - theta_min) * (i / steps if steps else 1.0)
            with self._state_lock:
                comp_state = (self.current_state.get("components") or {}).get(target_id)
                if isinstance(comp_state, dict):
                    tun = comp_state.setdefault("tunables", default_tunables())
                    meas = comp_state.setdefault("measurables", default_measurables())
                    np_ = dict(tun.get("nominal_pose") or {})
                    np_["rotation"] = float(theta)
                    tun["nominal_pose"] = np_
                    mp_ = dict(meas.get("pose") or {})
                    mp_["rotation"] = float(theta)
                    meas["pose"] = mp_
                if mode == "held":
                    hld = get_holding(self.current_state)
                    hnp = dict(hld.get("nominal_pose") or {})
                    hnp["rotation"] = float(theta)
                    hld["nominal_pose"] = hnp
                    self.current_state["holding"] = hld
                self.current_state["last_updated"] = datetime.now().isoformat()
            await asyncio.sleep(duration / max(1, steps))
        with self._state_lock:
            self.current_state["system_status"] = (
                SYSTEM_STATUS_HOLDING if mode == "held" else SYSTEM_STATUS_IDLE
            )
            self.current_state["last_updated"] = datetime.now().isoformat()

    async def confirm_holding_tag(self, tag_id: str):
        """
        Operator confirms which tag is physically in the gripper after a
        boot-time HOLDING_UNCONFIRMED reconciliation (see §6.3). Clears the
        ``requires_operator_confirm`` flag and stamps ``holding.tag_id`` so
        the UI unblocks cross-part commands.

        Note: this method mutates state regardless of the placeholder flag
        (it never moves the robot; it only records what the operator says).
        """
        print(f"[REAL LAB] CONFIRM_HOLDING_TAG {tag_id}")
        with self._state_lock:
            if not is_holding(self.current_state):
                print("[REAL LAB] confirm_holding_tag: system not HOLDING; nothing to confirm.")
                return
            _confirm_holding_tag(self.current_state, tag_id)
            self.current_state["last_updated"] = datetime.now().isoformat()
