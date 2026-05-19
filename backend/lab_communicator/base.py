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

Architectural rules (enforced by the lints in
``backend/tests/test_lab_primitives.py``):

- ``shared/`` modules can be imported here; ``real/`` and ``mock/``
  cannot. Any cross-backend coupling lives in ``shared/``.
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

from lab_model.component_model import (
    PRESENCE_BREADBOARD,
    default_measurables,
    default_tunables,
    get_measurables,
    get_tunables,
    is_on_table,
    is_stored,
    new_component_entry,
)
from lab_model.holding import (
    DEFAULT_HOVER_Z_MM,
    SYSTEM_STATUS_BUSY,
    SYSTEM_STATUS_HOLDING,
    SYSTEM_STATUS_IDLE,
    SYSTEM_STATUS_OPTIMIZING,
    clear_holding,
    confirm_holding_tag as _confirm_holding_tag_helper,
    empty_holding,
    get_holding,
    is_holding,
    set_holding,
)
from lab_model import motor_rotation_store as motor_rot

from lab_model.storage_region import (
    STORAGE_NOMINAL_ROTATION_DEG,
    find_storage_slot_and_center,
    is_placed_region,
    nominal_center_pose_for_stored_entry,
)

from lab_communicator.shared.catalog_lookup import (
    catalog_wh as _catalog_wh_pure,
    motor_catalog_ok as _motor_catalog_ok_pure,
)
from lab_communicator.shared.commits import (
    commit_affirm_placed,
    commit_hover,
    commit_move_to_breadboard,
    commit_move_to_storage,
    commit_observed_camera_image,
    commit_optimization_complete,
    commit_pick,
    commit_place_from_hover,
    commit_scan_rotation,
)
from lab_communicator.shared.motor_state import (
    inject_motor_rotations_into_state,
    persist_motor_rotations_from_component,
)
from lab_communicator.shared.snapshot import (
    LabPose,
    merge_snapshot_components,
    normalize_loaded_state,
)
from lab_communicator.shared.state_machine import (
    refuse_if_holding,
    refuse_if_holding_other_tag,
    refuse_if_in_storage_quadrant,
    refuse_if_not_holding,
    refuse_if_not_in_state,
    refuse_if_not_on_breadboard,
    refuse_if_not_stored,
    refuse_if_status_not_idle,
    refuse_if_stored,
    refuse_if_z_lab_out_of_bounds,
)


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

    # --- State (subclasses populate in __init__) ---
    current_state: Dict[str, Any]
    catalog_map: Dict[str, Dict[str, Any]]

    def __init__(self) -> None:
        # Subclasses are expected to fully populate ``self.current_state``
        # and ``self.catalog_map`` before any orchestrator runs. This
        # default just makes the attributes safe to access if a subclass
        # forgets a field -- a missing ``components`` block manifests
        # as "empty inventory" rather than a hard AttributeError.
        self._state_lock: threading.RLock = threading.RLock()
        self.current_state = {
            "system_status": SYSTEM_STATUS_IDLE,
            "last_updated": datetime.now().isoformat(),
            "components": {},
            "optimization_step": 0,
            "optimization_run_dir": None,
            "holding": empty_holding(),
        }
        self.catalog_map = {}

    # ---------------------------------------------------------------
    # Catalog accessors
    # ---------------------------------------------------------------

    def get_catalog(self) -> List[Dict[str, Any]]:
        """Return merged catalog rows (``component_library`` ∩ ``active_catalog``).

        Reloads from disk on every call so edits to ``lab_view`` JSON are visible
        without a restart. In-memory :attr:`catalog_map` is refreshed by mock
        via :meth:`~lab_communicator.mock.communicator.MockLabCommunicator._load_catalog`
        when primitives need a fresh mirror.
        """
        from lab_communicator.shared.catalog_bundle import merged_catalog_rows

        try:
            return merged_catalog_rows()
        except Exception:
            return []

    def _catalog_meta_for_tag(self, tag_id: str) -> Optional[Dict[str, Any]]:
        """Catalog metadata for ``tag_id`` (or ``None`` if unknown).

        Default uses :attr:`catalog_map`. Subclasses with a different
        in-memory shape may override (mock historically scanned a list;
        Phase 2A unified both backends on a dict).
        """
        return self.catalog_map.get(tag_id)

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

    def get_lab_state(self) -> Dict[str, Any]:
        """Deep-copy snapshot of ``self.current_state`` for the UI.

        Always:

        - Acquires the state lock for the copy.
        - Injects software motor angles into
          ``components[*].measurables.pose.motor_rotations`` and
          ``components[*].tunables.nominal_motor_positions``.
        - Normalizes ``state["holding"]`` so the UI never sees
          ``undefined`` for held tag / requires_operator_confirm.
        """
        with self._state_lock:
            state = json.loads(json.dumps(self.current_state))
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

    # Legacy aliases preserved for backward compatibility.
    def get_tunables_for_tag(self, tag_id: str) -> Dict[str, Any]:
        return self.return_tunables_for_tag(tag_id)

    def get_measurables_for_tag(self, tag_id: str) -> Dict[str, Any]:
        return self.return_measurables_for_tag(tag_id)

    async def observe_measurables_for_tag(self, tag_id: str) -> Dict[str, Any]:
        """Trigger a fresh observation of ``tag_id`` and return its measurables.

        Template method:

        1. Refuse if the tag is unknown to current_state.
        2. Delegate to :meth:`_primitive_observe_measurables`. The hook
           inspects the catalog ``type`` and may capture a camera frame
           (real lab) or a synthetic frame (mock UI demo); on success
           it returns a ``{"path", "source", "cam_id", "format"}`` dict.
        3. If the hook returned data, commit it under the lock to
           ``measurables.camera_image`` and persist.
        4. Always return the (possibly updated) saved measurables.
        """
        with self._state_lock:
            entry = (self.current_state.get("components") or {}).get(tag_id)
        if not isinstance(entry, dict):
            return {}

        catalog_meta = self._catalog_meta_for_tag(tag_id) or {}
        try:
            captured = await self._primitive_observe_measurables(tag_id, catalog_meta)
        except Exception as e:  # noqa: BLE001 -- hook failures shouldn't kill UI
            print(f"{self.log_prefix} observe_measurables_for_tag failed: {e}")
            captured = None

        if isinstance(captured, dict) and captured.get("path"):
            with self._state_lock:
                commit_observed_camera_image(
                    self.current_state,
                    tag_id,
                    path=str(captured["path"]),
                    source=str(captured.get("source") or ""),
                    cam_id=int(captured.get("cam_id") or 0),
                    fmt=str(captured.get("format") or "png"),
                )
                self.current_state["last_updated"] = datetime.now().isoformat()
            self._persist_state()
        return self.return_measurables_for_tag(tag_id)

    async def _primitive_observe_measurables(
        self, tag_id: str, catalog_meta: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Hardware step for :meth:`observe_measurables_for_tag`.

        Default returns ``None`` (no fresh observation -- saved
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
                st_t = json.loads(json.dumps(src_ent.get("tunables") or {}))
                st_m = json.loads(json.dumps(src_ent.get("measurables") or {}))
                dst["tunables"] = st_t
                dst["measurables"] = st_m
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
        return merged_ids

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

        with self._state_lock:
            self.current_state = new_state
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
        """Rotate a component's motor by ``distance`` degrees (relative).

        Template method:

        1. Refuse if STORED.
        2. Validate the catalog declares this motor.
        3. Status BUSY.
        4. Delegate the hardware step to :meth:`_primitive_move_motor`.
        5. On success, advance ``motor_rotation_store`` by ``distance``.
        6. Status IDLE in ``finally`` (so a hardware failure rolls back
           the BUSY flag).
        """
        print(
            f"{self.log_prefix} Moving motor {motor_id} of {target_id} "
            f"by {distance} (RELATIVE)..."
        )
        with self._state_lock:
            current_snapshot = self.current_state

        refusal = refuse_if_stored(
            current_snapshot, target_id, primitive_name="move motor"
        )
        if refusal:
            print(f"{self.log_prefix} Refusing motor move: {refusal.reason}")
            return

        if not self._motor_catalog_ok(target_id, motor_id):
            mids = (self._catalog_meta_for_tag(target_id) or {}).get("motor_ids") or []
            print(
                f"{self.log_prefix} Error: motor_id {motor_id} invalid for "
                f"{target_id} (motor_ids={mids})."
            )
            return

        self._set_status(SYSTEM_STATUS_BUSY)
        try:
            await self._primitive_move_motor(target_id, motor_id, float(distance))
            motor_rot.add_delta(target_id, motor_id, float(distance))
            print(f"{self.log_prefix} Motor moved.")
        except Exception as e:
            print(f"{self.log_prefix} Motor move failed: {e}")
        finally:
            self._set_status(SYSTEM_STATUS_IDLE)

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
        """Move a placed (BREADBOARD) part to a new (XY, rotation) on the table.

        Template method:

        1. Refuse if STORED (use ``place_from_storage`` instead).
        2. Refuse if the target XY falls in storage Q3 (use
           ``store_component`` instead).
        3. Status BUSY.
        4. Delegate to ``_primitive_move_component(target_id, lab_pose) -> Optional[LabPose]``.
        5. Commit BREADBOARD presence + MANUAL placement at the
           commanded pose; ``measurables.pose`` may carry a noisy
           override from the hook (mock).
        6. Status IDLE in ``finally`` (so a hardware failure rolls back).
        """
        print(f"{self.log_prefix} Move {target_id} -> {target_pose}")
        with self._state_lock:
            snapshot = self.current_state

        for refusal in (
            refuse_if_not_in_state(snapshot, target_id, primitive_name="move"),
            refuse_if_stored(snapshot, target_id, primitive_name="move"),
        ):
            if refusal:
                print(f"{self.log_prefix} Refusing move: {refusal.reason}")
                return

        target_pose = target_pose or {}
        tx = float(target_pose.get("target_x", target_pose.get("x", 0.0)))
        ty = float(target_pose.get("target_y", target_pose.get("y", 0.0)))
        trot = float(target_pose.get("rotation", 0.0))

        bound = refuse_if_in_storage_quadrant(tx, ty, primitive_name="move")
        if bound:
            print(f"{self.log_prefix} Refusing move: {bound.reason}")
            return

        commanded = LabPose(x=tx, y=ty, z=0.0, rotation=trot)
        await self._run_move_to_breadboard(target_id, commanded)

    async def store_component(self, target_id: str) -> None:
        """Move a BREADBOARD part into the storage quadrant at a packed slot.

        Template method:

        1. Refuse if not on the breadboard.
        2. Allocate the next free storage slot via
           :func:`find_storage_slot_and_center` (catalog-aware packing).
        3. Status BUSY → ``_primitive_move_component`` → commit STORAGE presence + slot.
        4. Notify the storage-intent layer (real persists this; mock
           ignores).
        5. Status IDLE in ``finally``.
        """
        print(f"{self.log_prefix} Store {target_id}")
        with self._state_lock:
            snapshot = self.current_state

        refusal = refuse_if_not_on_breadboard(
            snapshot, target_id, primitive_name="store"
        )
        if refusal:
            print(f"{self.log_prefix} Refusing store: {refusal.reason}")
            return

        slot = self._allocate_storage_slot(target_id)
        if slot is None:
            print(f"{self.log_prefix} Refusing store: no free storage slot in Q3.")
            return
        sx, sy, si, sj = slot
        commanded = LabPose(
            x=sx, y=sy, z=0.0, rotation=STORAGE_NOMINAL_ROTATION_DEG
        )
        await self._run_move_to_storage(target_id, commanded, si, sj)

    async def place_from_storage(
        self, target_id: str, target_pose: Dict[str, Any]
    ) -> None:
        """Place a STORED part onto the breadboard at the given lab pose.

        Template method:

        1. Refuse if not currently STORED.
        2. Refuse if the target XY is inside storage Q3 (use
           ``recenter_stored_in_inventory`` if the part stays stored).
        3. Status BUSY → ``_primitive_move_component`` → commit BREADBOARD.
        4. Clear the storage-intent record for this tag (real-only;
           mock no-op).
        """
        print(f"{self.log_prefix} PlaceFromStorage {target_id} -> {target_pose}")
        with self._state_lock:
            snapshot = self.current_state

        refusal = refuse_if_not_stored(
            snapshot, target_id, primitive_name="place_from_storage"
        )
        if refusal:
            print(f"{self.log_prefix} Refusing place_from_storage: {refusal.reason}")
            return

        target_pose = target_pose or {}
        tx = float(target_pose.get("target_x", target_pose.get("x", 0.0)))
        ty = float(target_pose.get("target_y", target_pose.get("y", 0.0)))
        trot = float(target_pose.get("rotation", 0.0))
        if not is_placed_region(tx, ty):
            print(
                f"{self.log_prefix} Refusing place_from_storage: target "
                f"({tx},{ty}) is inside storage quadrant."
            )
            return
        commanded = LabPose(x=tx, y=ty, z=0.0, rotation=trot)
        await self._run_move_to_breadboard(
            target_id, commanded, exiting_storage=True
        )

    async def repack_storage_slot(self, target_id: str) -> None:
        """Move a STORED part to the next free inventory cell.

        Template method: same shape as :meth:`store_component` except
        the pre-condition is "currently STORED" rather than
        "currently BREADBOARD". Used to compact the storage grid.
        """
        print(f"{self.log_prefix} RepackStorageSlot {target_id}")
        with self._state_lock:
            snapshot = self.current_state

        refusal = refuse_if_not_stored(
            snapshot, target_id, primitive_name="repack"
        )
        if refusal:
            print(f"{self.log_prefix} Refusing repack: {refusal.reason}")
            return

        slot = self._allocate_storage_slot(target_id)
        if slot is None:
            print(f"{self.log_prefix} Refusing repack: no free storage slot.")
            return
        sx, sy, si, sj = slot
        commanded = LabPose(
            x=sx, y=sy, z=0.0, rotation=STORAGE_NOMINAL_ROTATION_DEG
        )
        await self._run_move_to_storage(target_id, commanded, si, sj)

    async def recenter_stored_in_inventory(self, target_id: str) -> None:
        """Move a STORED part to the *center* of its currently assigned cell.

        Template method: differs from :meth:`repack_storage_slot` in
        that the slot is the part's *current* cell (resolved from
        the entry's slot metadata or its current pose-in-Q3), not the
        next free one. Used when a stored part has drifted off-center.
        """
        print(f"{self.log_prefix} RecenterStored {target_id}")
        with self._state_lock:
            snapshot = self.current_state
            entry = (snapshot.get("components") or {}).get(target_id)

        refusal = refuse_if_not_stored(
            snapshot, target_id, primitive_name="recenter"
        )
        if refusal:
            print(f"{self.log_prefix} Refusing recenter: {refusal.reason}")
            return

        nom = nominal_center_pose_for_stored_entry(entry or {})
        if nom is None:
            print(
                f"{self.log_prefix} Refusing recenter: cannot resolve storage "
                f"cell (need slot metadata or pose in Q3)."
            )
            return
        sx, sy, si, sj = nom
        commanded = LabPose(
            x=sx, y=sy, z=0.0, rotation=STORAGE_NOMINAL_ROTATION_DEG
        )
        await self._run_move_to_storage(target_id, commanded, si, sj)

    async def affirm_placed_at_current(self, target_id: str) -> None:
        """Mark a STORED part as PLACED at its current pose (no hardware move).

        Resolves the case where the operator hand-moved a stored part
        onto the breadboard manually -- the layout system needs to be
        told to drop the storage-intent record without dispatching a
        robot motion.

        Template method:

        1. Refuse if not currently STORED.
        2. Commit: ``measurables.pose`` -> ``tunables.nominal_pose``,
           presence STORAGE → BREADBOARD, mode = MANUAL.
        3. Clear the storage-intent record.
        4. Apply ``is_placed=True`` to the hardware-side component
           (real-only; mock no-op).
        """
        print(f"{self.log_prefix} AffirmPlacedAtCurrent {target_id}")
        with self._state_lock:
            snapshot = self.current_state

        refusal = refuse_if_not_stored(
            snapshot, target_id, primitive_name="affirm_placed"
        )
        if refusal:
            print(f"{self.log_prefix} Refusing affirm: {refusal.reason}")
            return

        with self._state_lock:
            commit_affirm_placed(self.current_state, target_id)
            self.current_state["last_updated"] = datetime.now().isoformat()
        self._after_move_out_of_storage(target_id)
        self._apply_is_placed_flag(target_id, True)
        self._persist_state()
        print(f"{self.log_prefix} {target_id} marked PLACED at current pose")

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
        pres = (entry.get("tunables") or {}).get("presence")
        print(f"{self.log_prefix} Added {tag_id} presence={pres}")

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
        """Optimize ``target_id`` with the named strategy.

        Template method:

        1. Refuse if the tag is unknown to the catalog.
        2. Refuse if STORED.
        3. Call :meth:`_primitive_prepare_optimization_run` to let the backend
           create any per-run side state (real builds the per-run
           images subdirectory and returns its basename for the UI;
           mock returns ``None``).
        4. Status OPTIMIZING + reset ``optimization_step`` +
           ``optimization_run_dir = run_dir_basename``.
        5. Build the ``progress_callback(step)`` closure and pass it
           into :meth:`_primitive_optimize_component` along with target / strategy /
           params. The hook is free to call the callback as many
           times as it wants; each call updates ``optimization_step``
           under the lock and persists.
        6. On hook success: commit the strategy mode, the score, and
           the final pose to ``measurables.last_optimized_pose``.
        7. ``finally``: call :meth:`_primitive_finalize_optimization_run` (real
           tears down the Newton place hook), reset status to IDLE,
           clear the step + run-dir fields.
        """
        print(
            f"{self.log_prefix} Optimize {target_id} with {strategy_name} "
            f"(params keys={sorted((params or {}).keys())})"
        )

        # Catalog gate first -- if the tag is unknown we don't even
        # know what hardware to talk to. Real additionally rejects
        # tags missing from ``component_map``; we surface that via the
        # hook (raising in the hook lands in the orchestrator's except
        # branch and the finally still runs).
        if not self._catalog_meta_for_tag(target_id):
            print(
                f"{self.log_prefix} Refusing optimize: {target_id} not in "
                f"catalog."
            )
            return

        with self._state_lock:
            snapshot = self.current_state
        refusal = refuse_if_stored(snapshot, target_id, primitive_name="optimize")
        if refusal:
            print(f"{self.log_prefix} Refusing optimize: {refusal.reason}")
            return

        run_dir_basename = self._primitive_prepare_optimization_run(target_id, strategy_name)

        with self._state_lock:
            self.current_state["system_status"] = SYSTEM_STATUS_OPTIMIZING
            self.current_state["optimization_step"] = 0
            self.current_state["optimization_run_dir"] = run_dir_basename
            self.current_state["last_updated"] = datetime.now().isoformat()
        self._persist_state()

        def progress_callback(*, step: Optional[int] = None) -> None:
            """Closure passed to the hook for stepwise UI updates.

            Currently only ``step`` is supported -- the hook signals
            "iteration N landed" and we update ``optimization_step``
            for the UI's progress bar. Future fields (e.g. live score)
            can be added without changing the hook signature: keyword-
            only args are inherently extensible.
            """
            with self._state_lock:
                if step is not None:
                    self.current_state["optimization_step"] = int(step)
                self.current_state["last_updated"] = datetime.now().isoformat()
            self._persist_state()

        result: Optional[Dict[str, Any]] = None
        try:
            result = await self._primitive_optimize_component(
                target_id=target_id,
                strategy_name=strategy_name,
                params=params or {},
                progress_callback=progress_callback,
            )
        except Exception as e:  # noqa: BLE001 -- log + clean exit
            print(f"{self.log_prefix} Optimization failed: {e}")
            result = None

        if isinstance(result, dict):
            score = float(result.get("score", 1.0))
            final_pose = result.get("final_pose")
            with self._state_lock:
                commit_optimization_complete(
                    self.current_state,
                    target_id,
                    strategy_name=strategy_name,
                    score=score,
                    final_pose=final_pose if isinstance(final_pose, dict) else None,
                )
                self.current_state["last_updated"] = datetime.now().isoformat()
            self._persist_state()

        try:
            self._primitive_finalize_optimization_run()
        finally:
            with self._state_lock:
                self.current_state["system_status"] = SYSTEM_STATUS_IDLE
                self.current_state["optimization_step"] = 0
                self.current_state["optimization_run_dir"] = None
                self.current_state["last_updated"] = datetime.now().isoformat()
            self._persist_state()
            print(
                f"{self.log_prefix} Optimization complete for {target_id} "
                f"({strategy_name})"
            )

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

    async def _run_move_to_breadboard(
        self,
        target_id: str,
        commanded: LabPose,
        *,
        exiting_storage: bool = False,
    ) -> None:
        """Shared engine: BUSY → ``_primitive_move_component`` → BREADBOARD commit → IDLE."""
        self._set_status(SYSTEM_STATUS_BUSY)
        actual: Optional[LabPose] = None
        try:
            actual = await self._primitive_move_component(target_id, commanded)
        except Exception as e:  # noqa: BLE001 -- log + roll status
            print(f"{self.log_prefix} Move failed: {e}")
            self._set_status(SYSTEM_STATUS_IDLE)
            return

        with self._state_lock:
            commit_move_to_breadboard(
                self.current_state,
                target_id,
                x=commanded.x,
                y=commanded.y,
                rotation=commanded.rotation,
                actual_pose=actual,
            )
            self.current_state["last_updated"] = datetime.now().isoformat()
        if exiting_storage:
            self._after_move_out_of_storage(target_id)
        self._apply_is_placed_flag(target_id, True)
        self._set_status(SYSTEM_STATUS_IDLE)

    async def _run_move_to_storage(
        self,
        target_id: str,
        commanded: LabPose,
        slot_i: int,
        slot_j: int,
    ) -> None:
        """Shared engine: BUSY → ``_primitive_move_component`` → STORAGE commit → IDLE."""
        self._set_status(SYSTEM_STATUS_BUSY)
        actual: Optional[LabPose] = None
        try:
            actual = await self._primitive_move_component(target_id, commanded)
        except Exception as e:  # noqa: BLE001
            print(f"{self.log_prefix} Move failed: {e}")
            self._set_status(SYSTEM_STATUS_IDLE)
            return

        with self._state_lock:
            commit_move_to_storage(
                self.current_state,
                target_id,
                x=commanded.x,
                y=commanded.y,
                rotation=commanded.rotation,
                slot_i=slot_i,
                slot_j=slot_j,
                actual_pose=actual,
            )
            self.current_state["last_updated"] = datetime.now().isoformat()
        self._after_move_to_storage(target_id, slot_i, slot_j)
        self._apply_is_placed_flag(target_id, False)
        self._set_status(SYSTEM_STATUS_IDLE)

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
        """Approach ``target_id``, close gripper, retract to safe Z.

        Template method:

        1. Refuse if already HOLDING (single-gripper invariant).
        2. Refuse if STORED (use PLACE_FROM_STORAGE first).
        3. Refuse if no entry in state.
        4. Read commanded XY/rotation from ``measurables.pose``.
        5. Status BUSY.
        6. Delegate to ``_primitive_pick_component(target_id, lab_pose, params) -> float``;
           the returned ``settled_z`` is the z_lab the lab will hold the
           part at.
        7. Commit HOLDING (tunables.nominal_pose, measurables.pose,
           top-level holding) at the commanded XY/rotation and the
           returned settled_z.
        8. Status HOLDING. On hook failure, status rolls back to IDLE.
        """
        print(f"{self.log_prefix} Pick {target_id}...")
        with self._state_lock:
            snapshot = self.current_state

        for refusal in (
            refuse_if_holding(snapshot, primitive_name="pick"),
            refuse_if_stored(snapshot, target_id, primitive_name="pick"),
            refuse_if_not_in_state(snapshot, target_id, primitive_name="pick"),
        ):
            if refusal:
                print(f"{self.log_prefix} Refusing pick: {refusal.reason}")
                return

        with self._state_lock:
            entry = (self.current_state.get("components") or {}).get(target_id) or {}
        pose_dict = ((entry.get("measurables") or {}).get("pose") or {})
        commanded = LabPose(
            x=float(pose_dict.get("x", 0.0)),
            y=float(pose_dict.get("y", 0.0)),
            z=0.0,  # picks ignore z_lab on input -- the hook returns settled_z
            rotation=float(pose_dict.get("rotation", 0.0)),
        )

        self._set_status(SYSTEM_STATUS_BUSY)
        try:
            settled_z = await self._primitive_pick_component(target_id, commanded, params)
        except Exception:
            self._set_status(SYSTEM_STATUS_IDLE)
            raise

        settled_z_f = float(settled_z)
        with self._state_lock:
            commit_pick(
                self.current_state,
                target_id,
                x=commanded.x,
                y=commanded.y,
                rotation=commanded.rotation,
                settled_z=settled_z_f,
            )
            self.current_state["last_updated"] = datetime.now().isoformat()
        self._set_status(SYSTEM_STATUS_HOLDING)
        print(
            f"{self.log_prefix} Picked {target_id} at "
            f"({commanded.x:.1f},{commanded.y:.1f},rot={commanded.rotation:.1f}) "
            f"-> HOLDING @ z_lab={settled_z_f:.1f} mm"
        )

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
        """Reposition an already-held part in mid-air.

        Template method:

        1. Refuse if not HOLDING.
        2. Same-tag gate (the held tag must match ``target_id``).
        3. Parse XY/rotation/z/speed from the input dict.
        4. Bounds-check ``z_lab`` against ``max_safe_hover_z_lab_mm``.
        5. Status BUSY.
        6. Delegate to ``_primitive_hover_component(target_id, lab_pose, speed) -> Optional[LabPose]``.
           ``None`` means "use commanded verbatim"; a value means
           "this is what the hardware actually achieved" (mock returns
           commanded + small Gaussian noise).
        7. Commit HOLDING at the commanded pose; ``measurables.pose``
           uses the actual_pose override when provided.
        8. Status HOLDING (or rolls back on hook failure).
        """
        print(f"{self.log_prefix} Hover {target_id} -> {target_pose}")
        with self._state_lock:
            snapshot = self.current_state

        for refusal in (
            refuse_if_not_holding(snapshot, primitive_name="hover"),
            refuse_if_holding_other_tag(snapshot, target_id, primitive_name="hover"),
        ):
            if refusal:
                print(f"{self.log_prefix} Refusing hover: {refusal.reason}")
                return

        target_pose = target_pose or {}
        tx = float(target_pose.get("target_x", target_pose.get("x", 0.0)))
        ty = float(target_pose.get("target_y", target_pose.get("y", 0.0)))
        trot = float(target_pose.get("rotation", 0.0))
        tz = float(target_pose.get("z", DEFAULT_HOVER_Z_MM))
        try:
            speed = int(target_pose.get("speed", 100))
        except (TypeError, ValueError):
            speed = 100

        bound = refuse_if_z_lab_out_of_bounds(
            tz,
            max_safe_z_lab_mm=self.max_safe_hover_z_lab_mm,
            primitive_name="hover",
        )
        if bound:
            print(f"{self.log_prefix} Refusing hover: {bound.reason}")
            return

        commanded = LabPose(x=tx, y=ty, z=tz, rotation=trot)

        self._set_status(SYSTEM_STATUS_BUSY)
        try:
            actual = await self._primitive_hover_component(target_id, commanded, speed)
        except Exception:
            # Hover always restores HOLDING -- the part is still in the
            # gripper even if the lab refused the new pose.
            self._set_status(SYSTEM_STATUS_HOLDING)
            raise

        with self._state_lock:
            commit_hover(
                self.current_state,
                target_id,
                x=commanded.x,
                y=commanded.y,
                rotation=commanded.rotation,
                z=commanded.z,
                actual_pose=actual,
            )
            self.current_state["last_updated"] = datetime.now().isoformat()
        self._set_status(SYSTEM_STATUS_HOLDING)
        print(
            f"{self.log_prefix} Hovered {target_id} -> "
            f"({commanded.x:.1f},{commanded.y:.1f},rot={commanded.rotation:.1f},"
            f"z={commanded.z:.1f})"
        )

    async def _primitive_hover_component(
        self, target_id: str, commanded: LabPose, speed: int
    ) -> Optional[LabPose]:
        """Hardware step for :meth:`hover_component`. Return optional achieved pose."""
        raise NotImplementedError

    async def place_from_hover(
        self, target_id: str, target_pose: Dict[str, float]
    ) -> None:
        """Place a held part on the breadboard.

        Template method:

        1. Refuse if not HOLDING.
        2. Same-tag gate.
        3. Parse XY/rotation; refuse if XY falls in storage quadrant
           (use STORE_COMPONENT for that path).
        4. Status BUSY.
        5. Delegate to ``_primitive_place_from_hover(target_id, lab_pose) -> None``.
        6. Commit placed-pose: clear holding, set presence=BREADBOARD,
           strip ``z`` from poses (z is meaningless once on table).
        7. Status IDLE (or HOLDING rollback on failure -- the part is
           still in the gripper).
        """
        print(f"{self.log_prefix} PlaceFromHover {target_id} -> {target_pose}")
        with self._state_lock:
            snapshot = self.current_state

        for refusal in (
            refuse_if_not_holding(snapshot, primitive_name="place_from_hover"),
            refuse_if_holding_other_tag(
                snapshot, target_id, primitive_name="place_from_hover"
            ),
        ):
            if refusal:
                print(f"{self.log_prefix} Refusing place_from_hover: {refusal.reason}")
                return

        target_pose = target_pose or {}
        tx = float(target_pose.get("target_x", target_pose.get("x", 0.0)))
        ty = float(target_pose.get("target_y", target_pose.get("y", 0.0)))
        trot = float(target_pose.get("rotation", 0.0))

        bound = refuse_if_in_storage_quadrant(tx, ty, primitive_name="place_from_hover")
        if bound:
            print(f"{self.log_prefix} Refusing place_from_hover: {bound.reason}")
            return

        commanded = LabPose(x=tx, y=ty, z=0.0, rotation=trot)

        self._set_status(SYSTEM_STATUS_BUSY)
        try:
            await self._primitive_place_from_hover(target_id, commanded, target_pose)
        except Exception:
            # Failed place: the part is still gripped. Keep HOLDING so
            # the operator can retry without re-picking.
            self._set_status(SYSTEM_STATUS_HOLDING)
            raise

        with self._state_lock:
            commit_place_from_hover(
                self.current_state,
                target_id,
                x=commanded.x,
                y=commanded.y,
                rotation=commanded.rotation,
            )
            self.current_state["last_updated"] = datetime.now().isoformat()
        self._set_status(SYSTEM_STATUS_IDLE)
        print(
            f"{self.log_prefix} Placed {target_id} from hover at "
            f"({commanded.x:.1f},{commanded.y:.1f},rot={commanded.rotation:.1f})"
        )

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
        """Sweep rotation across [theta_min, theta_max] (held or placed).

        Template method:

        1. Decide dispatch mode from current state:
           - HOLDING (held tag matches): mode="held".
           - IDLE + on-breadboard: mode="placed".
        2. Validate params (numeric, ``speed > 0``).
        3. Read base pose (XY[Z] for held; XY for placed).
        4. Status BUSY.
        5. Construct ``on_rotation_update`` callback that commits each
           intermediate rotation to ``current_state`` under the lock --
           the hook calls it for stepwise UI updates without touching
           state directly (architectural lint enforces this).
        6. Delegate to ``_primitive_scan_rotate_in_place(...)``.
        7. Final commit at ``theta_max`` + status flip back to HOLDING
           (held) or IDLE (placed).
        """
        with self._state_lock:
            snapshot = self.current_state
            comp_snapshot = (snapshot.get("components") or {}).get(target_id)
            holding_now = is_holding(snapshot)
            status_at_entry = snapshot.get("system_status") or SYSTEM_STATUS_IDLE

        # --- Dispatch decision (mode) ----------------------------------
        if holding_now:
            same_tag = refuse_if_holding_other_tag(
                snapshot, target_id, primitive_name="scan_rotate_in_place"
            )
            if same_tag:
                print(
                    f"{self.log_prefix} Refusing scan_rotate_in_place: "
                    f"{same_tag.reason}"
                )
                return
            mode = "held"
        else:
            if status_at_entry != SYSTEM_STATUS_IDLE:
                print(
                    f"{self.log_prefix} Refusing scan_rotate_in_place: "
                    f"system_status={status_at_entry!r}, need IDLE or HOLDING."
                )
                return
            in_state = refuse_if_not_in_state(
                snapshot, target_id, primitive_name="scan_rotate_in_place"
            )
            if in_state:
                print(
                    f"{self.log_prefix} Refusing scan_rotate_in_place: "
                    f"{in_state.reason}"
                )
                return
            on_table = refuse_if_not_on_breadboard(
                snapshot, target_id, primitive_name="scan_rotate_in_place"
            )
            if on_table:
                print(
                    f"{self.log_prefix} Refusing scan_rotate_in_place: "
                    f"{on_table.reason}"
                )
                return
            mode = "placed"

        # --- Param validation ------------------------------------------
        params = params or {}
        try:
            theta_min = float(params.get("theta_min", 0.0))
            theta_max = float(params.get("theta_max", 0.0))
            speed = float(params.get("speed_deg_per_s", 1.0))
        except (TypeError, ValueError):
            print(f"{self.log_prefix} scan_rotate_in_place: non-numeric params.")
            return
        if speed <= 0:
            print(f"{self.log_prefix} scan_rotate_in_place: speed must be > 0.")
            return
        axis = str(params.get("axis", "z"))

        # --- Base pose lookup ------------------------------------------
        if mode == "held":
            held = get_holding(snapshot)
            base = dict(held.get("nominal_pose") or {})
            base_x = float(base.get("x", 0.0))
            base_y = float(base.get("y", 0.0))
            base_z: Optional[float] = float(base.get("z", DEFAULT_HOVER_Z_MM))
        else:
            cur_pose = ((comp_snapshot or {}).get("measurables") or {}).get("pose") or {}
            base_x = float(cur_pose.get("x", 0.0))
            base_y = float(cur_pose.get("y", 0.0))
            base_z = None  # placed parts: z not stored on nominal_pose

        print(
            f"{self.log_prefix} ScanRotate mode={mode} {target_id}: "
            f"{theta_min} deg -> {theta_max} deg @ {speed} deg/s axis={axis}"
        )

        # --- Stepwise progress callback (writes under the lock) --------
        def on_rotation_update(rotation: float) -> None:
            with self._state_lock:
                commit_scan_rotation(
                    self.current_state,
                    target_id,
                    mode=mode,
                    x=base_x,
                    y=base_y,
                    rotation=float(rotation),
                    z=base_z,
                )
                self.current_state["last_updated"] = datetime.now().isoformat()
            self._persist_state()

        # --- BUSY → hook → final commit + status flip ------------------
        self._set_status(SYSTEM_STATUS_BUSY)
        final_status = (
            SYSTEM_STATUS_HOLDING if mode == "held" else SYSTEM_STATUS_IDLE
        )
        try:
            await self._primitive_scan_rotate_in_place(
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
        except Exception:
            self._set_status(final_status)
            raise

        # Final commit at theta_max (the hook may have been stepping; the
        # final value is always the commanded theta_max regardless).
        on_rotation_update(theta_max)
        self._set_status(final_status)
        print(
            f"{self.log_prefix} ScanRotate ({mode}) done {target_id}: "
            f"final rot={theta_max:.2f} deg -- {final_status}"
        )

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
        """Operator confirms which tag is in the gripper.

        Used to clear the ``requires_operator_confirm`` flag after a
        boot-time gripper-closed reconciliation (see
        ``new_primitives.md`` §6.3). Pure state-only -- no hook,
        because no hardware step is involved (it only records what the
        operator says).
        """
        print(f"{self.log_prefix} ConfirmHoldingTag {tag_id}")
        with self._state_lock:
            if not is_holding(self.current_state):
                print(
                    f"{self.log_prefix} confirm_holding_tag: system not "
                    f"HOLDING; nothing to confirm."
                )
                return
            _confirm_holding_tag_helper(self.current_state, tag_id)
            self.current_state["last_updated"] = datetime.now().isoformat()
        self._persist_state()
        print(f"{self.log_prefix} Confirmed held tag: {tag_id}")

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

    def table_cam_connect(self, cam_id: int) -> Tuple[bool, str]:
        """Optional HTTP hook for lazy table-cam ownership (real cloudlabs build)."""
        return False, "table cam lifecycle is not available for this backend"

    def table_cam_disconnect(self, cam_id: int) -> Tuple[bool, str]:
        return False, "table cam lifecycle is not available for this backend"

    def table_cam_live_set(self, cam_id: int, enabled: bool) -> Tuple[bool, str]:
        return False, "table cam lifecycle is not available for this backend"

    def table_cam_send_vexp(self, cam_id: int, exposure_s: float) -> Tuple[bool, str]:
        return False, "table cam imaging controls are not available for this backend"

    def table_cam_send_vgain(self, cam_id: int, gain: float) -> Tuple[bool, str]:
        return False, "table cam imaging controls are not available for this backend"

    def get_table_cam_stream(self, cam_id: int = 1, fps: int = 18):
        """Multipart MJPEG generator for ``/api/table-cam/stream`` when implemented."""
        raise NotImplementedError

    def fetch_table_cam_preview_jpeg(self, cam_id: int = 1) -> Optional[bytes]:
        """Latest JPEG for ``/api/table-cam/preview`` when implemented."""
        return None

    def get_cobyla_reference_png_bytes(self) -> Optional[bytes]:
        """PNG of the stored cobyla reference, or ``None`` if unset."""
        return None

    def refresh_pose_from_camera(self, preserve_tag_ids: Optional[List[str]] = None) -> None:
        """Re-localize component poses from the camera. Default no-op."""
        return
