"""Concrete template class for the lab communicator.

Phase 2A of the communicator refactor (see ``communicator_refactor.md``)
promoted this file from a thin abstract base to the concrete template
class that orchestrates every primitive. Subclasses (``RealLabCommunicator``,
``MockLabCommunicator``) provide the small ``_do_*`` hooks that distinguish
"talk to lab_automation" from "sleep + add noise"; everything else --
state ownership, refusal logic, status transitions, snapshot load
merging, motor-rotation injection, holding-field bookkeeping -- lives
here.

Architectural rules (enforced by the lints in
``backend/tests/test_lab_primitives.py``):

- ``shared/`` modules can be imported here; ``real/`` and ``mock/``
  cannot. Any cross-backend coupling lives in ``shared/``.
- Hook implementations in ``real/`` and ``mock/`` MUST NOT read or
  write ``self.current_state``. The orchestrator passes them what they
  need via arguments and (for long-running primitives) a
  ``progress_callback`` -- see ``communicator_refactor.md`` §6.2.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from lab_model.component_model import (
    get_measurables,
    get_tunables,
    is_on_table,
)
from lab_model.holding import (
    SYSTEM_STATUS_BUSY,
    SYSTEM_STATUS_HOLDING,
    SYSTEM_STATUS_IDLE,
    clear_holding,
    empty_holding,
    get_holding,
    set_holding,
)
from lab_model import motor_rotation_store as motor_rot

from lab_communicator.shared.catalog_lookup import motor_catalog_ok as _motor_catalog_ok_pure
from lab_communicator.shared.motor_state import inject_motor_rotations_into_state
from lab_communicator.shared.snapshot import (
    LabPose,
    merge_snapshot_components,
    normalize_loaded_state,
)
from lab_communicator.shared.state_machine import refuse_if_stored


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
    - **Primitive hooks** -- ``_do_<primitive>(...)`` for each
      primitive the orchestrator dispatches. Phase 2A migrates the
      motor primitives; later phases migrate the in-air, heavy-state,
      and optimization primitives.

    The class attribute :attr:`log_prefix` is interpolated into log
    lines so a reader can tell which backend produced a message
    without inspecting the call site.
    """

    #: Backend tag used in log lines. Subclasses override.
    log_prefix: str = "[LAB]"

    #: Absolute path to the component catalog JSON this backend loads from.
    #: Each concrete implementation is expected to set this in ``__init__``
    #: (mock and real lab use *different* catalog files -- see README).
    catalog_file: Optional[str] = None

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
        """Return the component catalog as a list of dicts.

        Always reads from disk so callers see on-disk edits without a
        backend restart. Backends that maintain an in-memory
        :attr:`catalog_map` mirror should keep it in sync separately.
        """
        path = self.catalog_file
        if not path or not os.path.exists(path):
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return []
        return data if isinstance(data, list) else []

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
        """Default: return saved measurables. Real may trigger a camera read."""
        return self.return_measurables_for_tag(tag_id)

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
        4. Delegate the hardware step to :meth:`_do_move_motor`.
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
            await self._do_move_motor(target_id, motor_id, float(distance))
            motor_rot.add_delta(target_id, motor_id, float(distance))
            print(f"{self.log_prefix} Motor moved.")
        except Exception as e:
            print(f"{self.log_prefix} Motor move failed: {e}")
        finally:
            self._set_status(SYSTEM_STATUS_IDLE)

    async def _do_move_motor(
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
        await self._do_motor_set_zero(target_id, motor_id)
        motor_rot.set_zero(target_id, motor_id)
        print(
            f"{self.log_prefix} Motor {motor_id} on {target_id}: "
            f"zero reference set (software)."
        )

    async def _do_motor_set_zero(self, target_id: str, motor_id: int) -> None:
        """Hardware step for :meth:`motor_set_zero` (default no-op).

        No real backend supports re-zeroing a motor encoder from
        software today, so both real and mock leave this as the
        default no-op. Future hardware that *does* support hardware-
        side re-zero (e.g. a motor controller with a writable
        reference register) overrides this hook.
        """
        return

    # ---------------------------------------------------------------
    # NotImplementedError stubs for primitives migrated in Phase 2B/C/D
    # (kept here so the public API surface is stable; subclasses still
    # provide the implementations until the relevant phase migrates them).
    # ---------------------------------------------------------------

    async def move_component(self, target_id: str, target_pose: Dict[str, float]):
        raise NotImplementedError

    async def optimize_component(
        self, target_id: str, strategy: str, params: Dict[str, Any]
    ):
        raise NotImplementedError

    async def remove_component(self, target_id: str):
        raise NotImplementedError

    async def add_component_to_state(self, component_data: Dict[str, Any]):
        raise NotImplementedError

    async def store_component(self, target_id: str):
        """Move a breadboard (PLACED) part into the storage quadrant with packed placement."""
        raise NotImplementedError

    async def place_from_storage(self, target_id: str, target_pose: Dict[str, Any]):
        """Place a STORED part onto the breadboard at the given lab pose (must not be in storage Q3)."""
        raise NotImplementedError

    async def affirm_placed_at_current(self, target_id: str):
        """Mark a STORED part as PLACED at its current pose (resolves layout when pose is outside Q3)."""
        raise NotImplementedError

    async def repack_storage_slot(self, target_id: str):
        """Move a STORED part to the next free inventory cell at cell center."""
        raise NotImplementedError

    async def recenter_stored_in_inventory(self, target_id: str):
        """Move a STORED part to the center of its assigned (or inferred) cell."""
        raise NotImplementedError

    async def pick_component(self, target_id: str, params: Dict[str, Any]):
        """Approach ``target_id``, close gripper, retract to safe Z."""
        raise NotImplementedError

    async def hover_component(
        self, target_id: str, target_pose: Dict[str, float]
    ):
        """Reposition an already-held part in mid-air."""
        raise NotImplementedError

    async def place_from_hover(
        self, target_id: str, target_pose: Dict[str, float]
    ):
        """Place a held part on the breadboard."""
        raise NotImplementedError

    async def scan_rotate_in_place(
        self, target_id: str, params: Dict[str, Any]
    ):
        """Sweep rotation across [theta_min, theta_max] (held or placed)."""
        raise NotImplementedError

    async def confirm_holding_tag(self, tag_id: str):
        """Operator confirms which tag is in the gripper."""
        raise NotImplementedError

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

    def get_cobyla_reference_png_bytes(self) -> Optional[bytes]:
        """PNG of the stored cobyla reference, or ``None`` if unset."""
        return None

    def refresh_pose_from_camera(self) -> None:
        """Re-localize component poses from the camera. Default no-op."""
        return
