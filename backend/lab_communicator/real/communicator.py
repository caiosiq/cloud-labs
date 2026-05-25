import atexit
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
from typing import Any, Callable, Dict, List, Optional, Tuple
import numpy as np
from scipy.spatial.transform import Rotation as R

from lab_model import motor_rotation_store as motor_rot
from lab_model.domain.component import (
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
from lab_model.domain.holding import (
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
from lab_model.domain.storage_region import (
    STORAGE_NOMINAL_ROTATION_DEG,
    find_storage_slot_and_center,
    is_placed_region,
    is_storage_region,
    nominal_center_pose_for_stored_entry,
)

from lab_communicator.base import LabCommunicator
from lab_communicator.shared.lab_view_config import get_lab_view_paths
from lab_model.state.snapshot import LabPose

# ``lab_automation_path`` comes from ``lab_manifest.json`` (via bootstrap ? os.environ).
LAB_AUTOMATION_PATH = os.getenv("LAB_AUTOMATION_PATH")
if LAB_AUTOMATION_PATH and os.path.exists(LAB_AUTOMATION_PATH):
    _lab_parent = os.path.dirname(LAB_AUTOMATION_PATH)
    if _lab_parent not in sys.path:
        sys.path.insert(0, _lab_parent)
    print(
        f"[REAL LAB] Added parent {_lab_parent} to sys.path "
        f"(lab_automation from lab_manifest.json: {LAB_AUTOMATION_PATH})"
    )
else:
    print(
        "[REAL LAB] Warning: lab_automation_path not set in lab_manifest.json or path invalid."
    )

# Import Real Lab Automation
try:
    from lab_automation.managers.experiment_manager import OpticalExperiment
    from lab_automation.objects.base import OpticalComponent, Pose
    # ``find_angle``, ``NewtonPlacementStrategy_cloudlab``, and
    # ``CobylaAlignmentStrategy_cloudlab`` are imported lazily inside
    # the matching ``primitive_*`` functions in
    # :mod:`lab_communicator.real.primitives`; communicator.py no
    # longer needs them at module scope.

    LAB_LIB_AVAILABLE = True
except ImportError as e:
    print(f"[REAL LAB] Critical Error: Failed to import lab_automation: {e}")
    LAB_LIB_AVAILABLE = False

try:
    from lab_automation.managers.recorder_capture_helpers_cloudlab import activate_cam_and_capture
    RECORDER_CAPTURE_AVAILABLE = True
except ImportError:
    activate_cam_and_capture = None
    RECORDER_CAPTURE_AVAILABLE = False

# --- Coordinate frames + utility helpers (Phase 1 of the communicator
# refactor; see ``communicator_refactor.md`` ?10) ---
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
)


# Back-compat alias preserving the old ``[REAL LAB]`` log prefix on
# stale-env-var warnings without forcing every call site to pass it.
def _env_float(name: str, default: float) -> float:
    return _env_float_shared(name, default, log_prefix="[REAL LAB]")


class RealLabCommunicator(LabCommunicator):
    log_prefix = "[REAL LAB]"
    # Calibrated safe-hover bound (lab frame), forwarded from
    # ``real/coordinate_frames.py``. The base orchestrator uses it to
    # refuse runaway HTTP payloads in :meth:`hover_component` before
    # they hit the forward transform.
    max_safe_hover_z_lab_mm = MAX_SAFE_HOVER_Z_LAB_MM

    def __init__(self):
        if not LAB_LIB_AVAILABLE:
            raise RuntimeError("lab_automation library not available. Cannot start RealLabCommunicator.")

        # Base seeds ``current_state`` (with empty components / IDLE),
        # ``catalog_map``, and the state lock. Phase 2A made base the
        # owner of all three -- see ``communicator_refactor.md`` ?5.2.
        super().__init__()

        print("[REAL LAB] Initializing OpticalExperiment...")
        print(
            "[REAL LAB] Bench calibration is loaded from lab_automation/data/calibration/ "
            "(package path, not cloud-labs cwd)."
        )
        # Initialize the experiment manager.
        #
        # Phase 5 of ``universal_component_architecture.md`` (§16.4 / §17.1):
        # cloud-labs owns the catalog and passes it to ``lab_automation``
        # at construction time so the hardware side never imports cloud-labs.
        # The kwarg is *opt-in* on the lab_automation side -- if the
        # installed version doesn't recognize ``catalog=``, we fall back
        # to the legacy zero-arg constructor so this commit can land
        # before the matching lab_automation PR.
        catalog_doc = self._load_catalog_for_lab_automation()
        try:
            import inspect  # noqa: PLC0415

            sig = inspect.signature(OpticalExperiment.__init__)
            if "catalog" in sig.parameters and catalog_doc is not None:
                self.experiment = OpticalExperiment(mock=False, catalog=catalog_doc)
                print(
                    f"[REAL LAB] Passed catalog to OpticalExperiment "
                    f"(schema_version={catalog_doc.get('schema_version')}, "
                    f"{len(catalog_doc.get('components') or {})} components)"
                )
            else:
                if catalog_doc is not None:
                    print(
                        "[REAL LAB] OpticalExperiment.__init__ does not accept "
                        "'catalog=' yet; falling back to legacy constructor. "
                        "Update lab_automation per CLOUDLAB_CONTRACT.md."
                    )
                self.experiment = OpticalExperiment(mock=False)
        except Exception as exc:  # pragma: no cover -- defensive
            print(f"[REAL LAB] Catalog passthrough failed ({exc!r}); using legacy constructor.")
            self.experiment = OpticalExperiment(mock=False)
        self.experiment.initialize_robot()

        # Cache synced from ``experiment.registry`` manipulables (Phase 8).
        self.component_map: Dict[str, Any] = {}
        self._sync_component_map_from_registry()
        self._hardware_teleop_tags: set[str] = set()
        self._hardware_teleop_was_executing: Dict[str, bool] = {}
        self._teleop_estop_tags: set[str] = set()
        self._teleop_start_params: Dict[str, Any] = {}
        self._table_cam_stream_profile: Dict[int, str] = {1: "default", 2: "default"}

        # ``HOVER_PLACEHOLDER_STATE`` was deleted in Phase 2B of the
        # communicator refactor: MockLabCommunicator now serves the
        # "exercise the UI without moving the table" use case. Real
        # always dispatches to ``lab_automation`` -- no placeholder
        # state-mutation branch exists. (The env var is silently
        # ignored if still set; consider it deprecated.)
        self._place_cloudlab_orig: Any = None
        # The flag-based dispatch (``_place_from_storage_tag``,
        # ``_store_component_tag``, ``_store_pending_slot``) used in the
        # pre-Phase-2C ``move_component`` was retired: the orchestrator
        # now picks the commit shape (BREADBOARD vs STORAGE) directly,
        # so ``_primitive_move_component`` does not need to peek at any side-channel
        # state to know what to do.

        # Tags intentionally in storage (tag_id -> {i,j}); persisted under lab_view/stored_intent.json
        self._stored_intent: Dict[str, Dict[str, int]] = {}
        self._load_stored_intent_from_disk()

        self._initialize_state()
        # Boot-time gripper reconciliation (see new_primitives.md #6.3).
        # MUST run AFTER _initialize_state so component poses exist for the
        # "best-effort hold pose" fallback, and BEFORE the recorder / monitor
        # threads so any HOLDING_UNCONFIRMED is visible on the very first
        # GET /api/lab-state that the UI issues.
        self._reconcile_holding_on_boot()
        from lab_communicator.real.gripper import sync_experiment_holding_from_cloud_state

        sync_experiment_holding_from_cloud_state(self)
        self._recorder_procs: List[subprocess.Popen] = []
        self._use_cloudlab_table_recorder = False
        self._table_cam_recorder_mock = False
        self._table_cam_connected = {1: False, 2: False}
        self._table_cam_streaming = {1: False, 2: False}
        self._table_cam_hardware = {1: "none", 2: "none"}
        self._table_cam_last_error: Dict[int, Any] = {1: None, 2: None}
        self._table_cam_lock = threading.Lock()
        self._start_recorder_processes()
        if not self._use_cloudlab_table_recorder:
            # Legacy recorder: mark connected only when subprocess + TCP port are up.
            from lab_communicator.real.video import (  # noqa: PLC0415
                _probe_recorder_port,
                _recorder_port_for_cam,
                _recorder_procs_alive,
            )

            if _recorder_procs_alive(self):
                for cid in (1, 2):
                    if _probe_recorder_port(_recorder_port_for_cam(cid)):
                        self._table_cam_connected[cid] = True
                        self._table_cam_hardware[cid] = (
                            "mock" if self._table_cam_recorder_mock else "real"
                        )

        # Fallback when image names have no stepNN: count once per new basename (avoids double bumps on mtime+size).
        self._last_optimization_image_basename: Optional[str] = None
        # During OPTIMIZING, watch only this run's subdirectory (see _make_optimization_run_dir).
        self._active_optimization_image_dir: Optional[str] = None

        # Start a background thread to monitor optimization steps reliably
        self._opt_monitor_thread = threading.Thread(target=self._monitor_optimization_dir, daemon=True)
        self._opt_monitor_thread.start()

    @staticmethod
    def _load_catalog_for_lab_automation() -> Optional[Dict[str, Any]]:
        """Read the active lab_view catalog as a v1 doc for lab_automation.

        Returns ``None`` if the bundle is not bootstrapped (unit test paths)
        or the file is still in legacy array shape -- in which case the
        hardware side keeps its own hardcoded defaults. Once the migration
        script has run on the active bundle, this returns the full
        ``{schema_version, components}`` document.
        """
        try:
            from lab_communicator.shared.lab_view_config import (  # noqa: PLC0415
                get_lab_view_paths_optional,
            )
            from lab_model.catalog.schema import (  # noqa: PLC0415
                is_v1_object_shape,
            )

            paths = get_lab_view_paths_optional()
            if paths is None:
                return None
            with open(paths.component_library_json, "r", encoding="utf-8") as f:
                data = json.load(f)
            if is_v1_object_shape(data):
                return data
            return None
        except Exception as exc:
            print(f"[REAL LAB] _load_catalog_for_lab_automation skipped: {exc!r}")
            return None

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
        return get_lab_view_paths().stored_intent_json

    def _load_stored_intent_from_disk(self) -> None:
        """Hydrate ``self._stored_intent`` from ``stored_intent.json``
        under ``LAB_VIEW_PATH``.

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

    def refresh_pose_from_camera(
        self, preserve_tag_ids: Optional[List[str]] = None
    ) -> None:
        """Re-scan the table; optional ``preserve_tag_ids`` freeze whole component rows."""

        from lab_communicator.real.scan import refresh_pose_from_camera as rpc

        rpc(self, preserve_component_ids=preserve_tag_ids)

    def refresh_state(self):
        """Deprecated name; use :meth:`refresh_pose_from_camera`."""
        self.refresh_pose_from_camera(None)

    # ``set_lab_state`` and ``get_lab_state`` now live on the base
    # template class (Phase 2A of the communicator refactor). Real
    # provides the ``_apply_loaded_pose_to_hardware`` /
    # ``_post_apply_snapshot`` hooks below; everything else (merge
    # logic, holding reset, status normalization, lock acquisition,
    # motor-rotation injection on read) is shared in
    # ``lab_communicator.base`` + ``lab_model.state.snapshot``.

    def get_manipulable(self, tag_id: str) -> Any:
        """Registry manipulable for ``tag_id`` (falls back to ``component_map``)."""
        exp = getattr(self, "experiment", None)
        if exp is not None and hasattr(exp, "get_manipulable"):
            found = exp.get_manipulable(str(tag_id))
            if found is not None:
                return found
        return self.component_map.get(str(tag_id))

    def _sync_component_map_from_registry(self) -> None:
        """Mirror ``experiment.registry.manipulables()`` into ``component_map``."""
        exp = getattr(self, "experiment", None)
        if exp is None or not hasattr(exp, "list_manipulables"):
            return
        self.component_map = {m.tag_id: m for m in exp.list_manipulables()}

    def _apply_loaded_pose_to_hardware(
        self, tag_id: str, lab_pose: LabPose, *, is_placed: bool
    ) -> None:
        """Push a loaded snapshot pose into the ``lab_automation`` component.

        This is the **marquee Stage C exemption** -- the only method
        allowed to write ``OpticalComponent.current_location``. The
        hook conventions in ``lab_communicator/README.md`` enforce this. Snapshot poses are lab / UI frame; the
        ``lab_automation`` library expects robot frame. All three axes
        are transformed through the dedicated helpers so the cross-wall
        convention stays in one place (``fixing.md`` ?3, ?3.1, ?5):

        - **XY**       :func:`lab_table_xy_to_robot_xy` (calibrated rotation)
        - **Z**        :meth:`_z_lab_to_robot`            (per-tag; uses catalog ``height_mm``)
        - **rotation** :func:`lab_rotation_to_robot_yaw`  (identity today; see module comment)

        We write only ``current_location`` and ``is_placed``;
        ``inventory_location`` is owned elsewhere in ``lab_automation``
        (see ``labautomation_new_primitives.md`` ?5).
        """
        comp = self.get_manipulable(tag_id)
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
        :func:`lab_model.state.placement_ui.ui_pose_for_placement_tick`.
        Real passes :func:`lab_communicator.real.coordinate_frames.robot_table_xy_to_lab_xy`
        as the XY transform; mock passes the identity transform from
        ``mock/coordinate_frames.py``.
        """
        from lab_model.state.placement_ui import ui_pose_for_placement_tick
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
        :func:`lab_model.state.placement_ui.apply_placement_ui_phase`.
        """
        from lab_model.state.placement_ui import apply_placement_ui_phase
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
        :func:`lab_model.catalog.lookup.catalog_wh`."""
        from lab_model.catalog.lookup import catalog_wh
        return catalog_wh(self.catalog_map.get, tag_id)

    # --- Z-frame transforms (see module-level comment for the convention) ---
    def _component_height_mm(self, tag_id: str) -> float:
        """Physical height of a component (base to top, mm).

        Thin wrapper around
        :func:`lab_model.catalog.lookup.component_height_mm`.
        Defaults to ``DEFAULT_COMPONENT_HEIGHT_MM`` (env-tunable) on a
        missing/malformed catalog entry.
        """
        from lab_model.catalog.lookup import component_height_mm
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
        comp = self.get_manipulable(tag_id)
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

    # ---------------------------------------------------------------
    # Primitive hooks
    # ---------------------------------------------------------------
    #
    # The methods below are the complete list of ``_primitive_*``
    # hooks the base orchestrator dispatches for the real backend.
    # Each one is a single-line delegation to the matching
    # ``primitive_<name>`` free function in
    # :mod:`lab_communicator.real.primitives`, where the actual
    # ``lab_automation`` API call lives. Reading this section answers
    # "what primitives does the real communicator implement?"; reading
    # ``primitives.py`` answers "what API call does each primitive
    # make?".

    async def _primitive_move_component(
        self, target_id: str, commanded: LabPose
    ) -> Optional[LabPose]:
        from lab_communicator.real.primitives import primitive_move_component
        return await primitive_move_component(self, target_id, commanded)

    async def _primitive_move_motor(
        self, target_id: str, motor_id: int, distance: float
    ) -> None:
        from lab_communicator.real.primitives import primitive_move_motor
        await primitive_move_motor(self, target_id, motor_id, distance)

    def _primitive_prepare_optimization_run(
        self, target_id: str, strategy_name: str
    ) -> Optional[str]:
        from lab_communicator.real.primitives import primitive_prepare_optimization_run
        return primitive_prepare_optimization_run(self, target_id, strategy_name)

    async def _primitive_optimize_component(
        self,
        *,
        target_id: str,
        strategy_name: str,
        params: Dict[str, Any],
        progress_callback: "Callable[..., None]",
    ) -> Optional[Dict[str, Any]]:
        from lab_communicator.real.primitives import primitive_optimize_component
        return await primitive_optimize_component(
            self,
            target_id=target_id,
            strategy_name=strategy_name,
            params=params,
            progress_callback=progress_callback,
        )

    def _primitive_finalize_optimization_run(self) -> None:
        from lab_communicator.real.primitives import primitive_finalize_optimization_run
        primitive_finalize_optimization_run(self)

    async def remove_component(self, target_id: str):
        """Real backend: removal happens via physical-scan delta.

        Override of the base orchestrator that would mutate state.
        Real's component inventory is rebuilt from physical scans
        (see ``_initialize_state``), so an API-level ``remove`` would
        be immediately overwritten on the next rescan and is therefore
        intentionally a no-op.
        """
        print(f"[REAL LAB] Remove requested for {target_id} (Not implemented)")

    async def _primitive_add_component_to_state(
        self,
        component_data: Dict[str, Any],
        existing_components: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        from lab_communicator.real.primitives import primitive_add_component_to_state
        return await primitive_add_component_to_state(
            self, component_data, existing_components
        )

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

    def capture_overhead_cam(self, exposure: float = 0.2):
        """Capture one still from the table-overview USB camera. Returns PNG bytes or ``None``."""
        from lab_communicator.real.video import capture_overhead_cam

        return capture_overhead_cam(self, exposure)

    def table_cam_connect(self, cam_id: int) -> Tuple[bool, str]:
        from lab_communicator.real.video import table_cam_connect

        return table_cam_connect(self, cam_id)

    def table_cam_disconnect(self, cam_id: int) -> Tuple[bool, str]:
        from lab_communicator.real.video import table_cam_disconnect

        return table_cam_disconnect(self, cam_id)

    def table_cam_live_set(
        self, cam_id: int, enabled: bool, *, profile: str = "default"
    ) -> Tuple[bool, str]:
        from lab_communicator.real.video import table_cam_live_set

        return table_cam_live_set(self, cam_id, enabled, profile=profile)

    def table_cam_send_vexp(self, cam_id: int, exposure_s: float) -> Tuple[bool, str]:
        from lab_communicator.real.video import table_cam_send_vexp

        return table_cam_send_vexp(self, cam_id, exposure_s)

    def table_cam_send_vgain(self, cam_id: int, gain: float) -> Tuple[bool, str]:
        from lab_communicator.real.video import table_cam_send_vgain

        return table_cam_send_vgain(self, cam_id, gain)

    def get_table_cam_stream(self, cam_id: int, fps: int = 18):
        """MJPEG-ish multipart stream for `/api/table-cam/stream`.

        Implemented only for backends that wired the communicator hooks; callers
        should ``yield from`` this helper to preserve iteration semantics.
        """
        from lab_communicator.real.video import get_table_cam_stream

        yield from get_table_cam_stream(self, cam_id, fps)

    def fetch_table_cam_preview_jpeg(self, cam_id: int = 1) -> Optional[Any]:
        from lab_communicator.real.video import fetch_table_cam_preview_jpeg

        return fetch_table_cam_preview_jpeg(self, int(cam_id))

    def get_table_cam_status(self, only_cam_id: Optional[int] = None) -> Dict[str, Any]:
        from lab_communicator.real.video import table_cam_status_snapshot

        return table_cam_status_snapshot(self, only_cam_id=only_cam_id)

    async def _primitive_record_measurables(
        self, tag_id: str, catalog_meta: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        from lab_communicator.real.primitives import primitive_record_measurables
        return await primitive_record_measurables(self, tag_id, catalog_meta)

    # --- Storage-intent + is_placed virtual hooks (Phase 2C) ---
    #
    # The base orchestrators (``store_component``, ``place_from_storage``,
    # ``move_component``, ``affirm_placed_at_current``) call these
    # hooks after a successful state commit. Mock leaves them as
    # no-ops; real persists the storage-intent file and propagates
    # ``is_placed`` to :class:`OpticalComponent`.

    def _after_move_to_storage(
        self, target_id: str, slot_i: int, slot_j: int
    ) -> None:
        self._stored_intent_set_slot(target_id, int(slot_i), int(slot_j))

    def _after_move_out_of_storage(self, target_id: str) -> None:
        self._stored_intent_remove(target_id)

    def _apply_is_placed_flag(self, target_id: str, value: bool) -> None:
        cobj = self.get_manipulable(target_id)
        if cobj is not None:
            cobj.is_placed = bool(value)

    # --- In-air manipulation (see ``new_primitives.md`` ?6 and ?8.3) ---
    #
    # Stage 8 wiring is live: ``lab_automation`` ships
    # ``pick_component_cloudlab``, ``hover_component_cloudlab``,
    # ``place_from_hover_cloudlab``, ``scan_rotate_held_cloudlab``,
    # ``scan_rotate_placed_cloudlab`` (see
    # ``labautomation_new_primitives.md`` ?2). Each primitive below dispatches
    # to the corresponding method via ``asyncio.to_thread`` and updates
    # ``current_state`` on success.
    #
    # ``HOVER_PLACEHOLDER_STATE=1`` keeps a debug escape hatch that bypasses
    # the real call and only mutates ``current_state`` (matches the
    # mock-lab behavior for UI testing without moving the robot). Left in
    # place intentionally per ``labautomation_new_primitives.md`` ?6
    # item 3 ("remove or keep as debug escape hatch").

    def _reconcile_holding_on_boot(self) -> None:
        """Boot-time HOLDING reconciliation (see ``new_primitives.md`` ?6.3).

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

    def _hardware_set_laser_output_power_mw(
        self, tag_id: str, power_mw: float
    ) -> Tuple[bool, str]:
        exp = getattr(self, "experiment", None)
        if exp is None:
            return False, "experiment unavailable"
        laser = exp.get_laser_component(tag_id) if hasattr(exp, "get_laser_component") else None
        if laser is None:
            return False, f"no laser component for {tag_id!r}"
        return laser.set_output_power_mw(float(power_mw))

    def _hardware_read_laser_output_power_mw(self, tag_id: str) -> Optional[float]:
        exp = getattr(self, "experiment", None)
        if exp is None:
            return None
        laser = exp.get_laser_component(tag_id) if hasattr(exp, "get_laser_component") else None
        if laser is None:
            return None
        return laser.read_output_power_mw()

    async def confirm_holding_tag(self, tag_id: str) -> None:
        await super().confirm_holding_tag(tag_id)
        from lab_communicator.real.gripper import sync_experiment_holding_from_cloud_state

        sync_experiment_holding_from_cloud_state(self)

    # --- TeleOp hardware bridge (Phase 6) ---

    async def _primitive_prepare_teleop(self, target_id: str) -> Tuple[bool, str]:
        """Enter lab_automation ``LiveControlSession`` (table Rz or held pose3d)."""
        if not LAB_LIB_AVAILABLE:
            return False, "lab_automation not available"
        if not getattr(self, "experiment", None):
            return False, "OpticalExperiment not initialized"
        if self.get_manipulable(target_id) is None:
            return False, f"unknown component {target_id!r} (rescan first)"

        from lab_communicator.real.teleop_bridge import start_hardware_session
        from lab_communicator.shared.util import optional_float
        from lab_automation.utils.clearance import validate_safe_z_optional

        params = dict(getattr(self, "_teleop_start_params", {}) or {})
        safe_z_raw = optional_float(params, "safe_z")
        safe_z = None
        if safe_z_raw is not None:
            try:
                safe_z = validate_safe_z_optional(
                    float(safe_z_raw), label="teleop safe_z"
                )
            except ValueError as exc:
                return False, str(exc)

        def _enter() -> None:
            start_hardware_session(self, target_id, safe_z=safe_z)

        try:
            await asyncio.to_thread(_enter)
        except Exception as exc:  # noqa: BLE001
            print(f"[REAL LAB] teleop prepare failed for {target_id}: {exc!r}")
            return False, str(exc)

        self._hardware_teleop_tags.add(target_id)
        self._hardware_teleop_was_executing[target_id] = False
        print(f"[REAL LAB] TELEOP hardware session ready for {target_id}")
        return True, "ok"

    def _teleop_live_start(self, tag_id: str, initial_pose: Dict[str, Any]) -> None:
        if tag_id in self._hardware_teleop_tags:
            return
        super()._teleop_live_start(tag_id, initial_pose)

    def _teleop_live_get_pose(self, tag_id: str) -> Optional[Dict[str, Any]]:
        if tag_id in self._hardware_teleop_tags:
            from lab_communicator.real.teleop_bridge import read_hardware_live_pose

            return read_hardware_live_pose(self, tag_id)
        return super()._teleop_live_get_pose(tag_id)

    def get_teleop_live_pose(self, tag_id: str) -> Optional[Dict[str, Any]]:
        if tag_id in self._hardware_teleop_tags and tag_id not in self._teleop_estop_tags:
            from lab_communicator.real.teleop_bridge import check_gripper_slip_during_teleop

            slip = check_gripper_slip_during_teleop(self, tag_id)
            if slip:
                self._teleop_estop_sync(tag_id, slip)
                return None

        pose = super().get_teleop_live_pose(tag_id)
        if tag_id in self._hardware_teleop_tags and pose is not None:
            was = self._hardware_teleop_was_executing.get(tag_id, False)
            now = bool(pose.get("executing"))
            if was and not now:
                self._on_teleop_motion_idle(tag_id)
            self._hardware_teleop_was_executing[tag_id] = now
        return pose

    def _teleop_estop_sync(self, tag_id: str, reason: str) -> None:
        """Stop hardware TeleOp and mark session failed (gripper slip / safety)."""
        if tag_id in self._teleop_estop_tags:
            return
        self._teleop_estop_tags.add(tag_id)
        print(f"[REAL LAB] TELEOP estop for {tag_id}: {reason}")
        self._teleop_live_stop_sync(tag_id)
        from lab_model.state.commits import commit_teleop_start_failed

        with self._state_lock:
            commit_teleop_start_failed(self.current_state, tag_id, error=reason)
            self.current_state["last_updated"] = datetime.now().isoformat()
        try:
            self._persist_state()
        except Exception as exc:  # noqa: BLE001
            print(f"[REAL LAB] teleop estop persist failed: {exc!r}")
        self._teleop_estop_tags.discard(tag_id)

    def _teleop_live_set_goto(
        self,
        tag_id: str,
        target: Dict[str, Any],
        speed: Dict[str, Any],
    ) -> None:
        if tag_id in self._hardware_teleop_tags:
            from lab_communicator.real.teleop_bridge import enqueue_hardware_goto

            enqueue_hardware_goto(self, tag_id, target, speed)
            self._hardware_teleop_was_executing[tag_id] = True
            return
        super()._teleop_live_set_goto(tag_id, target, speed)

    def _teleop_live_stop_sync(self, tag_id: str) -> None:
        if tag_id in self._hardware_teleop_tags:
            from lab_communicator.real.teleop_bridge import stop_hardware_session

            stop_hardware_session(self)
            self._hardware_teleop_tags.discard(tag_id)
            self._hardware_teleop_was_executing.pop(tag_id, None)
            return
        super()._teleop_live_stop_sync(tag_id)

    async def _teleop_live_stop(self, tag_id: str) -> None:
        if tag_id in self._hardware_teleop_tags:
            await asyncio.to_thread(self._teleop_live_stop_sync, tag_id)
            return
        await super()._teleop_live_stop(tag_id)

    # --- In-air manipulation hooks (Phase 2B; see new_primitives.md) ---

    async def _primitive_pick_component(
        self, target_id: str, commanded: LabPose, params: Dict[str, Any]
    ) -> float:
        from lab_communicator.real.primitives import primitive_pick_component
        return await primitive_pick_component(self, target_id, commanded, params)

    async def _primitive_hover_component(
        self, target_id: str, commanded: LabPose, speed: int
    ) -> Optional[LabPose]:
        from lab_communicator.real.primitives import primitive_hover_component
        return await primitive_hover_component(self, target_id, commanded, speed)

    async def _primitive_place_from_hover(
        self, target_id: str, commanded: LabPose, params: Dict[str, Any]
    ) -> None:
        from lab_communicator.real.primitives import primitive_place_from_hover
        await primitive_place_from_hover(self, target_id, commanded, params)

    async def _primitive_scan_rotate_in_place(
        self,
        *,
        target_id: str,
        mode: str,
        theta_min: float,
        theta_max: float,
        speed: float,
        axis: str,
        base_x: float,
        base_y: float,
        base_z: Optional[float],
        params: Dict[str, Any],
        on_rotation_update: Callable[[float], None],
    ) -> None:
        from lab_communicator.real.primitives import primitive_scan_rotate_in_place
        await primitive_scan_rotate_in_place(
            self,
            target_id=target_id,
            mode=mode,
            theta_min=theta_min,
            theta_max=theta_max,
            speed=speed,
            axis=axis,
            base_x=base_x,
            base_y=base_y,
            base_z=base_z,
            params=params,
            on_rotation_update=on_rotation_update,
        )
