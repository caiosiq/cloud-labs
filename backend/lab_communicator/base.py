"""Concrete template class for the lab communicator.

Phase 2A of the communicator refactor (see ``communicator_refactor.md``)
promoted this file from a thin abstract base to the concrete template
class that orchestrates every primitive. Subclasses (``RealLabCommunicator``,
``MockLabCommunicator``) provide the small ``_primitive_*`` hooks that distinguish
"talk to lab_automation" from "sleep + add noise" (the per-backend
hardware steps live one-for-one in ``real/primitives.py`` and
``mock/primitives.py``); everything else --
state ownership, refusal logic, status transitions, snapshot load
merging, motor-rotation injection, holding-field bookkeeping -- lives
here.

Architectural rules (enforced by ``lab_model.platform`` integrity checks
and hook conventions documented in ``lab_communicator/README.md``):

- ``lab_communicator/shared/`` config modules can be imported here;
  ``real/`` and ``mock/`` must not import each other.
- Primitive hook implementations in ``real/`` and ``mock/`` (the
  ``_primitive_*`` methods on the class, plus the ``primitive_*``
  free functions in ``primitives.py`` they delegate to) MUST NOT
  read or write ``self.current_state``. The orchestrator passes them
  what they need via arguments and (for long-running primitives) a
  ``progress_callback`` -- see ``communicator_refactor.md`` §6.2.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from lab_model.domain.component import (
    PRESENCE_BREADBOARD,
    default_measurables,
    default_tunables,
    get_measurables,
    get_telemetry,
    get_tunables,
    is_on_table,
    is_stored,
    new_component_entry,
    normalize_components_map,
)
from lab_model.domain.holding import (
    DEFAULT_HOVER_Z_MM,
    SYSTEM_STATUS_BUSY,
    SYSTEM_STATUS_HOLDING,
    SYSTEM_STATUS_IDLE,
    clear_holding,
    empty_holding,
    get_holding,
    set_holding,
)
from lab_model.orchestration.teleop_live_pose import TeleopLivePoseStore
from lab_model import motor_rotation_store as motor_rot

from lab_model.domain.storage_region import find_storage_slot_and_center

from lab_model.catalog.lookup import (
    catalog_wh as _catalog_wh_pure,
    motor_catalog_ok as _motor_catalog_ok_pure,
)
from lab_model.orchestration import (
    TeleopController,
    run_affirm_placed_at_current,
    run_confirm_holding_tag,
    run_end_live_feed,
    run_hover_component,
    run_move_component,
    run_move_motor,
    run_optimize_component,
    run_pick_component,
    run_place_from_hover,
    run_place_from_storage,
    run_recenter_stored_in_inventory,
    run_record_measurables,
    run_repack_storage_slot,
    run_scan_rotate_in_place,
    run_start_live_feed,
    run_store_component,
)
from lab_model.state.commits import (
    commit_observed_camera_image,
    null_measurables_for_targets,
)
from lab_model.state.motor_state import (
    inject_motor_rotations_into_state,
    persist_motor_rotations_from_component,
)
from lab_model.state.snapshot import (
    LabPose,
    merge_snapshot_components,
    normalize_loaded_state,
)
from lab_model.state.runtime_manager import MutationKind, RuntimeManager, default_runtime_state

class LabCommunicator:
    """Concrete template: owns state + orchestrates every primitive.

    Subclasses provide:

    - **State seed** (in ``__init__``) -- ``self.current_state``,
      ``self.catalog_map``, plus any backend-specific fields
      (``self.experiment`` for real, ``self.state_file`` for mock).
    - **Per-component hardware apply** -- :meth:`_apply_loaded_pose_to_hardware`,
      called by :meth:`set_lab_state` for every component in a freshly
      loaded snapshot. Real transforms XY/Z/yaw and writes
      ``comp.current_location``; mock is a no-op.
    - **Persistence hook** -- :meth:`_persist_state`, called after
      every state mutation. Real is a no-op (state is already in
      memory); mock writes to disk.
    - **Post-snapshot hook** -- :meth:`_post_apply_snapshot`, called
      once at the end of :meth:`set_lab_state`. Real rebuilds the
      stored-intent JSON file; mock is a no-op.
    - **Catalog meta lookup** -- :meth:`_catalog_meta_for_tag`, used
      by ``inject_motor_rotations_into_state``. Real and mock both
      provide :attr:`catalog_map` so the default implementation here
      works for both.
    - **Primitive hooks** -- ``_primitive_<name>(...)`` for each
      primitive the orchestrator dispatches. Each one is a
      one-line delegation to the matching free function in
      ``real/primitives.py`` / ``mock/primitives.py`` -- where the
      actual ``lab_automation`` API call (or the mock simulation)
      lives.

    The class attribute :attr:`log_prefix` is interpolated into log
    lines so a reader can tell which backend produced a message
    without inspecting the call site.
    """

    #: Backend tag used in log lines. Subclasses override.
    log_prefix: str = "[LAB]"

    #: Safety bound for ``z_lab`` (mm) accepted by ``hover_component``.
    #: Subclasses with a calibrated robot frame override (real uses
    #: ``MAX_SAFE_HOVER_Z_LAB_MM`` from ``coordinate_frames.py``); mock
    #: leaves a generous default so tests don't need a calibration.
    max_safe_hover_z_lab_mm: float = 200.0

    # --- State (subclasses populate via RuntimeManager in __init__) ---
    _lab_runtime: RuntimeManager
    catalog_map: Dict[str, Dict[str, Any]]

    @property
    def current_state(self) -> Dict[str, Any]:
        return self._lab_runtime.state

    @property
    def _lab_runtime_manager(self) -> RuntimeManager:
        """Public alias for tests and ControlManager integration."""
        return self._lab_runtime

    def __init__(self) -> None:
        self._lab_runtime = RuntimeManager(default_runtime_state())
        self._state_lock = self._lab_runtime.lock
        self.catalog_map = {}
        self._teleop_live_pose = TeleopLivePoseStore(
            on_motion_idle=self._on_teleop_motion_idle,
        )
        self._teleop = TeleopController(self)

    def _on_teleop_motion_idle(self, tag_id: str) -> None:
        with self._state_lock:
            from lab_model.state.commits import (
                commit_teleop_command_idle,
                commit_teleop_session_pose,
            )

            # Use _teleop_live_get_pose — not get_teleop_live_pose (RealLab override
            # would re-enter motion-idle detection and recurse).
            final_pose = self._teleop_live_get_pose(tag_id)
            if final_pose:
                commit_teleop_session_pose(self.current_state, tag_id, final_pose)
            commit_teleop_command_idle(self.current_state, tag_id)
            self.current_state["last_updated"] = datetime.now().isoformat()
        self._persist_state()

    def get_teleop_live_pose(self, tag_id: str) -> Optional[Dict[str, Any]]:
        return self._teleop_live_get_pose(tag_id)

    def _teleop_live_start(self, tag_id: str, initial_pose: Dict[str, Any]) -> None:
        self._teleop_live_pose.start_session(tag_id, initial_pose)

    def _teleop_live_get_pose(self, tag_id: str) -> Optional[Dict[str, Any]]:
        return self._teleop_live_pose.get_pose(tag_id)

    def _teleop_live_set_goto(
        self,
        tag_id: str,
        target: Dict[str, Any],
        speed: Dict[str, Any],
    ) -> None:
        self._teleop_live_pose.set_goto(tag_id, target, speed)

    def _teleop_live_stop_sync(self, tag_id: str) -> None:
        self._teleop_live_pose.stop_session(tag_id)

    async def _teleop_live_stop(self, tag_id: str) -> None:
        self._teleop_live_stop_sync(tag_id)

    # ---------------------------------------------------------------
    # Catalog accessors
    # ---------------------------------------------------------------

    def get_catalog(self) -> List[Dict[str, Any]]:
        """Return merged catalog rows (``component_library`` ∩ ``active_catalog``).

        Reloads from disk on every call so edits to ``lab_view`` JSON are visible
        without a restart. Runtime component tags not listed in ``active_catalog``
        are unioned in so OFF_TABLE inventory remains addressable in the UI.

        In-memory :attr:`catalog_map` is refreshed by mock
        via :meth:`~lab_communicator.mock.communicator.MockLabCommunicator._load_catalog`
        when primitives need a fresh mirror.
        """
        from lab_model.catalog.bundle import merged_catalog_rows

        try:
            runtime_ids = list((self.current_state.get("components") or {}).keys())
            return merged_catalog_rows(runtime_tag_ids=runtime_ids)
        except Exception:
            return []

    def _catalog_meta_for_tag(self, tag_id: str) -> Optional[Dict[str, Any]]:
        """Catalog metadata for ``tag_id`` (or ``None`` if unknown).

        Default uses :attr:`catalog_map`. Subclasses with a different
        in-memory shape may override (mock historically scanned a list;
        Phase 2A unified both backends on a dict).
        """
        return self.catalog_map.get(tag_id)

    def _ensure_fixture_components(self) -> None:
        """Seed fixed-instrument catalog rows into ``current_state['components']``."""
        from lab_model.state.fixture_seed import ensure_fixture_components_in_state

        rows = self.get_catalog()
        if not rows:
            return
        with self._state_lock:
            changed = ensure_fixture_components_in_state(self.current_state, rows)
            if changed:
                self.current_state["last_updated"] = datetime.now().isoformat()
        if changed:
            self._persist_state()

    def _catalog_tag_ids(self) -> Set[str]:
        """Set of tag ids known to the catalog (used by snapshot merge)."""
        return set(self.catalog_map.keys())

    def _motor_catalog_ok(self, target_id: str, motor_id: int) -> bool:
        """Validate that ``target_id`` declares ``motor_id`` in the catalog."""
        return _motor_catalog_ok_pure(self.catalog_map.get, target_id, motor_id)

    def _catalog_wh(self, tag_id: str) -> "tuple[float, float]":
        """UI footprint ``(width, height)`` for ``tag_id`` (catalog lookup).

        Used by storage primitives that ask
        :func:`find_storage_slot_and_center` for a packing-aware slot
        center. Both backends share this lookup -- the catalog format
        is unified across real and mock.
        """
        return _catalog_wh_pure(self.catalog_map.get, tag_id)

    # ---------------------------------------------------------------
    # State accessors / mutators (the only place outside subclasses
    # that touches ``self.current_state``)
    # ---------------------------------------------------------------

    def _ensure_overlay_fields_locked(self) -> None:
        """Seed versioned alignment overlays onto ``current_state`` in place.

        Caller must hold ``self._state_lock``. ``alignment_guides`` defaults to
        an empty list; ``laser_lines`` is seeded from the lab-view bundle file
        (``laser_lines.json``) on first access for an existing lab whose state
        predates line-versioning. Absence (not emptiness) triggers the seed, so
        a user who deletes every laser line keeps an empty set.
        """
        st = self.current_state
        if not isinstance(st.get("alignment_guides"), list):
            st["alignment_guides"] = []
        ll = st.get("laser_lines")
        if not isinstance(ll, dict) or not isinstance(ll.get("lines"), list):
            seed: Dict[str, Any] = {}
            try:
                from lab_communicator.shared.lab_view_config import read_laser_lines_doc

                seed = read_laser_lines_doc()
            except Exception:
                seed = {}
            st["laser_lines"] = {
                "snap_line_id": seed.get("snap_line_id"),
                "lines": json.loads(json.dumps(seed.get("lines") or [])),
            }

    def get_lab_state(self) -> Dict[str, Any]:
        """Deep-copy snapshot of ``self.current_state`` for the UI.

        Always:

        - Acquires the state lock for the copy.
        - Injects software motor angles into
          ``statecontrol.tunables.nominal_motor_positions`` (recalculated
          from the motor tracker — not a measurable).
          Seeds missing ``statecontrol.tunables.nominal_motor_positions``
          keys only — never overwrites committed setpoints.
        - Normalizes ``state["holding"]`` so the UI never sees
          ``undefined`` for held tag / requires_operator_confirm.
        """
        with self._state_lock:
            self._ensure_overlay_fields_locked()
            state = json.loads(json.dumps(self.current_state))
        normalize_components_map(state.get("components") or {})
        inject_motor_rotations_into_state(state, self._catalog_meta_for_tag)
        get_holding(state)  # normalizes in-place
        return state

    def return_tunables_for_tag(self, tag_id: str) -> Dict[str, Any]:
        """Saved tunables slice for one tag (no I/O)."""
        st = self.get_lab_state()
        comp = (st.get("components") or {}).get(tag_id)
        return get_tunables(comp) if isinstance(comp, dict) else {}

    def return_measurables_for_tag(self, tag_id: str) -> Dict[str, Any]:
        """Saved measurables slice for one tag (no I/O)."""
        st = self.get_lab_state()
        comp = (st.get("components") or {}).get(tag_id)
        return get_measurables(comp) if isinstance(comp, dict) else {}

    def return_telemetry_for_tag(self, tag_id: str) -> Dict[str, Any]:
        """Saved telemetry slice (teleop + live_feed session state)."""
        st = self.get_lab_state()
        comp = (st.get("components") or {}).get(tag_id)
        return get_telemetry(comp) if isinstance(comp, dict) else {}

    # Legacy aliases preserved for backward compatibility.
    def get_tunables_for_tag(self, tag_id: str) -> Dict[str, Any]:
        return self.return_tunables_for_tag(tag_id)

    def get_measurables_for_tag(self, tag_id: str) -> Dict[str, Any]:
        return self.return_measurables_for_tag(tag_id)

    async def record_measurables_for_tag(self, tag_id: str) -> Dict[str, Any]:
        """Trigger a fresh measurement — see :func:`lab_model.orchestration.run_record_measurables`."""
        return await run_record_measurables(self, tag_id)

    async def eval_kernel_for_tag(
        self,
        tag_id: str,
        kernel_id: str,
        *,
        field: str = "camera_image",
        lease_id: Optional[str] = None,
        backend_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """EVAL_KERNEL primitive: capture BGR and run a TorchScript kernel.

        Kernels are **inputs** (``kernel_id``), not peer verbs. Closed-loop
        OPTIMIZE runs kernels in-process via the ensemble backends — this
        method is the authoring/probe path (and DAG/command IR for one probe).

        ``field`` is reserved for measurable selection; capture uses camera BGR today.
        """
        from lab_model.optimization.kernels import session_store
        from lab_model.optimization.kernels.torchscript_runtime import (
            run_torchscript_output,
        )

        kid = str(kernel_id or "").strip()
        tid = str(tag_id or "").strip()
        if not kid or not tid:
            raise ValueError("kernel_id and tag_id required")

        if kid.startswith("session."):
            lid = str(lease_id or getattr(self, "_command_lease_id", None) or "").strip()
            bid = str(
                backend_id or getattr(self, "_command_backend_id", None) or ""
            ).strip()
            if lid and bid:
                session_store.activate_lease_roots(bid, lid)

        bgr = self.read_camera_bgr(tid)
        if bgr is None:
            import numpy as np

            # Mock-only gray fallback; real backends must not invent frames.
            mode = str(getattr(self, "lab_mode", "") or "").upper()
            is_mock = mode == "MOCK" or type(self).__name__.startswith("Mock")
            if not is_mock:
                raise RuntimeError(
                    f"camera capture failed for {tid!r} "
                    "(no frame from edge/real communicator)"
                )
            bgr = np.full((64, 64, 3), 128, dtype=np.uint8)

        kind, value = run_torchscript_output(kid, bgr)
        if kind == "features":
            return {
                "kind": "features",
                "features": list(value),
                "kernel_id": kid,
                "field": field,
            }
        return {
            "kind": "scalar",
            "scalar": float(value),
            "kernel_id": kid,
            "field": field,
        }

    async def _primitive_record_measurables(
        self, tag_id: str, catalog_meta: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Hardware step for :meth:`record_measurables_for_tag`.

        Default returns ``None`` (no fresh measurement -- saved
        measurables are returned verbatim). Real overrides for
        ``OPTICAL_CAMERA`` tags to capture a PNG and return its path
        + metadata; mock can do the same with a synthetic frame.
        """
        return None

    def _set_status(self, status: str, *, persist: bool = True) -> None:
        """Set ``system_status`` under the lock; optionally persist after.

        Single-purpose helper so primitive orchestrators don't repeat the
        ``with self._state_lock: ... last_updated = now`` boilerplate.
        ``persist=False`` is for batched mutations -- the caller will
        trigger a single :meth:`_persist_state` after the cluster
        completes.
        """
        with self._state_lock:
            self.current_state["system_status"] = status
            self.current_state["last_updated"] = datetime.now().isoformat()
        if persist:
            self._persist_state()
        if status == SYSTEM_STATUS_IDLE:
            try:
                self.save_session_checkpoint_if_enabled()
            except Exception:
                pass

    def _null_measurables_for_targets(
        self, target_ids: List[str], *, persist: bool = True
    ) -> None:
        """Phase 3 / Golden Rule: null measurables for the targeted tags.

        Thin wrapper around
        :func:`lab_model.state.commits.null_measurables_for_targets`
        that acquires the state lock, bumps ``last_updated``, and
        optionally persists. Call this **immediately before**
        ``_set_status(BUSY)`` / ``_set_status(OPTIMIZING)`` for every
        motion / optimization primitive — see
        ``universal_component_architecture.md`` §3.2 ("Golden Rule of
        Measurables") for the contract.

        ``persist=False`` is for primitives that follow the null with
        additional state writes (e.g. ``optimize_component`` sets the
        run dir + step counter in the same cluster); they should make
        a single ``_persist_state`` call at the cluster boundary.
        """
        if not target_ids:
            return
        with self._state_lock:
            null_measurables_for_targets(self.current_state, target_ids)
            self.current_state["last_updated"] = datetime.now().isoformat()
        if persist:
            self._persist_state()

    def _set_holding(
        self,
        *,
        tag_id: Optional[str],
        x: float,
        y: float,
        rotation: float,
        z: float,
        requires_operator_confirm_flag: bool = False,
        persist: bool = True,
    ) -> None:
        """Write the top-level ``holding`` field under the lock."""
        with self._state_lock:
            set_holding(
                self.current_state,
                tag_id=tag_id,
                x=x,
                y=y,
                rotation=rotation,
                z=z,
                requires_operator_confirm_flag=requires_operator_confirm_flag,
            )
            self.current_state["last_updated"] = datetime.now().isoformat()
        if persist:
            self._persist_state()

    def _clear_holding(self, *, persist: bool = True) -> None:
        """Clear the top-level ``holding`` field (e.g. after place_from_hover)."""
        with self._state_lock:
            clear_holding(self.current_state)
            self.current_state["last_updated"] = datetime.now().isoformat()
        if persist:
            self._persist_state()

    # ---------------------------------------------------------------
    # Phase 8: per-component TELEOP (delegates to lab_model.orchestration)
    # ---------------------------------------------------------------

    async def start_teleop(self, target_id: str) -> None:
        await self._teleop.start(target_id)

    async def end_teleop(self, target_id: str) -> None:
        await self._teleop.end(target_id)

    async def teleop_jog(self, target_id: str, jog: Dict[str, Any]) -> None:
        await self._teleop.jog(target_id, jog)

    async def teleop_goto(self, target_id: str, body: Dict[str, Any]) -> None:
        await self._teleop.goto(target_id, body)

    async def _primitive_prepare_teleop(self, target_id: str) -> Tuple[bool, str]:
        """Lab-specific setup before TELEOP controls become ready."""
        return True, "ok"

    async def start_live_feed(self, target_id: str, *, channel: str = "stream") -> None:
        await run_start_live_feed(self, target_id, channel=channel)

    async def end_live_feed(self, target_id: str, *, channel: str = "all") -> None:
        await run_end_live_feed(self, target_id, channel=channel)

    async def _primitive_start_live_feed(
        self,
        target_id: str,
        *,
        channel: str,
        backend: str,
        cam_id: Optional[int],
        profile: str = "default",
    ) -> Tuple[bool, str]:
        if backend == "overhead":
            return True, "ok"
        if backend == "table_cam" and cam_id is not None:
            ok, msg = self.table_cam_connect(int(cam_id))
            if not ok:
                return False, msg
            return self.table_cam_live_set(int(cam_id), True, profile=profile)
        return False, f"unsupported live feed backend {backend!r}"

    async def _primitive_end_live_feed(
        self,
        target_id: str,
        *,
        channel: str,
        catalog_meta: Dict[str, Any],
    ) -> Tuple[bool, str]:
        from lab_model.catalog.schema import resolve_cam_id_for_tag, resolve_telemetry_stream_backend

        backend = resolve_telemetry_stream_backend(catalog_meta or {})
        cam_id = resolve_cam_id_for_tag(catalog_meta or {})
        if backend == "table_cam" and cam_id is not None:
            # Stop streaming only — keep recorder TCP session warm for the next
            # START_LIVE_FEED and so in-flight JPEGPoll requests get fast
            # "LIVE OFF" placeholders instead of blocking CAP captures.
            self.table_cam_live_set(int(cam_id), False)
        return True, "ok"

    def shutdown_lab_processes(self) -> None:
        """Best-effort teardown of lab child processes (real recorder subprocesses)."""
        fn = getattr(self, "_shutdown_recorders", None)
        if callable(fn):
            fn()

    def stop_teleop_sweeper(self, *, join_timeout_s: float = 1.0) -> None:
        self._teleop.stop_sweeper(join_timeout_s=join_timeout_s)

    # ---------------------------------------------------------------
    # Persistence + post-snapshot hooks (subclasses override)
    # ---------------------------------------------------------------

    def _persist_state(self) -> None:
        """Persist ``self.current_state`` to durable storage.

        Default: no-op. Real keeps state in memory; mock overrides to
        write the JSON file. Called automatically by :meth:`_set_status`,
        :meth:`_set_holding`, :meth:`_clear_holding`, and the snapshot
        load orchestrator. Primitive orchestrators that batch multiple
        state mutations should pass ``persist=False`` to those helpers
        and call :meth:`_persist_state` once at the end of the batch.
        """
        return

    def session_checkpoint_enabled(self) -> bool:
        """When True, shutdown persists :meth:`get_lab_state` and UI reconciliation is offered."""

        _ENV_TRUE = frozenset({"1", "true", "yes", "on"})
        _ENV_FALSE = frozenset({"0", "false", "no", "off"})
        raw = (os.getenv("SESSION_CHECKPOINT") or "").strip().lower()
        if raw in _ENV_TRUE:
            return True
        if raw in _ENV_FALSE:
            return False
        try:
            from lab_communicator.shared.lab_view_config import get_lab_manifest

            return bool(get_lab_manifest().session_checkpoint)
        except Exception:
            return False

    def session_reconciliation_thresholds(self):
        from lab_communicator.shared.session_checkpoint import (
            reconciliation_thresholds_from_manifest,
        )

        return reconciliation_thresholds_from_manifest()

    def save_session_checkpoint_if_enabled(self) -> None:
        """Write ``session_last_lab_state.json`` beside lab_view JSON (feature-gated)."""

        if not self.session_checkpoint_enabled():
            return

        try:
            from lab_communicator.shared.lab_view_config import get_lab_manifest

            lab_mode = get_lab_manifest().lab_mode
        except Exception:
            lab_mode = (os.getenv("LAB_MODE") or "MOCK").upper()
        snapshot = self.get_lab_state()
        from lab_communicator.shared.session_checkpoint import persist_checkpoint

        persist_checkpoint(lab_mode, snapshot)

    def apply_session_reconciliation_tags(self, tag_ids: List[str]) -> List[str]:
        """Copy ``tunables`` + ``measurables`` for ``tag_ids`` from the checkpoint file."""

        from lab_communicator.shared.lab_view_config import get_lab_view_paths_optional
        from lab_communicator.shared.session_checkpoint import (
            checkpoint_lab_state,
            merge_offers_tag_ids,
            read_checkpoint_document,
        )
        from lab_model.domain.component import (
            get_measurables,
            get_tunables,
            measurables_bucket,
            tunables_bucket,
        )

        paths = get_lab_view_paths_optional()
        if paths is None:
            return []
        ck_path = getattr(paths, "session_checkpoint_json", "") or ""

        thresholds = self.session_reconciliation_thresholds()
        with self._state_lock:
            current = json.loads(json.dumps(self.current_state))
        chk_doc = read_checkpoint_document(ck_path)
        chk_state = checkpoint_lab_state(chk_doc)
        if not isinstance(chk_state, dict):
            return []

        allow = merge_offers_tag_ids(
            current_state=current,
            checkpoint_state=chk_state,
            thresholds=thresholds,
        )
        allow_set = set(allow)
        merged_ids: List[str] = []

        meta_ch = chk_state.get("components") or {}
        if not isinstance(meta_ch, dict):
            meta_ch = {}

        with self._state_lock:
            comps = self.current_state.setdefault("components", {})
            if not isinstance(comps, dict):
                return []
            for tid in tag_ids:
                if tid not in allow_set:
                    continue
                src_ent = meta_ch.get(tid)
                if not isinstance(src_ent, dict) or tid not in comps:
                    continue
                dst = comps[tid]
                if not isinstance(dst, dict):
                    continue
                st_t = json.loads(json.dumps(get_tunables(src_ent)))
                st_m = json.loads(json.dumps(get_measurables(src_ent)))
                dst_tun = tunables_bucket(dst)
                dst_meas = measurables_bucket(dst)
                dst_tun.clear()
                dst_tun.update(st_t)
                dst_meas.clear()
                dst_meas.update(st_m)
                meta = self._catalog_meta_for_tag(tid) or {}
                motor_ids_any = meta.get("motor_ids") or []
                motor_ids_int: List[int] = []
                for m in motor_ids_any:
                    try:
                        motor_ids_int.append(int(m))
                    except (TypeError, ValueError):
                        continue
                if motor_ids_int:
                    persist_motor_rotations_from_component(tid, dst, motor_ids_int)
                merged_ids.append(tid)
            self.current_state["last_updated"] = datetime.now().isoformat()

        self._persist_state()
        self._post_session_reconciliation_tags(merged_ids)
        return merged_ids

    def _post_session_reconciliation_tags(self, merged_ids: List[str]) -> None:
        """Hook after checkpoint merge (real syncs registry poses)."""
        return

    def _post_apply_snapshot(self, components: Dict[str, Any]) -> None:
        """Called once at the end of :meth:`set_lab_state`.

        Default: no-op. Real overrides to call
        :meth:`_rebuild_stored_intent_from_lab_state` so the persisted
        stored-intent file is in sync with the loaded snapshot. Mock
        has no separate stored-intent file.
        """
        return

    # ---------------------------------------------------------------
    # Snapshot load (the only writer of ``current_location`` per the
    # tightened Stage C lint -- see ``fixing.md`` §9 / Phase 2A)
    # ---------------------------------------------------------------

    def _apply_loaded_pose_to_hardware(
        self, tag_id: str, lab_pose: LabPose, *, is_placed: bool
    ) -> None:
        """Push a single component's loaded pose into the hardware layer.

        Called by :meth:`set_lab_state` once per loaded component.
        ``lab_pose`` is in the lab frame (UI / cloud-labs convention);
        backends that talk to a robot transform XY/Z/yaw before
        writing. Backends without hardware (mock) leave this as the
        no-op default.

        ``is_placed`` is the canonical "is this part on the breadboard
        now?" flag derived from the component entry's presence; real
        propagates it to ``OpticalComponent.is_placed`` (the legitimate
        Stage C exemption -- see ``fixing.md`` §9 item 6 / Phase 2A
        lint tightening).
        """
        return

    def set_lab_state(self, state: Dict[str, Any]) -> None:
        """Load a snapshot and apply it to in-memory state + hardware.

        Behavior matches the historical ``RealLabCommunicator.set_lab_state``
        / ``MockLabCommunicator.set_lab_state`` -- the orchestrator
        merges (keeping catalog tags absent from the snapshot),
        normalizes the top-level fields, swaps under the lock, drives
        the per-component hardware apply, and runs the post-snapshot
        hook. Persistence (mock JSON write) is the final step.
        """
        if not isinstance(state, dict):
            raise ValueError("Loaded state must be a JSON object/dict")

        catalog_ids = self._catalog_tag_ids()
        snapshot_components = dict(state.get("components") or {})

        with self._state_lock:
            prev_components = dict(self.current_state.get("components") or {})

        merged, kept = merge_snapshot_components(
            snapshot_components,
            prev_components,
            catalog_ids,
            log_prefix=self.log_prefix,
        )
        if kept:
            print(
                f"{self.log_prefix} Load state merge: kept {len(kept)} "
                f"catalog component(s) not in snapshot: {kept}"
            )

        new_state = normalize_loaded_state(state, merged)

        self._lab_runtime.replace_state(
            new_state,
            kind=MutationKind.ADMINISTRATIVE_LOAD,
            source="set_lab_state",
        )
        with self._state_lock:
            components_to_apply = dict(self.current_state.get("components") or {})

        for tag_id, entry in components_to_apply.items():
            lab_pose = LabPose.from_entry(entry)
            placed_flag = is_on_table(entry) if isinstance(entry, dict) else False
            self._apply_loaded_pose_to_hardware(
                tag_id, lab_pose, is_placed=placed_flag
            )

        self._post_apply_snapshot(components_to_apply)
        self._persist_state()

    # ---------------------------------------------------------------
    # Motor primitive orchestrators (Phase 2A)
    # ---------------------------------------------------------------

    async def move_motor(
        self, target_id: str, motor_id: int, distance: float
    ) -> None:
        """Rotate a motor — see :func:`lab_model.orchestration.run_move_motor`."""
        await run_move_motor(self, target_id, motor_id, distance)

    async def _primitive_move_motor(
        self, target_id: str, motor_id: int, distance: float
    ) -> None:
        """Hardware step for :meth:`move_motor`.

        Real dispatches to ``experiment.<motor_controller>.move_motor``.
        Mock sleeps. The orchestrator handles status, refusals, and the
        ``motor_rotation_store`` bookkeeping; the hook is just the
        cross-wall call.
        """
        raise NotImplementedError

    async def motor_send_home(self, target_id: str, motor_id: int) -> None:
        """Hardware-move by ``-tracked_angle`` so cumulative angle becomes 0.

        Macro: defers to :meth:`move_motor` with the negation of the
        software-tracked angle. No-op when the tracker already reads
        zero (avoids dispatching a 0-degree move).
        """
        if not self._motor_catalog_ok(target_id, motor_id):
            print(
                f"{self.log_prefix} motor_send_home: invalid tag or "
                f"motor_id for {target_id} m{motor_id}"
            )
            return
        cur = motor_rot.get_angle(target_id, motor_id)
        if abs(cur) < 1e-12:
            return
        await self.move_motor(target_id, motor_id, -cur)

    async def motor_set_zero(self, target_id: str, motor_id: int) -> None:
        """Define the current physical position as angle 0 (software only).

        Catalog gate then ``motor_rotation_store.set_zero``. No
        hardware call (the motor doesn't move) and no status flip
        (the operation is instantaneous).
        """
        if not self._motor_catalog_ok(target_id, motor_id):
            print(
                f"{self.log_prefix} motor_set_zero: invalid tag or "
                f"motor_id for {target_id} m{motor_id}"
            )
            return
        await self._primitive_motor_set_zero(target_id, motor_id)
        motor_rot.set_zero(target_id, motor_id)
        print(
            f"{self.log_prefix} Motor {motor_id} on {target_id}: "
            f"zero reference set (software)."
        )

    async def set_motor_setpoint(
        self, target_id: str, motor_id: int, angle_deg: float
    ) -> None:
        """Commit ``tunables.nominal_motor_positions`` (see ``lab_model.tunables.nominal_motor_positions``)."""
        from lab_model.tunables import nominal_motor_positions as motor_tunable

        await motor_tunable.apply(self, target_id, int(motor_id), float(angle_deg))

    async def set_exposure_time_ms(self, target_id: str, exposure_time_ms: float) -> None:
        """Commit ``tunables.exposure_time_ms`` (see ``lab_model.tunables.exposure_time_ms``)."""
        from lab_model.tunables import exposure_time_ms as exposure_tunable

        await exposure_tunable.apply(self, target_id, float(exposure_time_ms))

    async def set_output_power_mw(self, target_id: str, output_power_mw: float) -> None:
        """Commit ``tunables.output_power_mw`` (see ``lab_model.tunables.output_power_mw``)."""
        from lab_model.tunables import output_power_mw as laser_tunable

        await laser_tunable.apply(self, target_id, float(output_power_mw))

    async def _primitive_motor_set_zero(self, target_id: str, motor_id: int) -> None:
        """Hardware step for :meth:`motor_set_zero` (default no-op).

        No real backend supports re-zeroing a motor encoder from
        software today, so both real and mock leave this as the
        default no-op. Future hardware that *does* support hardware-
        side re-zero (e.g. a motor controller with a writable
        reference register) overrides this hook.
        """
        return

    # ---------------------------------------------------------------
    # Heavy-state primitive orchestrators (Phase 2C)
    # ---------------------------------------------------------------
    #
    # All five "move on the table" primitives below share a single
    # hardware hook -- :meth:`_primitive_move_component`. The variation is purely in:
    #
    # - Which refusal gate runs (BREADBOARD vs STORED start state).
    # - Which slot is picked (none / first-free / current cell).
    # - Which commit shape lands (BREADBOARD vs STORAGE).
    # - Whether the storage-intent file is updated post-move (a
    #   real-only side effect surfaced via :meth:`_after_move_to_storage`
    #   / :meth:`_after_move_out_of_storage` virtual hooks).

    async def move_component(
        self, target_id: str, target_pose: Dict[str, float]
    ) -> None:
        await run_move_component(self, target_id, target_pose)

    async def store_component(self, target_id: str) -> None:
        await run_store_component(self, target_id)

    async def place_from_storage(
        self, target_id: str, target_pose: Dict[str, Any]
    ) -> None:
        await run_place_from_storage(self, target_id, target_pose)

    async def repack_storage_slot(self, target_id: str) -> None:
        await run_repack_storage_slot(self, target_id)

    async def recenter_stored_in_inventory(self, target_id: str) -> None:
        await run_recenter_stored_in_inventory(self, target_id)

    async def affirm_placed_at_current(self, target_id: str) -> None:
        await run_affirm_placed_at_current(self, target_id)

    async def add_component_to_state(self, component_data: Dict[str, Any]) -> None:
        """Insert a new component entry into ``current_state``.

        Default behavior: validate ``tag_id``, refuse on duplicate,
        and delegate the entry-construction to
        :meth:`_primitive_add_component_to_state`. Backends may override the
        hook to either:

        - Build a full entry (mock: catalog-aware UI placement,
          including storage-slot allocation).
        - Return ``None`` to refuse (real: parts join the inventory
          via a physical scan, not an API call).
        """
        tag_id = (component_data or {}).get("tag_id")
        if not tag_id:
            print(f"{self.log_prefix} Error: no tag_id in add_component request.")
            return
        with self._state_lock:
            existing = dict(self.current_state.get("components") or {})
        if tag_id in existing:
            print(
                f"{self.log_prefix} Component {tag_id} already exists. Skipping."
            )
            return

        entry = await self._primitive_add_component_to_state(component_data, existing)
        if entry is None:
            return
        with self._state_lock:
            comps = self.current_state.setdefault("components", {})
            comps[tag_id] = entry
            self.current_state["last_updated"] = datetime.now().isoformat()
        self._persist_state()
        from lab_model.domain.component import presence_of

        pres = presence_of(entry)
        print(f"{self.log_prefix} Added {tag_id} presence={pres}")

    async def add_component_from_inventory(
        self,
        component_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Place a catalog part on the bench from inventory (OFF_TABLE or library-only).

        Ensures the tag is listed in ``active_catalog.json``, then either
        reactivates an existing OFF_TABLE entry or inserts a new component.
        Writes through :class:`~lab_model.state.runtime_manager.RuntimeManager`.
        """
        from lab_model.catalog.active_catalog_store import ensure_tag_in_active_catalog
        from lab_model.catalog.bundle import library_by_tag
        from lab_model.domain.component import is_off_table, presence_of
        from lab_model.state.runtime_manager import MutationKind

        tag_id = (component_data or {}).get("tag_id")
        if not tag_id:
            raise ValueError("tag_id is required")

        by_tag = library_by_tag()
        lib_row = by_tag.get(tag_id)
        if lib_row is None:
            raise ValueError(f"Unknown tag_id: {tag_id}")

        catalog_updated = ensure_tag_in_active_catalog(tag_id)
        if catalog_updated and hasattr(self, "_load_catalog"):
            self._load_catalog()

        payload = dict(component_data or {})
        if not payload.get("type"):
            payload["type"] = lib_row.get("type", "OPTICAL_MIRROR")

        with self._state_lock:
            existing = dict(self.current_state.get("components") or {})

        if tag_id in existing:
            entry = existing[tag_id]
            if not is_off_table(entry):
                raise ValueError(f"Component {tag_id} is already on the layout")
            new_entry = await self._primitive_reactivate_off_table_component(
                tag_id,
                entry,
                payload,
                existing,
            )
            if new_entry is None:
                raise RuntimeError(
                    f"Cannot reactivate {tag_id} from inventory on this backend"
                )
        else:
            new_entry = await self._primitive_add_component_to_state(payload, existing)
            if new_entry is None:
                raise RuntimeError(f"Cannot add {tag_id} from inventory on this backend")

        def _apply(state: Dict[str, Any]) -> None:
            comps = state.setdefault("components", {})
            comps[tag_id] = new_entry

        self._lab_runtime.mutate(
            _apply,
            kind=MutationKind.ADMINISTRATIVE_LOAD,
            source=f"inventory_add:{tag_id}",
        )
        self._persist_state()

        return {
            "status": "ok",
            "tag_id": tag_id,
            "presence": presence_of(new_entry),
            "catalog_updated": catalog_updated,
        }

    async def track_component(self, component_data: Dict[str, Any]) -> Dict[str, Any]:
        """Enable operator control for a part (active catalog) without moving it on the table."""
        from lab_model.catalog.active_catalog_store import ensure_tag_in_active_catalog
        from lab_model.catalog.bundle import library_by_tag
        from lab_model.domain.component import (
            PRESENCE_OFF_TABLE,
            new_component_entry,
            presence_of,
        )
        from lab_model.state.runtime_manager import MutationKind

        tag_id = (component_data or {}).get("tag_id")
        if not tag_id:
            raise ValueError("tag_id is required")

        by_tag = library_by_tag()
        lib_row = by_tag.get(tag_id)
        if lib_row is None:
            raise ValueError(f"Unknown tag_id: {tag_id}")

        catalog_updated = ensure_tag_in_active_catalog(tag_id)
        if catalog_updated and hasattr(self, "_load_catalog"):
            self._load_catalog()

        comp_type = (component_data or {}).get("type") or lib_row.get("type", "OPTICAL_MIRROR")

        with self._state_lock:
            existing = dict(self.current_state.get("components") or {})

        if tag_id in existing:
            entry = existing[tag_id]
            return {
                "status": "ok",
                "tag_id": tag_id,
                "tracked": True,
                "presence": presence_of(entry),
                "catalog_updated": catalog_updated,
                "action": "catalog_only",
            }

        new_entry = new_component_entry(
            tag_id,
            comp_type,
            presence=PRESENCE_OFF_TABLE,
            nominal_pose={"x": 0.0, "y": 0.0, "rotation": 0.0},
            meas_pose={"x": 0.0, "y": 0.0, "rotation": 0.0},
            placement_mode="MANUAL",
            in_storage=False,
            slot=None,
        )

        def _apply(state: Dict[str, Any]) -> None:
            comps = state.setdefault("components", {})
            comps[tag_id] = new_entry

        self._lab_runtime.mutate(
            _apply,
            kind=MutationKind.ADMINISTRATIVE_LOAD,
            source=f"track_component:{tag_id}",
        )
        self._persist_state()

        return {
            "status": "ok",
            "tag_id": tag_id,
            "tracked": True,
            "presence": PRESENCE_OFF_TABLE,
            "catalog_updated": catalog_updated,
            "action": "created_off_table",
        }

    async def untrack_component(self, tag_id: str) -> Dict[str, Any]:
        """Remove a part from the active (controlled) catalog without deleting runtime state."""
        from lab_model.catalog.active_catalog_store import remove_tag_from_active_catalog

        tid = (tag_id or "").strip()
        if not tid:
            raise ValueError("tag_id is required")

        catalog_updated = remove_tag_from_active_catalog(tid)
        if catalog_updated and hasattr(self, "_load_catalog"):
            self._load_catalog()

        return {
            "status": "ok",
            "tag_id": tid,
            "tracked": False,
            "catalog_updated": catalog_updated,
        }

    async def _primitive_reactivate_off_table_component(
        self,
        tag_id: str,
        existing_entry: Dict[str, Any],
        component_data: Dict[str, Any],
        existing_components: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Hook: move an OFF_TABLE entry onto the breadboard or into storage."""
        import copy

        from lab_model.domain.component import (
            PRESENCE_BREADBOARD,
            measurables_bucket,
            set_reported_pose,
            tunables_bucket,
        )

        placement_mode = (component_data.get("placement_mode") or "breadboard").lower()
        entry = copy.deepcopy(existing_entry)
        if component_data.get("type"):
            entry["type"] = component_data["type"]
        tun = tunables_bucket(entry)
        pose = {"x": 0.0, "y": 0.0, "rotation": 0.0}
        tun["presence"] = PRESENCE_BREADBOARD
        tun["nominal_pose"] = dict(pose)
        tun["storage"] = {"in_storage": False, "slot": None}
        tun["placement"] = {"mode": "MANUAL"}
        set_reported_pose(entry, pose)
        return entry

    async def _primitive_add_component_to_state(
        self,
        component_data: Dict[str, Any],
        existing_components: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Hook: build a complete component entry, or ``None`` to refuse.

        ``existing_components`` is a snapshot of the current
        ``current_state["components"]`` (dict copy, taken under the
        lock by the orchestrator) -- backends that need to allocate a
        free pose / storage slot use it without reaching back into
        ``self.current_state``.

        Default returns a minimal default-pose entry on the
        breadboard. Mock overrides for catalog-aware UI placement;
        real overrides to refuse (returns ``None`` and prints a
        message asking the operator to physically place the part).
        """
        tag_id = component_data.get("tag_id")
        comp_type = component_data.get("type", "OPTICAL_MIRROR")
        return new_component_entry(
            tag_id,
            comp_type,
            presence=PRESENCE_BREADBOARD,
            nominal_pose={"x": 0.0, "y": 0.0, "rotation": 0.0},
            meas_pose={"x": 0.0, "y": 0.0, "rotation": 0.0},
            placement_mode="MANUAL",
            in_storage=False,
            slot=None,
        )

    async def remove_component(self, target_id: str) -> None:
        """Remove ``target_id`` from current_state. Backends may override.

        Default removes the entry from ``current_state["components"]``
        and persists. Real overrides as a no-op (parts leave the
        inventory via a physical scan, not an API call).
        """
        print(f"{self.log_prefix} Remove {target_id}")
        with self._state_lock:
            comps = self.current_state.get("components") or {}
            if target_id not in comps:
                return
            del comps[target_id]
            self.current_state["last_updated"] = datetime.now().isoformat()
        self._persist_state()

    # ---------------------------------------------------------------
    # Optimization primitive (Phase 2D)
    # ---------------------------------------------------------------
    #
    # Distinct from the other primitives in two ways:
    #
    # 1. Long-running. ``experiment.optimize_component`` blocks for
    #    minutes; the orchestrator dispatches it via ``asyncio.to_thread``
    #    so the lab-state poll keeps running.
    # 2. Stepwise progress. The hook publishes step updates back to
    #    state via the orchestrator's ``progress_callback`` closure.
    #    See ``communicator_refactor.md`` §6.2 for the contract.
    #
    # Real wires up two side-channel mechanisms inside the hook:
    # - The ``cloudlab_progress_callback`` that updates per-component
    #   ghost / physical poses for Newton sub-moves (drives the live
    #   canvas overlay; lives in ``real/optimization.py``).
    # - The ``monitor_optimization_dir`` background thread, which
    #   counts PNGs landing in the per-run subdirectory and bumps
    #   ``optimization_step`` independently (started once at boot in
    #   ``RealLabCommunicator.__init__``).
    # Both stay real-only; the orchestrator just owns the high-level
    # status / step / run-dir bookkeeping.

    async def optimize_component(
        self, target_id: str, strategy_name: str, params: Dict[str, Any]
    ) -> None:
        """Optimize — see :func:`lab_model.orchestration.run_optimize_component`."""
        await run_optimize_component(self, target_id, strategy_name, params)

    def _primitive_prepare_optimization_run(
        self, target_id: str, strategy_name: str
    ) -> Optional[str]:
        """Backend-side setup for one optimization run; default no-op.

        Real overrides to create a per-run subdirectory under
        ``Camera_Images`` and store its absolute path on
        ``self._active_optimization_image_dir`` so the file watcher
        knows where to look. The basename is what we expose to the
        UI via ``current_state["optimization_run_dir"]``.

        Mock returns ``None`` -- there is no per-run directory.
        """
        return None

    async def _primitive_optimize_component(
        self,
        *,
        target_id: str,
        strategy_name: str,
        params: Dict[str, Any],
        progress_callback: "Callable[..., None]",
    ) -> Optional[Dict[str, Any]]:
        """Hardware step for :meth:`optimize_component`.

        Drives the actual strategy run (``experiment.optimize_component``
        on real, simulated sleep loop on mock). Returns either:

        - ``None`` -- run completed without producing summary data; the
          orchestrator skips the success commit (status still flips
          back to IDLE in ``finally``).
        - ``{"score": float, "final_pose": Optional[dict]}`` -- the
          orchestrator passes this through
          :func:`commit_optimization_complete`.

        ``progress_callback(step=N)`` is the orchestrator-built
        closure. Hooks call it whenever they have a step boundary;
        real lets the file-watcher thread do the counting and so does
        not call it explicitly. Mock ticks it on every simulated step.
        """
        raise NotImplementedError

    async def _primitive_run_ensemble_optimization(
        self,
        *,
        spec: Any,
        x0: Dict[str, Any],
        session_id: str,
        progress_callback: "Callable[..., None]",
        should_abort: Optional["Callable[[], bool]"] = None,
    ) -> Optional[Dict[str, Any]]:
        """Hardware step for ensemble ``OPTIMIZE`` (Phase 1 default: not implemented).

        Expected return shape::

            {
                "session_id": str,
                "best_loss": float,
                "final_values": {variable_id: physical_value, ...},
            }

        Mock/real backends override to call
        ``lab_model.optimization.run_ensemble_optimization``.
        """
        raise NotImplementedError

    def _primitive_finalize_optimization_run(self) -> None:
        """Backend-side teardown for one optimization run; default no-op.

        Real overrides to remove the Newton place-UI hook (the
        wrapper around ``place_component_wo_home_specific_xy_cloudlab``)
        and clear ``self._active_optimization_image_dir`` so the file
        watcher reverts to the global ``Camera_Images`` directory.
        Mock has no per-run resources so the default no-op is correct.
        """
        return

    # ---------------------------------------------------------------
    # Heavy-state shared internals (Phase 2C)
    # ---------------------------------------------------------------

    def _allocate_storage_slot(
        self, target_id: str
    ) -> Optional["tuple[float, float, int, int]"]:
        """Find the next free storage cell that fits ``target_id``.

        Returns ``(slot_x, slot_y, slot_i, slot_j)`` or ``None`` when
        the storage grid is full / no cell can host this part. Pure
        catalog + state read; safe to call without holding a lock.
        """
        w, h = self._catalog_wh(target_id)
        with self._state_lock:
            comps = dict(self.current_state.get("components") or {})
        return find_storage_slot_and_center(
            comps, target_id, w, h, lambda tid: self._catalog_wh(tid)
        )

    async def _primitive_move_component(
        self, target_id: str, commanded: LabPose
    ) -> Optional[LabPose]:
        """Hardware step for the move family of primitives.

        Receives the commanded lab-frame pose ``(x, y, rotation)``
        (``z`` is ignored -- on-table parts have no z component).
        Returns ``None`` to commit the commanded pose verbatim, or a
        :class:`LabPose` carrying a noisy "actual achieved" pose for
        ``measurables.pose`` (mock surfaces measurement-noise this
        way; real returns ``None``).
        """
        raise NotImplementedError

    # --- Storage-intent virtual hooks (real-only side effect) ----------

    def _after_move_to_storage(
        self, target_id: str, slot_i: int, slot_j: int
    ) -> None:
        """Persist a "this tag is in this slot" record. Default: no-op.

        Real overrides to write
        :class:`StorageIntentStore`. Mock has no separate intent file
        (the slot is already in ``components[tag].tunables.storage``).
        """
        return

    def _after_move_out_of_storage(self, target_id: str) -> None:
        """Drop the storage-intent record. Default: no-op.

        Real overrides to call ``StorageIntentStore.remove(target_id)``.
        """
        return

    def _apply_is_placed_flag(self, target_id: str, value: bool) -> None:
        """Reflect ``is_placed`` on the hardware-side component object.

        Default: no-op. Real overrides to write
        ``OpticalComponent.is_placed`` so the optimization layer
        agrees with the lab-state view. The Stage C lint allows this
        attribute write here and in :meth:`_apply_loaded_pose_to_hardware`
        only.
        """
        return

    # ---------------------------------------------------------------
    # In-air primitive orchestrators (Phase 2B)
    # ---------------------------------------------------------------

    async def pick_component(
        self, target_id: str, params: Dict[str, Any]
    ) -> None:
        await run_pick_component(self, target_id, params)

    async def _primitive_pick_component(
        self, target_id: str, commanded: LabPose, params: Dict[str, Any]
    ) -> float:
        """Hardware step for :meth:`pick_component`.

        Receives the commanded XY/rotation (from ``measurables.pose``)
        and returns the z_lab the part will hold at after the retract.
        Real asks ``compute_intent_hover_z_lab(component)`` (math-only,
        no motion); mock returns ``DEFAULT_HOVER_Z_MM``.
        """
        raise NotImplementedError

    async def hover_component(
        self, target_id: str, target_pose: Dict[str, float]
    ) -> None:
        await run_hover_component(self, target_id, target_pose)

    async def _primitive_hover_component(
        self, target_id: str, commanded: LabPose, speed: int
    ) -> Optional[LabPose]:
        """Hardware step for :meth:`hover_component`. Return optional achieved pose."""
        raise NotImplementedError

    async def place_from_hover(
        self, target_id: str, target_pose: Dict[str, float]
    ) -> None:
        await run_place_from_hover(self, target_id, target_pose)

    async def _primitive_place_from_hover(
        self,
        target_id: str,
        commanded: LabPose,
        params: Dict[str, Any],
    ) -> None:
        """Hardware step for :meth:`place_from_hover`.

        ``params`` is the original input dict so backends can pull
        side-channel knobs like ``safe_z`` without forcing them onto
        the canonical ``LabPose`` shape.
        """
        raise NotImplementedError

    async def scan_rotate_in_place(
        self, target_id: str, params: Dict[str, Any]
    ) -> None:
        await run_scan_rotate_in_place(self, target_id, params)

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
        on_rotation_update: "Callable[[float], None]",
    ) -> None:
        """Hardware step for :meth:`scan_rotate_in_place`.

        ``mode`` is ``"held"`` or ``"placed"``. The hook runs the
        sweep; for stepwise UI updates it calls
        ``on_rotation_update(rotation)`` (the closure constructed by
        the orchestrator -- it serializes per-step writes through the
        state lock without the hook ever touching ``current_state``).
        """
        raise NotImplementedError

    async def confirm_holding_tag(self, tag_id: str) -> None:
        await run_confirm_holding_tag(self, tag_id)

    # ---------------------------------------------------------------
    # Hardware probes (default no-op / fall-through)
    # ---------------------------------------------------------------

    def get_gripper_status(self) -> Dict[str, Any]:
        """Hardware poll for "is the gripper closed on something?".

        Default for backends without gripper introspection:
        ``closed=False``. Real overrides; mock returns ``closed=True``
        only when the dev flag ``MOCK_GRIPPER_CLOSED_ON_BOOT`` is set.
        """
        return {"closed": False, "confidence": None, "source": "default"}

    def get_video_feed_status(self) -> Dict[str, Any]:
        return {"connected": True, "source": "/static/mock_feed.svg"}

    def get_video_stream(self):
        """Returns a generator yielding MJPEG frames. Real overrides."""
        raise NotImplementedError

    def capture_table_cam(self, cam_id: int, exposure: float = 0.2):
        """Single-shot capture from a table recorder camera.

        Default returns ``None``. Real returns PNG bytes; mock returns
        a synthetic PNG for UI testing (overridden on the mock class).
        """
        return None

    def read_camera_bgr(self, tag_id: str):
        """Capture one frame for ``tag_id`` as OpenCV BGR uint8 HxWx3.

        Shared Step C data-plane hook used by ``POST /api/kernels/eval``,
        edge agents, and any path that needs the same layout TorchScript
        kernels already consume. Returns ``None`` when capture fails —
        callers must not invent synthetic frames on real backends.
        """
        from lab_model.measurables.capture import read_camera_bgr_for_tag

        meta = None
        cmap = getattr(self, "catalog_map", None)
        if isinstance(cmap, dict):
            meta = cmap.get(tag_id)
        return read_camera_bgr_for_tag(self, tag_id, meta)

    def table_cam_connect(self, cam_id: int) -> Tuple[bool, str]:
        """Optional HTTP hook for lazy table-cam ownership (real cloudlabs build)."""
        return False, "table cam lifecycle is not available for this backend"

    def table_cam_disconnect(self, cam_id: int) -> Tuple[bool, str]:
        return False, "table cam lifecycle is not available for this backend"

    def table_cam_live_set(
        self, cam_id: int, enabled: bool, *, profile: str = "default"
    ) -> Tuple[bool, str]:
        return False, "table cam lifecycle is not available for this backend"

    def table_cam_send_vexp(self, cam_id: int, exposure_s: float) -> Tuple[bool, str]:
        return False, "table cam imaging controls are not available for this backend"

    def table_cam_send_vgain(self, cam_id: int, gain: float) -> Tuple[bool, str]:
        return False, "table cam imaging controls are not available for this backend"

    def get_table_cam_stream(self, cam_id: int = 1, fps: int = 18):
        """Multipart MJPEG generator for ``/api/table-cam/stream`` when implemented."""
        raise NotImplementedError

    def fetch_table_cam_preview_jpeg(self, cam_id: int = 1) -> Optional[bytes]:
        """Latest JPEG for per-component ``telemetry/preview`` when implemented."""
        return None

    def refresh_pose_from_camera(
        self,
        preserve_tag_ids: Optional[List[str]] = None,
        apply_tag_ids: Optional[List[str]] = None,
        tag_ids: Optional[List[str]] = None,
    ) -> None:
        """Re-localize component poses from the camera. Default no-op."""
        return

    def preview_refresh_pose_candidates(
        self,
        tag_ids: Optional[List[str]] = None,
    ) -> Dict[str, Dict[str, float]]:
        """Dry-run scan poses for refresh offers. Default: unsupported (empty)."""
        return {}
