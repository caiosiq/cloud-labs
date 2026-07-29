import json
import os
import asyncio
import time
from datetime import datetime
from io import BytesIO
from typing import Any, Callable, Dict, List, Optional, Tuple

from lab_model.language.domain.component import (
    PRESENCE_BREADBOARD,
    PRESENCE_STORAGE,
    default_measurables,
)
from lab_model.language.domain.holding import (
    DEFAULT_HOVER_Z_MM,
    SYSTEM_STATUS_BUSY,
    SYSTEM_STATUS_HOLDING,
    SYSTEM_STATUS_IDLE,
    empty_holding,
    get_holding,
    set_holding,
)

from mock_edge.host.base import LabCommunicator
from lab_model.coordinator.catalog.bundle import merged_catalog_maps
from lab_model.coordinator.backends.lab_view_config import get_lab_view_paths
from lab_model.coordinator.state.runtime_manager import MutationKind
from lab_model.coordinator.state.snapshot import LabPose

# Constants
# File now at ``backend/lab_communicator/mock/communicator.py``; the
# project ``schemas/`` directory is three levels up. (Was two levels
# up when the class lived in ``backend/lab_communicator/mock.py``.)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
class MockLabCommunicator(LabCommunicator):
    """
    Mock implementation that simulates a physical lab.
    Uses a local JSON file to persist state.

    **Z convention** (see ``new_primitives.md`` "Z / coordinate convention"):
    every z in mock -- command payloads, ``tunables.nominal_pose.z``,
    ``measurables.pose.z``, ``holding.nominal_pose.z`` -- is **z_lab**:
    the height of a component's *base* above the breadboard surface, in
    millimeters. ``z_lab = 0`` means the part is on the table;
    ``z_lab = DEFAULT_HOVER_Z_MM`` (40) is the default safe hover clearance
    used after a PICK. Mock never talks to a real robot, so there is no
    z_robot here and no z transform happens -- mock *is* the reference
    implementation of the lab-frame convention that ``RealLabCommunicator``
    must round-trip to and from the robot frame.
    """

    log_prefix = "[MOCK LAB]"

    def __init__(self):
        # Phase 2A made base the owner of ``current_state``,
        # ``catalog_map``, and the state lock. Mock additionally
        # mirrors the in-memory state to a JSON file (so a backend
        # restart resumes the previous session, and so test harnesses
        # can seed a known starting state by editing the file).
        super().__init__()

        paths = get_lab_view_paths()
        self.state_file = paths.lab_state_json
        print(f"[MOCK LAB] Using state file: {self.state_file}")
        self.catalog = []
        self.catalog_map = {}
        self._ensure_state()
        # Initial in-memory load from disk. Subsequent mutations go
        # through ``_persist_state`` (migrated primitives) or
        # ``_write_state`` (still-file-backed primitives, both of
        # which keep the in-memory copy in sync).
        from mock_edge.host.persistence import read_state

        self._lab_runtime.replace_state(
            read_state(self.state_file),
            kind=MutationKind.BOOT_HYDRATE,
            source="mock_persistence_load",
        )
        self._load_catalog()
        self._ensure_fixture_components()

        # Dev flag: simulate boot-time gripper-closed reconciliation (see new_primitives.md #6.3).
        self._mock_gripper_closed_on_boot = (
            os.getenv("MOCK_GRIPPER_CLOSED_ON_BOOT", "").strip().lower()
            in ("1", "true", "yes", "on")
        )
        self._table_cam_connected = {1: False, 2: False}
        self._table_cam_streaming = {1: False, 2: False}
        self._table_cam_stream_profile: Dict[int, str] = {1: "default", 2: "default"}
        self._reconcile_holding_on_boot()

    def _reconcile_holding_on_boot(self) -> None:
        """
        Mirror the RealLabCommunicator startup check in #6.3 of new_primitives.md.

        - If the hardware (mock) reports gripper closed and the snapshot does NOT
          already declare a confirmed HOLDING state, force HOLDING_UNCONFIRMED
          (``requires_operator_confirm: true``) regardless of file contents.
        - Otherwise, leave any persisted HOLDING state as-is so mock survives
          restarts mid-hover (simulates the "power outage" recovery).
        """
        try:
            state = self._read_state()
        except Exception:
            return
        status = state.get("system_status")
        status_normalized = SYSTEM_STATUS_IDLE if status in (None, "") else status
        if status_normalized not in (
            SYSTEM_STATUS_IDLE,
            SYSTEM_STATUS_BUSY,
            SYSTEM_STATUS_HOLDING,
            "OPTIMIZING",
        ):
            status_normalized = SYSTEM_STATUS_IDLE

        gripper = self.get_gripper_status()
        gripper_closed = bool(gripper.get("closed"))

        if gripper_closed and status_normalized != SYSTEM_STATUS_HOLDING:
            print(
                "[MOCK LAB] MOCK_GRIPPER_CLOSED_ON_BOOT=1: forcing HOLDING_UNCONFIRMED "
                "(operator must confirm held tag_id)"
            )
            set_holding(
                state,
                tag_id=None,
                x=0.0,
                y=0.0,
                rotation=0.0,
                z=DEFAULT_HOVER_Z_MM,
                requires_operator_confirm_flag=True,
            )
            state["last_updated"] = datetime.now().isoformat()
            self._write_state(state)
            return

        # Ensure the top-level ``holding`` field exists even on a clean IDLE boot
        # so the UI never sees ``undefined``.
        holding = get_holding(state)
        if holding.get("tag_id") is None and not holding.get("requires_operator_confirm"):
            state["holding"] = empty_holding()
        # Normalize a bare status to IDLE if snapshot pre-dates the holding fields.
        if status is None:
            state["system_status"] = SYSTEM_STATUS_IDLE
        self._write_state(state)

    def _ensure_state(self):
        """Validate that the mock-lab JSON exists and is well-formed.

        Thin wrapper -- body in :func:`lab_communicator.mock.persistence.ensure_state_file`.
        """
        from mock_edge.host.persistence import ensure_state_file
        ensure_state_file(self.state_file)

    def _load_catalog(self):
        """Reload library + active tags from ``lab_view`` (list + ``catalog_map``)."""
        runtime_ids = list((self.current_state.get("components") or {}).keys())
        rows, cmap = merged_catalog_maps(runtime_tag_ids=runtime_ids)
        self.catalog = rows
        self.catalog_map = cmap

    def _get_component_size(self, tag_id: str) -> float:
        size = 90.0
        for item in self.catalog:
            if item.get("tag_id") == tag_id:
                s = item.get("size")
                if isinstance(s, dict):
                    size = max(s.get("width", 90), s.get("height", 90))
                elif isinstance(s, (int, float)):
                    size = float(s)
                break
        return size

    def _get_component_wh(self, tag_id: str) -> Tuple[float, float]:
        for item in self.catalog:
            if item.get("tag_id") == tag_id:
                s = item.get("size")
                if isinstance(s, dict):
                    return float(s.get("width", 90)), float(s.get("height", 90))
                if isinstance(s, (int, float)):
                    v = float(s)
                    return v, v
        return 90.0, 90.0

    def _read_state(self) -> Dict[str, Any]:
        """Read and parse the current snapshot from disk.

        Used by primitives that have not yet been migrated to the
        Phase 2 template-method pattern (in-air, heavy-state,
        optimization). Migrated primitives use the in-memory
        ``self.current_state`` directly.

        Thin wrapper -- body in :func:`lab_communicator.mock.persistence.read_state`.
        """
        from mock_edge.host.persistence import read_state
        return read_state(self.state_file)

    def _write_state(self, state: Dict[str, Any]):
        """Persist a snapshot to disk **and** mirror it into ``self.current_state``.

        Phase 2A made base the owner of ``self.current_state``; mock
        keeps the disk file as a persistence sink. Both writer entry
        points (this method, used by un-migrated primitives, and
        :meth:`_persist_state`, used by migrated primitives via the
        base orchestrator) maintain the invariant that disk and memory
        agree -- so :meth:`get_lab_state` can read from
        ``self.current_state`` without first hitting disk.

        Thin wrapper -- body in :func:`lab_communicator.mock.persistence.write_state`.
        """
        from mock_edge.host.persistence import write_state
        write_state(self.state_file, state)
        self._lab_runtime.replace_state(
            state,
            kind=MutationKind.BOOT_HYDRATE,
            source="mock_write_state",
        )

    def _persist_state(self) -> None:
        """Write ``self.current_state`` to the on-disk JSON file.

        Override of :meth:`LabCommunicator._persist_state`. Called by
        the base orchestrator after every state mutation in a migrated
        primitive (:meth:`_set_status`, :meth:`_set_holding`,
        :meth:`_clear_holding`, :meth:`set_lab_state`). Real keeps the
        default no-op.
        """
        from mock_edge.host.persistence import write_state
        with self._state_lock:
            snapshot = json.loads(json.dumps(self.current_state))
        write_state(self.state_file, snapshot)

    # ``get_lab_state`` lives on the base template class (Phase 2A);
    # mock's in-memory ``self.current_state`` is kept in sync with the
    # disk file by :meth:`_write_state` and :meth:`_persist_state`, so
    # the inherited implementation reads from memory and is correct.

    def get_gripper_status(self) -> Dict[str, Any]:
        """
        Mock: ``closed`` only when ``MOCK_GRIPPER_CLOSED_ON_BOOT=1`` at start-up
        AND the current state isn't a clean IDLE (so tests can reset).
        """
        if self._mock_gripper_closed_on_boot:
            return {"closed": True, "confidence": 1.0, "source": "mock_env_flag"}
        return {"closed": False, "confidence": 1.0, "source": "mock"}

    def refresh_pose_from_camera(
        self,
        preserve_tag_ids: Optional[List[str]] = None,
        apply_tag_ids: Optional[List[str]] = None,
        tag_ids: Optional[List[str]] = None,
    ) -> None:
        """Simulate camera re-localisation; scoped via ``tag_ids`` / ``apply_tag_ids``."""
        from mock_edge.shared.mock_scan_preview import (
            apply_mock_scan_to_component,
            build_mock_scan_proposed_poses,
        )
        from lab_model.coordinator.state.pose_refresh_merge import merge_scan_into_components
        from lab_model.coordinator.state.pose_refresh_selection import (
            filter_proposed_poses,
            resolve_pose_refresh_plan,
        )

        state = self._read_state()
        comps = state.get("components") or {}
        if not isinstance(comps, dict):
            return

        baseline = json.loads(json.dumps(comps))
        plan = resolve_pose_refresh_plan(
            baseline,
            tag_ids=tag_ids,
            apply_tag_ids=apply_tag_ids,
            preserve_tag_ids=preserve_tag_ids,
        )
        if not plan.scan_tag_ids:
            print("[MOCK LAB] refresh_pose_from_camera: no tags selected for scan")
            return

        proposed_poses = build_mock_scan_proposed_poses(
            baseline,
            tag_ids=plan.scan_tag_ids,
        )
        proposed_poses = filter_proposed_poses(proposed_poses, plan.scan_tag_ids)

        state["system_status"] = SYSTEM_STATUS_BUSY
        self._write_state(state)

        candidate: Dict[str, Any] = {}
        for tag_id, comp in baseline.items():
            if tag_id not in proposed_poses or not isinstance(comp, dict):
                continue
            candidate[tag_id] = apply_mock_scan_to_component(comp, proposed_poses[tag_id])

        merged_components = merge_scan_into_components(
            baseline,
            candidate,
            plan.preserve_tag_ids,
        )

        state = self._read_state()
        state["components"] = merged_components
        state["system_status"] = SYSTEM_STATUS_IDLE
        state["last_updated"] = datetime.now().isoformat()
        self._write_state(state)
        print(
            "[MOCK LAB] refresh_pose_from_camera: updated measurables.pose "
            f"for {plan.scan_tag_ids} (simulated camera)"
        )

    async def localize_components(
        self,
        tag_ids: Optional[List[str]] = None,
        force_rescan: bool = True,
    ) -> Dict[str, Any]:
        """LOCALIZE_COMPONENTS — mock camera re-localisation for inventory tags."""
        _ = force_rescan
        self.refresh_pose_from_camera(tag_ids=tag_ids)
        state = self._read_state()
        comps = state.get("components") or {}
        poses: Dict[str, Any] = {}
        ids = list(tag_ids or [])
        if not ids:
            ids = list(comps.keys()) if isinstance(comps, dict) else []
        for tid in ids:
            comp = comps.get(tid) if isinstance(comps, dict) else None
            pose = None
            if isinstance(comp, dict):
                tun = ((comp.get("statecontrol") or {}).get("tunables") or {})
                pose = tun.get("reported_pose") or tun.get("nominal_pose")
            poses[tid] = dict(pose) if isinstance(pose, dict) else None
        return {"tag_ids": ids, "poses": poses}

    def preview_refresh_pose_candidates(
        self,
        tag_ids: Optional[List[str]] = None,
    ) -> Dict[str, Dict[str, float]]:
        """Dry-run scan poses (deterministic; matches :meth:`refresh_pose_from_camera`)."""
        from mock_edge.shared.mock_scan_preview import build_mock_scan_proposed_poses
        from lab_model.coordinator.state.pose_refresh_selection import filter_proposed_poses

        state = self._read_state()
        comps = state.get("components") or {}
        if not isinstance(comps, dict):
            return {}
        proposed = build_mock_scan_proposed_poses(comps, tag_ids=tag_ids)
        if tag_ids:
            proposed = filter_proposed_poses(proposed, tag_ids)
        return proposed

    def refresh_state(self):
        """Deprecated name; use :meth:`refresh_pose_from_camera`."""
        self.refresh_pose_from_camera(None)

    # ``set_lab_state`` lives on the base template class (Phase 2A).
    # Mock has no robot, so :meth:`_apply_loaded_pose_to_hardware`
    # inherits the no-op default. ``_post_apply_snapshot`` also inherits
    # the no-op default (mock has no separate stored-intent file).

    # ---------------------------------------------------------------
    # Primitive hooks
    # ---------------------------------------------------------------
    #
    # The methods below are the complete list of ``_primitive_*``
    # hooks the base orchestrator dispatches for the mock backend.
    # Each one is a single-line delegation to the matching
    # ``primitive_<name>`` free function in
    # :mod:`lab_communicator.mock.primitives`, where the simulated
    # hardware step lives. Reading this section answers "what
    # primitives does the mock communicator implement?"; reading
    # ``primitives.py`` answers "what does each primitive simulate?".

    async def _primitive_prepare_teleop(self, target_id: str) -> Tuple[bool, str]:
        await asyncio.sleep(0.75)
        print(f"[MOCK LAB] TELEOP ready for {target_id}")
        return True, "ok"

    async def _primitive_move_component(
        self, target_id: str, commanded: LabPose
    ) -> Optional[LabPose]:
        from mock_edge.host.primitives import primitive_move_component
        return await primitive_move_component(self, target_id, commanded)

    async def _primitive_move_motor(
        self, target_id: str, motor_id: int, distance: float
    ) -> None:
        from mock_edge.host.primitives import primitive_move_motor
        await primitive_move_motor(self, target_id, motor_id, distance)

    async def _primitive_optimize_component(
        self,
        *,
        target_id: str,
        strategy_name: str,
        params: Dict[str, Any],
        progress_callback: Callable[..., None],
    ) -> Optional[Dict[str, Any]]:
        from mock_edge.host.primitives import primitive_optimize_component
        return await primitive_optimize_component(
            self,
            target_id=target_id,
            strategy_name=strategy_name,
            params=params,
            progress_callback=progress_callback,
        )

    async def _primitive_run_ensemble_optimization(
        self,
        *,
        spec: Any,
        x0: Dict[str, Any],
        session_id: str,
        progress_callback: Callable[..., None],
        should_abort: Optional[Callable[[], bool]] = None,
    ) -> Optional[Dict[str, Any]]:
        from mock_edge.host.ensemble import run_mock_ensemble_session
        from lab_model.execution.optimization.spec import OptimizeEnsembleParameters

        spec_obj = (
            spec
            if isinstance(spec, OptimizeEnsembleParameters)
            else OptimizeEnsembleParameters.model_validate(spec)
        )
        x0_map = {str(k): float(v) for k, v in (x0 or {}).items()}

        def _run() -> Any:
            def _ui_progress(*args: Any, **kwargs: Any) -> None:
                progress_callback(*args, **kwargs)
                time.sleep(0.025)

            # Do not hold the state lock for the whole session â€” progress_callback
            # updates in-memory state between evals so GET /api/lab-state can poll live.
            result = run_mock_ensemble_session(
                self.current_state,
                spec_obj,
                x0_map,
                session_id=session_id,
                progress_callback=_ui_progress,
                state_lock=self._state_lock,
                should_abort=should_abort
                or getattr(self, "_job_abort_check", None),
            )
            with self._state_lock:
                self._persist_state()
            return result

        result = await asyncio.to_thread(_run)
        return {
            "session_id": result.session_id,
            "best_loss": result.best_loss,
            "final_values": result.final_values,
            "evals": result.evals,
            "trace": result.trace[-200:],
        }

    async def _primitive_add_component_to_state(
        self,
        component_data: Dict[str, Any],
        existing_components: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        from mock_edge.host.primitives import primitive_add_component_to_state
        return await primitive_add_component_to_state(
            self, component_data, existing_components
        )

    async def _primitive_reactivate_off_table_component(
        self,
        tag_id: str,
        existing_entry: Dict[str, Any],
        component_data: Dict[str, Any],
        existing_components: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        from mock_edge.host.primitives import primitive_reactivate_off_table_component
        return await primitive_reactivate_off_table_component(
            self, tag_id, existing_entry, component_data, existing_components
        )

    async def track_component(self, component_data: Dict[str, Any]) -> Dict[str, Any]:
        """Enable control and materialize the part on the mock bench (table or chrome bar)."""
        from lab_model.coordinator.catalog.active_catalog_store import (
            ensure_tag_in_active_catalog,
            remove_tag_from_active_catalog,
        )
        from lab_model.coordinator.catalog.bundle import library_by_tag
        from lab_model.coordinator.catalog.schema import catalog_is_fixed_instrument
        from lab_model.language.domain.component import is_off_table, presence_of
        from lab_model.coordinator.state.fixture_seed import build_fixture_component_entry
        from lab_model.coordinator.state.runtime_manager import MutationKind

        tag_id = (component_data or {}).get("tag_id")
        if not tag_id:
            raise ValueError("tag_id is required")

        by_tag = library_by_tag()
        lib_row = by_tag.get(tag_id)
        if lib_row is None:
            raise ValueError(f"Unknown tag_id: {tag_id}")

        catalog_updated = ensure_tag_in_active_catalog(tag_id)
        if catalog_updated:
            self._load_catalog()

        comp_type = (component_data or {}).get("type") or lib_row.get("type", "OPTICAL_MIRROR")
        fixed = catalog_is_fixed_instrument(lib_row)

        with self._state_lock:
            existing = dict(self.current_state.get("components") or {})

        new_entry: Optional[Dict[str, Any]] = None
        action = "catalog_only"

        if tag_id not in existing:
            if fixed:
                new_entry = build_fixture_component_entry(lib_row)
                action = "created_fixture"
            else:
                payload = {
                    "tag_id": tag_id,
                    "type": comp_type,
                    "placement_mode": "breadboard",
                }
                new_entry = await self._primitive_add_component_to_state(payload, existing)
                action = "placed_on_table" if new_entry else "failed"
        elif fixed:
            action = "catalog_only"
        elif is_off_table(existing[tag_id]):
            payload = {
                "tag_id": tag_id,
                "type": comp_type,
                "placement_mode": "breadboard",
            }
            new_entry = await self._primitive_reactivate_off_table_component(
                tag_id,
                existing[tag_id],
                payload,
                existing,
            )
            action = "reactivated_on_table" if new_entry else "failed"
        else:
            action = "catalog_only"

        if action == "failed":
            if catalog_updated:
                remove_tag_from_active_catalog(tag_id)
                self._load_catalog()
            raise RuntimeError(f"No valid table placement for {tag_id}")

        if new_entry is not None:

            def _apply(state: Dict[str, Any]) -> None:
                state.setdefault("components", {})[tag_id] = new_entry

            self._lab_runtime.mutate(
                _apply,
                kind=MutationKind.ADMINISTRATIVE_LOAD,
                source=f"track_component:{tag_id}",
            )
            self._persist_state()

        if new_entry is not None:
            presence = presence_of(new_entry)
        elif tag_id in existing:
            presence = presence_of(existing[tag_id])
        else:
            presence = "off_table"

        return {
            "status": "ok",
            "tag_id": tag_id,
            "tracked": True,
            "presence": presence,
            "catalog_updated": catalog_updated,
            "action": action,
        }

    async def untrack_component(self, tag_id: str) -> Dict[str, Any]:
        """Remove from active catalog and drop the runtime row (returns part to library)."""
        from lab_model.coordinator.catalog.active_catalog_store import remove_tag_from_active_catalog
        from lab_model.language.domain.holding import SYSTEM_STATUS_IDLE, empty_holding, get_holding
        from lab_model.coordinator.state.runtime_manager import MutationKind

        tid = (tag_id or "").strip()
        if not tid:
            raise ValueError("tag_id is required")

        catalog_updated = remove_tag_from_active_catalog(tid)
        if catalog_updated:
            self._load_catalog()

        removed = False

        def _apply(state: Dict[str, Any]) -> None:
            nonlocal removed
            comps = state.get("components")
            if isinstance(comps, dict) and tid in comps:
                del comps[tid]
                removed = True
            holding = get_holding(state)
            if holding.get("tag_id") == tid:
                state["holding"] = empty_holding()
                if state.get("system_status") == "HOLDING":
                    state["system_status"] = SYSTEM_STATUS_IDLE

        self._lab_runtime.mutate(
            _apply,
            kind=MutationKind.ADMINISTRATIVE_LOAD,
            source=f"untrack_component:{tid}",
        )
        if removed:
            self._persist_state()

        return {
            "status": "ok",
            "tag_id": tid,
            "tracked": False,
            "catalog_updated": catalog_updated,
            "removed_from_runtime": removed,
        }

    async def _primitive_pick_component(
        self, target_id: str, commanded: LabPose, params: Dict[str, Any]
    ) -> float:
        from mock_edge.host.primitives import primitive_pick_component
        return await primitive_pick_component(self, target_id, commanded, params)

    async def _primitive_hover_component(
        self, target_id: str, commanded: LabPose, speed: int
    ) -> Optional[LabPose]:
        from mock_edge.host.primitives import primitive_hover_component
        return await primitive_hover_component(self, target_id, commanded, speed)

    async def _primitive_place_from_hover(
        self, target_id: str, commanded: LabPose, params: Dict[str, Any]
    ) -> None:
        from mock_edge.host.primitives import primitive_place_from_hover
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
        from mock_edge.host.primitives import primitive_scan_rotate_in_place
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

    # ``scan_rotate_in_place`` lives on the base template class
    # (Phase 2B); mock relies on the inherited orchestrator plus the
    # :meth:`_primitive_scan_rotate_in_place` hook above to drive the
    # stepwise sweep and the per-step + final state commits through
    # ``commit_scan_rotation``. ``confirm_holding_tag`` and the
    # holding-state housekeeping it runs are inherited the same way.

    def get_video_feed_status(self) -> Dict[str, Any]:
        return {"connected": True, "source": "/api/components/{tag_id}/telemetry/stream"}

    def _camera_captures_dir(self) -> str:
        """Directory mock writes synthetic capture PNGs into.

        Lives next to the mock state JSON so the directory layout
        matches the rest of the schemas folder. Used by
        :func:`lab_communicator.mock.primitives.primitive_record_measurables`
        to land ``<tag>_last.png`` for the UI to pick up.
        """
        return os.path.abspath(get_lab_view_paths().camera_captures_dir)

    def table_cam_connect(self, cam_id: int) -> Tuple[bool, str]:
        if cam_id not in (1, 2):
            return False, "cam_id must be 1 or 2"
        self._table_cam_connected[int(cam_id)] = True
        return True, "ok"

    def table_cam_disconnect(self, cam_id: int) -> Tuple[bool, str]:
        if cam_id not in (1, 2):
            return False, "cam_id must be 1 or 2"
        self._table_cam_streaming[int(cam_id)] = False
        self._table_cam_connected[int(cam_id)] = False
        return True, "ok"

    def table_cam_live_set(
        self, cam_id: int, enabled: bool, *, profile: str = "default"
    ) -> Tuple[bool, str]:
        if cam_id not in (1, 2):
            return False, "cam_id must be 1 or 2"
        if not self._table_cam_connected.get(int(cam_id)):
            return False, "connect camera first"
        self._table_cam_streaming[int(cam_id)] = bool(enabled)
        if enabled:
            self._table_cam_stream_profile[int(cam_id)] = (
                "teleop" if str(profile).strip().lower() == "teleop" else "default"
            )
        else:
            self._table_cam_stream_profile[int(cam_id)] = "default"
        return True, "ok"

    def table_cam_send_vexp(self, cam_id: int, exposure_s: float) -> Tuple[bool, str]:
        if cam_id not in (1, 2):
            return False, "cam_id must be 1 or 2"
        if not self._table_cam_connected.get(int(cam_id)):
            return False, "connect camera first"
        _ = float(exposure_s)
        return True, "ok (mock)"

    def table_cam_send_vgain(self, cam_id: int, gain: float) -> Tuple[bool, str]:
        if cam_id not in (1, 2):
            return False, "cam_id must be 1 or 2"
        if not self._table_cam_connected.get(int(cam_id)):
            return False, "connect camera first"
        _ = float(gain)
        return True, "ok (mock)"

    def get_table_cam_stream(self, cam_id: int = 1, fps: int = 18):
        import math
        import time

        import cv2  # noqa: PLC0415
        import numpy as np  # noqa: PLC0415

        fps = max(4, min(int(fps), 40))
        sleep_dur = 1.0 / fps

        while True:
            loop_t0 = time.perf_counter()
            ts = time.perf_counter()

            def _yield_placeholder(text: str, sub: str = "") -> bytes:
                frame = np.zeros((360, 480, 3), dtype=np.uint8)
                cv2.putText(
                    frame,
                    text[:40],
                    (20, 150),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.65,
                    (198, 198, 220),
                    2,
                )
                if sub:
                    cv2.putText(
                        frame,
                        sub[:48],
                        (20, 190),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.45,
                        (130, 130, 150),
                        1,
                    )
                ret, buf = cv2.imencode(".jpg", frame)
                return buf.tobytes() if ret else b""

            cid = int(cam_id)
            if not self._table_cam_connected.get(cid):
                frame_bytes = _yield_placeholder(
                    "Mock table cam disconnected", "CONNECT first"
                )
            elif not self._table_cam_streaming.get(cid):
                frame_bytes = _yield_placeholder(
                    "Mock LIVE paused",
                    "Enable Live to stream",
                )
            else:
                # GIF-like loop: optic â€œobjectâ€ with continuous tiny motion vs static CAPTURE PNG.
                h, w_frame = 360, 480
                frame = np.zeros((h, w_frame, 3), dtype=np.uint8)

                cid_f = float(cid)
                # Soft vignette teal lab background
                for yy in range(h):
                    v = float(yy) / float(h)
                    fill_b = int(22 + v * 18 + 6 * math.sin(ts * 0.4 + cid_f * 0.2))
                    fill_g = int(62 + v * 24 + 4 * math.sin(ts * 0.35))
                    fill_r = int(58 + v * 20 + 5 * math.cos(ts * 0.42))
                    frame[yy, :, 0] = min(140, fill_b)
                    frame[yy, :, 1] = min(180, fill_g)
                    frame[yy, :, 2] = min(170, fill_r)

                dx = int(8 * math.sin(ts * (0.85 + 0.04 * cid_f)))
                dy = int(6 * math.cos(ts * (0.7 + 0.05 * cid_f)))
                cx = 240 + dx
                cy = 172 + dy

                axis_long = int(96 + math.sin(ts * (2.2 + cid_f * 0.08)) * 8)
                axis_short = int(62 + math.cos(ts * (2.0 + cid_f * 0.07)) * 7)

                # Mount base under optic
                base_y = cy + axis_short // 2 + 14
                cv2.rectangle(
                    frame,
                    (cx - 118, base_y),
                    (cx + 118, base_y + 54),
                    (78, 78, 95),
                    -1,
                )
                cv2.rectangle(frame, (cx - 118, base_y), (cx + 118, base_y + 54), (40, 45, 55), 2)

                # Outer housing ring
                cv2.ellipse(
                    frame,
                    (cx, cy),
                    (axis_long + 10, axis_short + 10),
                    0,
                    0,
                    360,
                    (35, 45, 62),
                    8,
                )
                cv2.ellipse(frame, (cx, cy), (axis_long + 10, axis_short + 10), 0, 0, 360, (90, 100, 120), 3)

                # Glass element (muted teal fill)
                cv2.ellipse(
                    frame,
                    (cx, cy),
                    (axis_long, axis_short),
                    0,
                    0,
                    360,
                    (95, 55, 45),
                    -1,
                )
                cv2.ellipse(frame, (cx, cy), (axis_long, axis_short), 0, 0, 360, (165, 200, 220), 2)

                # Inner aperture wedge (tiny rotation-feel via arc sweep that moves)
                a0 = (ts * 55.0 + cid_f * 17.0) % 360.0
                cv2.ellipse(frame, (cx, cy), (axis_long - 28, axis_short - 22), 0, a0, a0 + 110, (30, 120, 150), -1)

                # Crawling highlight (specular glide)
                glide = ts * (1.25 + 0.12 * cid_f)
                hl_x = int(cx + (axis_long * 0.52) * math.cos(glide))
                hl_y = int(cy + (axis_short * 0.45) * math.sin(glide))
                cv2.circle(
                    frame,
                    (hl_x, hl_y),
                    int(14 + 4 * math.sin(ts * (4.8 + cid_f * 0.3))),
                    (240, 255, 255),
                    -1,
                )

                # Below: alignment spot that breathes like a realtime beam centroid
                spot_phase = ts * (3.15 + cid_f * 0.11)
                spot_x = cx + int(42 * math.sin(spot_phase * 0.5))
                spot_y = 268 + int(14 * math.sin(spot_phase + 1.0))
                spot_r = int(26 + 8 * math.sin(spot_phase * 1.3))
                cv2.circle(frame, (spot_x, spot_y), spot_r + 18, (12, 80, 100), -1)
                cv2.circle(frame, (spot_x, spot_y), spot_r + 12, (0, 210, 255), 8)
                cv2.circle(frame, (spot_x, spot_y), int(spot_r * 0.45), (220, 255, 255), -1)

                cv2.rectangle(frame, (10, 8), (w_frame - 10, h - 10), (20, 100, 110), 2)
                cv2.putText(
                    frame,
                    f"MOCK LIVE â€” CAM{cid} (moving preview)",
                    (22, 32),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.55,
                    (210, 250, 255),
                    1,
                    lineType=cv2.LINE_AA,
                )
                cv2.putText(
                    frame,
                    "~gif-like motion / compare to violet CAPTURE still",
                    (22, 50),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.38,
                    (160, 230, 240),
                    1,
                    lineType=cv2.LINE_AA,
                )
                ret, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 86])
                frame_bytes = buffer.tobytes() if ret else b""

            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n"
                + frame_bytes
                + b"\r\n"
            )
            elapsed = time.perf_counter() - loop_t0
            time.sleep(max(sleep_dur - elapsed, 0.001))

    def fetch_table_cam_preview_jpeg(self, cam_id: int = 1) -> Optional[bytes]:
        """One JPEG frame for polled live preview (same visuals as the mock stream)."""
        for part in self.get_table_cam_stream(int(cam_id), fps=30):
            start = part.find(b"\xff\xd8")
            if start < 0:
                continue
            end = part.find(b"\xff\xd9", start)
            if end >= 0:
                return bytes(part[start : end + 2])
        return None

    async def _primitive_record_measurables(
        self, tag_id: str, catalog_meta: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        from mock_edge.host.primitives import primitive_record_measurables

        return await primitive_record_measurables(self, tag_id, catalog_meta)

    def get_table_cam_status(self, only_cam_id=None) -> Dict[str, Any]:
        try:
            from lab_model.coordinator.backends.lab_view_config import (  # noqa: PLC0415
                load_table_cam_preview_config,
            )

            preview_config = load_table_cam_preview_config().as_dict()
        except Exception:
            preview_config = {
                "scale": 0.75,
                "jpeg_quality": 72,
                "target_fps": 144,
                "max_inflight_requests": 3,
            }
        return {
            "recorder_variant": "mock",
            "recorder_alive": True,
            "recorder_mock": True,
            "ports": {"cam1": 9999, "cam2": 10000},
            "preview_config": preview_config,
            "cameras": {
                "1": {
                    "connected": bool(self._table_cam_connected.get(1)),
                    "streaming": bool(self._table_cam_streaming.get(1)),
                    "hardware": "mock",
                    "port": 9999,
                    "port_open": True,
                    "last_error": None,
                },
                "2": {
                    "connected": bool(self._table_cam_connected.get(2)),
                    "streaming": bool(self._table_cam_streaming.get(2)),
                    "hardware": "mock",
                    "port": 10000,
                    "port_open": True,
                    "last_error": None,
                },
            },
        }

    def capture_table_cam(self, cam_id: int, exposure: float = 0.2) -> bytes:
        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError:
            print("[MOCK LAB] capture_table_cam: Pillow not installed")
            return None
        if cam_id not in (1, 2):
            return None
        if not self._table_cam_connected.get(cam_id):
            print(
                "[MOCK LAB] capture_table_cam refused: connect the table cam "
                f"(CAM {cam_id}) first."
            )
            return None

        w, h = 640, 480
        # Static "single CAPTURE" mock â€” violet / amber palette distinct from teal LIVE MJPEG.
        bg = (32, 20, 48)
        img = Image.new("RGB", (w, h), bg)
        draw = ImageDraw.Draw(img)
        magenta = (90, 32, 86)
        for i in range(-h, w, 42):
            draw.line([(i, 0), (i + h, h)], fill=magenta, width=2)
        for i in range(0, w + h, 46):
            draw.line([(i, 0), (i - h, h)], fill=(48, 30, 64), width=1)

        draw.rectangle([(0, 0), (w, 52)], fill=(217, 160, 60))
        try:
            font = ImageFont.load_default()
        except Exception:
            font = None
        banner_a = "MOCK Â· SINGLE CAPTURE (PNG / CAP)"
        banner_b = f"CAM{cam_id} Â· exp {exposure:g}s Â· still frame"
        if font:
            draw.text((14, 8), banner_a, fill=(40, 30, 10), font=font)
            draw.text((14, 28), banner_b, fill=(60, 44, 16), font=font)
        else:
            draw.text((14, 10), banner_a, fill=(40, 30, 10))
            draw.text((14, 28), banner_b, fill=(60, 44, 16))

        margin = 18
        draw.rectangle(
            [(margin, 68), (w - margin, h - margin)],
            outline=(147, 197, 253),
            width=4,
        )
        inset = margin + 32
        draw.line([(w // 2, inset), (w // 2, h - inset)], fill=(148, 163, 184), width=2)
        draw.line([(inset, h // 2), (w - inset, h // 2)], fill=(148, 163, 184), width=2)

        cx = w // 2 + (cam_id - 1) * 72
        cy = h // 2 + 6
        r = 40
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(251, 113, 133), width=5)
        draw.line([(cx - 52, cy), (cx + 52, cy)], fill=(250, 204, 21), width=4)
        stamp = "[STATIC SAMPLE â€” not live]"
        if font:
            draw.text((inset + 6, inset + 12), stamp, fill=(226, 232, 240), font=font)
        else:
            draw.text((inset + 6, inset + 12), stamp, fill=(226, 232, 240))

        buf = BytesIO()
        img.save(buf, format="PNG", compress_level=6)
        return buf.getvalue()

    def capture_overhead_cam(self, exposure: float = 0.2) -> bytes:
        """Synthetic still for the fixed table-top / overhead camera (tag_99)."""
        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError:
            print("[MOCK LAB] capture_overhead_cam: Pillow not installed")
            return None

        w, h = 480, 360
        img = Image.new("RGB", (w, h), (18, 24, 38))
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.load_default()
        except Exception:
            font = None
        banner = "MOCK Â· OVERHEAD CAPTURE"
        sub = f"table-top Â· exp {exposure:g}s"
        if font:
            draw.text((16, 16), banner, fill=(125, 211, 252), font=font)
            draw.text((16, 34), sub, fill=(148, 163, 184), font=font)
        else:
            draw.text((16, 16), banner, fill=(125, 211, 252))
            draw.text((16, 34), sub, fill=(148, 163, 184))
        draw.rectangle([(24, 64), (w - 24, h - 24)], outline=(56, 189, 248), width=3)
        draw.line([(w // 2, 64), (w // 2, h - 24)], fill=(71, 85, 105), width=2)
        draw.line([(24, h // 2), (w - 24, h // 2)], fill=(71, 85, 105), width=2)
        buf = BytesIO()
        img.save(buf, format="PNG", compress_level=6)
        return buf.getvalue()

    def get_video_stream(self, fps: int = 10):
        """Mock overhead / table-top MJPEG (fixed bench camera)."""
        import time

        import cv2  # noqa: PLC0415
        import numpy as np  # noqa: PLC0415

        fps = max(4, min(int(fps), 30))
        sleep_dur = 1.0 / fps
        while True:
            frame = np.zeros((360, 480, 3), dtype=np.uint8)
            cv2.putText(
                frame,
                "Mock table-top / overhead",
                (16, 140),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (120, 200, 255),
                2,
            )
            cv2.putText(
                frame,
                f"tag_99 cam_table_top @ {fps}fps",
                (16, 180),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.45,
                (150, 150, 170),
                1,
            )
            ret, buf = cv2.imencode(".jpg", frame)
            if ret:
                yield (
                    b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
                    + buf.tobytes()
                    + b"\r\n"
                )
            time.sleep(sleep_dur)
