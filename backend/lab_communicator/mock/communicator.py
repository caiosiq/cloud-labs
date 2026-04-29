import os
import json
import asyncio
import random
from datetime import datetime
from io import BytesIO
from typing import Any, Dict, Optional, Tuple

from lab_model import motor_rotation_store as motor_rot
from lab_model.component_model import (
    PLACEMENT_MODE_HOVER,
    PLACEMENT_MODE_MANUAL,
    PLACEMENT_MODE_PICK,
    PRESENCE_BREADBOARD,
    PRESENCE_STORAGE,
    default_measurables,
    default_tunables,
    is_stored,
    new_component_entry,
    set_presence_and_storage,
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
    random_placed_position,
)

from lab_communicator.base import LabCommunicator
from lab_communicator.shared.snapshot import LabPose

# Constants
# File now at ``backend/lab_communicator/mock/communicator.py``; the
# project ``schemas/`` directory is three levels up. (Was two levels
# up when the class lived in ``backend/lab_communicator/mock.py``.)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SCHEMAS_DIR = os.path.join(BASE_DIR, "..", "..", "..", "schemas")
LAB_STATE_FILE = os.path.abspath(os.path.join(SCHEMAS_DIR, "mock_lab_state.json"))
# Mock mode has its own, richer catalog distinct from the real lab's physical
# inventory (``component_catalog.real.json``). Keep them separate so mock
# demos can showcase parts the real table may not have yet.
CATALOG_FILE = os.path.abspath(os.path.join(SCHEMAS_DIR, "component_catalog.mock.json"))


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

        self.state_file = LAB_STATE_FILE
        self.catalog_file = CATALOG_FILE
        print(f"[MOCK LAB] Using state file: {self.state_file}")
        self._ensure_state()
        self._load_catalog()
        # Build the catalog dict-by-tag mirror that ``base.py`` uses for
        # O(1) catalog lookups. Mock historically scanned the list at
        # every call site; Phase 2A unified both backends on the dict
        # shape (the list ``self.catalog`` is preserved for legacy
        # call sites that iterate it).
        self.catalog_map = {
            item["tag_id"]: item
            for item in (self.catalog or [])
            if isinstance(item, dict) and item.get("tag_id")
        }
        # Initial in-memory load from disk. Subsequent mutations go
        # through ``_persist_state`` (migrated primitives) or
        # ``_write_state`` (still-file-backed primitives, both of
        # which keep the in-memory copy in sync).
        from lab_communicator.mock.persistence import read_state
        self.current_state = read_state(self.state_file)

        self._cobyla_reference_bgr = None  # optional BGR ndarray for UI / parity with real
        # Dev flag: simulate boot-time gripper-closed reconciliation (see new_primitives.md #6.3).
        self._mock_gripper_closed_on_boot = (
            os.getenv("MOCK_GRIPPER_CLOSED_ON_BOOT", "").strip().lower()
            in ("1", "true", "yes", "on")
        )
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
        from lab_communicator.mock.persistence import ensure_state_file
        ensure_state_file(self.state_file)

    def _load_catalog(self):
        """Load the mock component catalog from disk into ``self.catalog``.

        Thin wrapper -- body in :func:`lab_communicator.mock.persistence.load_catalog`.
        """
        from lab_communicator.mock.persistence import load_catalog
        self.catalog = load_catalog(self.catalog_file)

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
        from lab_communicator.mock.persistence import read_state
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
        from lab_communicator.mock.persistence import write_state
        write_state(self.state_file, state)
        with self._state_lock:
            self.current_state = state

    def _persist_state(self) -> None:
        """Write ``self.current_state`` to the on-disk JSON file.

        Override of :meth:`LabCommunicator._persist_state`. Called by
        the base orchestrator after every state mutation in a migrated
        primitive (:meth:`_set_status`, :meth:`_set_holding`,
        :meth:`_clear_holding`, :meth:`set_lab_state`). Real keeps the
        default no-op.
        """
        from lab_communicator.mock.persistence import write_state
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

    def refresh_pose_from_camera(self):
        """
        Simulate an overhead-camera pose pass: update **measurables.pose** toward
        **tunables.nominal_pose** with small localization noise (mock only).
        """
        state = self._read_state()
        comps = state.get("components") or {}
        if not isinstance(comps, dict):
            return
        state["system_status"] = "BUSY"
        self._write_state(state)

        state = self._read_state()
        comps = state.get("components") or {}
        for _tag_id, comp in comps.items():
            if not isinstance(comp, dict):
                continue
            tun = comp.get("tunables") or {}
            pres = tun.get("presence")
            if pres not in (PRESENCE_BREADBOARD, PRESENCE_STORAGE):
                continue
            np = tun.get("nominal_pose") or {}
            meas = comp.setdefault("measurables", default_measurables())
            pose = meas.setdefault("pose", {})
            nx = float(np.get("x", pose.get("x", 0.0)))
            ny = float(np.get("y", pose.get("y", 0.0)))
            nr = float(np.get("rotation", pose.get("rotation", 0.0)))
            pose["x"] = nx + random.uniform(-0.8, 0.8)
            pose["y"] = ny + random.uniform(-0.8, 0.8)
            pose["rotation"] = nr + random.uniform(-0.35, 0.35)

        state["system_status"] = "IDLE"
        state["last_updated"] = datetime.now().isoformat()
        self._write_state(state)
        print("[MOCK LAB] refresh_pose_from_camera: updated measurables.pose (simulated camera)")

    def refresh_state(self):
        """Deprecated name; use :meth:`refresh_pose_from_camera`."""
        self.refresh_pose_from_camera()

    # ``set_lab_state`` lives on the base template class (Phase 2A).
    # Mock has no robot, so :meth:`_apply_loaded_pose_to_hardware`
    # inherits the no-op default. ``_post_apply_snapshot`` also inherits
    # the no-op default (mock has no separate stored-intent file).

    async def move_component(self, target_id: str, target_pose: Dict[str, float]):
        print(f"[MOCK LAB] Moving {target_id}...")
        state = self._read_state()
        if "components" not in state or target_id not in state["components"]:
            print(f"[MOCK LAB] Error: Cannot move component {target_id} - Not found in Lab State.")
            return

        comp = state["components"][target_id]
        if is_stored(comp):
            print(f"[MOCK LAB] Refusing move: {target_id} is STORED (use Place from storage).")
            return

        tx = float(target_pose.get("target_x", target_pose.get("x", 0)))
        ty = float(target_pose.get("target_y", target_pose.get("y", 0)))
        trot = float(target_pose.get("rotation", 0))

        if comp.get("tunables", {}).get("presence") == PRESENCE_BREADBOARD and is_storage_region(tx, ty):
            print(f"[MOCK LAB] Refusing move: target ({tx},{ty}) is in storage quadrant (use Store).")
            return

        state["system_status"] = "BUSY"
        self._write_state(state)

        await asyncio.sleep(2)

        state = self._read_state()
        comp = state["components"][target_id]

        noise_x = random.uniform(-0.5, 0.5)
        noise_y = random.uniform(-0.5, 0.5)

        tun = comp.setdefault("tunables", default_tunables())
        meas = comp.setdefault("measurables", default_measurables())

        set_presence_and_storage(comp, PRESENCE_BREADBOARD, in_storage=False, slot=None)
        tun["nominal_pose"] = {"x": tx, "y": ty, "rotation": trot}
        tun["placement"] = {"mode": "MANUAL"}
        meas["pose"] = {
            "x": tx + noise_x,
            "y": ty + noise_y,
            "rotation": trot,
        }

        state["last_updated"] = datetime.now().isoformat()
        state["system_status"] = "IDLE"
        self._write_state(state)
        print(f"[MOCK LAB] Moved {target_id} to ({meas['pose']['x']:.2f}, {meas['pose']['y']:.2f})")

    async def _do_move_motor(
        self, target_id: str, motor_id: int, distance: float
    ) -> None:
        """Mock hardware step for :meth:`LabCommunicator.move_motor`.

        Refusal logic, catalog gate, BUSY/IDLE flip, and the
        ``motor_rotation_store`` bookkeeping are all owned by the base
        orchestrator. Mock just simulates the hardware delay so the UI
        can observe the BUSY transition.
        """
        await asyncio.sleep(1)

    async def optimize_component(self, target_id: str, strategy: str, params: Dict[str, Any]):
        print(f"[MOCK LAB] Optimizing {target_id} with {strategy}...")
        state = self._read_state()
        comp = (state.get("components") or {}).get(target_id)
        if comp and is_stored(comp):
            print(f"[MOCK LAB] Refusing optimize: {target_id} is STORED.")
            return

        state = self._read_state()
        state["system_status"] = "OPTIMIZING"
        self._write_state(state)

        await asyncio.sleep(3)

        state = self._read_state()
        if "components" in state and target_id in state["components"]:
            comp = state["components"][target_id]
            meas = comp.setdefault("measurables", default_measurables())
            pose = meas.setdefault("pose", {})
            tun = comp.setdefault("tunables", default_tunables())

            optimized_rotation = pose.get("rotation", 0) + random.uniform(-1, 1)
            pose["rotation"] = optimized_rotation
            meas["last_optimization_score"] = 0.99
            meas["last_optimized_pose"] = {k: pose[k] for k in ("x", "y", "rotation") if k in pose}
            tun["placement"] = {"mode": strategy.upper()}

        state["system_status"] = "IDLE"
        state["last_updated"] = datetime.now().isoformat()
        self._write_state(state)
        print(f"[MOCK LAB] Optimization complete.")

    async def remove_component(self, target_id: str):
        print(f"[MOCK LAB] Removing {target_id}...")
        state = self._read_state()
        if "components" in state and target_id in state["components"]:
            del state["components"][target_id]
            state["last_updated"] = datetime.now().isoformat()
            self._write_state(state)
        await asyncio.sleep(1)

    async def store_component(self, target_id: str):
        print(f"[MOCK LAB] Storing {target_id}...")
        state = self._read_state()
        comp = (state.get("components") or {}).get(target_id)
        if not comp:
            print(f"[MOCK LAB] store: {target_id} not in state")
            return
        if comp.get("tunables", {}).get("presence") != PRESENCE_BREADBOARD:
            print(f"[MOCK LAB] store: {target_id} must be on breadboard (got {comp.get('tunables', {}).get('presence')})")
            return
        w, h = self._get_component_wh(target_id)
        slot = find_storage_slot_and_center(
            state.get("components") or {},
            target_id,
            w,
            h,
            lambda tid: self._get_component_wh(tid),
        )
        if not slot:
            print("[MOCK LAB] No free storage slot in Q3.")
            return
        x, y, si, sj = slot
        rot = STORAGE_NOMINAL_ROTATION_DEG
        state["system_status"] = "BUSY"
        self._write_state(state)
        await asyncio.sleep(1.5)
        state = self._read_state()
        comp = state["components"][target_id]
        tun = comp.setdefault("tunables", default_tunables())
        meas = comp.setdefault("measurables", default_measurables())
        set_presence_and_storage(comp, PRESENCE_STORAGE, in_storage=True, slot={"i": si, "j": sj})
        tun["nominal_pose"] = {"x": x, "y": y, "rotation": rot}
        tun["placement"] = {"mode": "STORAGE"}
        meas["pose"] = {"x": x, "y": y, "rotation": rot}
        state["system_status"] = "IDLE"
        state["last_updated"] = datetime.now().isoformat()
        self._write_state(state)
        print(f"[MOCK LAB] Stored {target_id} at ({x:.1f}, {y:.1f}) slot=({si},{sj})")

    async def place_from_storage(self, target_id: str, target_pose: Dict[str, float]):
        print(f"[MOCK LAB] Place from storage {target_id}...")
        state = self._read_state()
        comp = (state.get("components") or {}).get(target_id)
        if not comp or not is_stored(comp):
            print(f"[MOCK LAB] place_from_storage: {target_id} not STORED")
            return
        tx = float(target_pose.get("target_x", target_pose.get("x", 0)))
        ty = float(target_pose.get("target_y", target_pose.get("y", 0)))
        trot = float(target_pose.get("rotation", 0))
        if not is_placed_region(tx, ty):
            print("[MOCK LAB] Target must be outside storage quadrant (x<0 and y<0).")
            return
        state["system_status"] = "BUSY"
        self._write_state(state)
        await asyncio.sleep(2)
        state = self._read_state()
        comp = state["components"][target_id]
        noise_x = random.uniform(-0.5, 0.5)
        noise_y = random.uniform(-0.5, 0.5)
        tun = comp.setdefault("tunables", default_tunables())
        meas = comp.setdefault("measurables", default_measurables())
        set_presence_and_storage(comp, PRESENCE_BREADBOARD, in_storage=False, slot=None)
        tun["nominal_pose"] = {"x": tx, "y": ty, "rotation": trot}
        tun["placement"] = {"mode": "MANUAL"}
        meas["pose"] = {"x": tx + noise_x, "y": ty + noise_y, "rotation": trot}
        state["system_status"] = "IDLE"
        state["last_updated"] = datetime.now().isoformat()
        self._write_state(state)
        print(f"[MOCK LAB] Placed from storage {target_id}")

    async def add_component_to_state(self, component_data: Dict[str, Any]):
        print(f"[MOCK LAB] Adding component: {component_data}")
        state = self._read_state()

        comp_type = component_data.get("type", "OPTICAL_MIRROR")
        tag_id = component_data.get("tag_id")

        if not tag_id:
            print("[MOCK LAB] Error: No tag_id provided for add_component")
            return

        if "components" not in state:
            state["components"] = {}

        if tag_id in state["components"]:
            print(f"[MOCK LAB] Component {tag_id} already exists. Skipping placement.")
            return

        placement_mode = (component_data.get("placement_mode") or "breadboard").lower()
        w, h = self._get_component_wh(tag_id)

        if placement_mode == "storage":
            slot = find_storage_slot_and_center(
                state.get("components") or {},
                tag_id,
                w,
                h,
                lambda tid: self._get_component_wh(tid),
            )
            if not slot:
                print("[MOCK LAB] FAILED to find free storage slot.")
                return
            x, y, si, sj = slot
            state["components"][tag_id] = new_component_entry(
                tag_id,
                comp_type,
                presence=PRESENCE_STORAGE,
                nominal_pose={"x": x, "y": y, "rotation": 0.0},
                meas_pose={"x": x, "y": y, "rotation": 0.0},
                placement_mode="STORAGE",
                in_storage=True,
                slot={"i": si, "j": sj},
            )
        else:
            pos = random_placed_position(
                state.get("components") or {},
                tag_id,
                w,
                h,
                lambda tid: self._get_component_wh(tid),
            )
            if not pos:
                print("[MOCK LAB] FAILED to find free pose for component.")
                return
            x, y = pos
            state["components"][tag_id] = new_component_entry(
                tag_id,
                comp_type,
                presence=PRESENCE_BREADBOARD,
                nominal_pose={"x": x, "y": y, "rotation": 0.0},
                meas_pose={"x": x, "y": y, "rotation": 0.0},
                placement_mode="MANUAL",
                in_storage=False,
                slot=None,
            )

        state["last_updated"] = datetime.now().isoformat()
        self._write_state(state)
        pres = state["components"][tag_id]["tunables"]["presence"]
        print(f"[MOCK LAB] Added {tag_id} presence={pres}")

    async def affirm_placed_at_current(self, target_id: str):
        print(f"[MOCK LAB] affirm_placed_at_current {target_id}...")
        state = self._read_state()
        comp = (state.get("components") or {}).get(target_id)
        if not comp or not is_stored(comp):
            print(f"[MOCK LAB] affirm: {target_id} must be STORED")
            return
        state["system_status"] = "BUSY"
        self._write_state(state)
        await asyncio.sleep(0.3)
        state = self._read_state()
        comp = state["components"][target_id]
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
        state["system_status"] = "IDLE"
        state["last_updated"] = datetime.now().isoformat()
        self._write_state(state)
        print(f"[MOCK LAB] {target_id} marked PLACED at current pose")

    async def repack_storage_slot(self, target_id: str):
        print(f"[MOCK LAB] repack_storage_slot {target_id}...")
        state = self._read_state()
        comp = (state.get("components") or {}).get(target_id)
        if not comp or not is_stored(comp):
            print(f"[MOCK LAB] repack: {target_id} must be STORED")
            return
        w, h = self._get_component_wh(target_id)
        slot = find_storage_slot_and_center(
            state.get("components") or {},
            target_id,
            w,
            h,
            lambda tid: self._get_component_wh(tid),
        )
        if not slot:
            print("[MOCK LAB] repack: no free storage slot")
            return
        x, y, si, sj = slot
        rot = STORAGE_NOMINAL_ROTATION_DEG
        state["system_status"] = "BUSY"
        self._write_state(state)
        await asyncio.sleep(1.0)
        state = self._read_state()
        comp = state["components"][target_id]
        tun = comp.setdefault("tunables", default_tunables())
        meas = comp.setdefault("measurables", default_measurables())
        set_presence_and_storage(comp, PRESENCE_STORAGE, in_storage=True, slot={"i": si, "j": sj})
        tun["nominal_pose"] = {"x": x, "y": y, "rotation": rot}
        tun["placement"] = {"mode": "STORAGE"}
        meas["pose"] = {"x": x, "y": y, "rotation": rot}
        state["system_status"] = "IDLE"
        state["last_updated"] = datetime.now().isoformat()
        self._write_state(state)
        print(f"[MOCK LAB] Repacked {target_id} to ({x:.1f},{y:.1f}) slot=({si},{sj})")

    async def recenter_stored_in_inventory(self, target_id: str):
        print(f"[MOCK LAB] recenter_stored_in_inventory {target_id}...")
        state = self._read_state()
        comp = (state.get("components") or {}).get(target_id)
        if not comp or not is_stored(comp):
            print(f"[MOCK LAB] recenter: {target_id} must be STORED")
            return
        nom = nominal_center_pose_for_stored_entry(comp)
        if nom is None:
            print("[MOCK LAB] recenter: could not resolve storage cell (need slot metadata or pose in Q3).")
            return
        x, y, si, sj = nom
        rot = STORAGE_NOMINAL_ROTATION_DEG
        state["system_status"] = "BUSY"
        self._write_state(state)
        await asyncio.sleep(1.0)
        state = self._read_state()
        comp = state["components"][target_id]
        tun = comp.setdefault("tunables", default_tunables())
        meas = comp.setdefault("measurables", default_measurables())
        set_presence_and_storage(comp, PRESENCE_STORAGE, in_storage=True, slot={"i": si, "j": sj})
        tun["nominal_pose"] = {"x": x, "y": y, "rotation": rot}
        tun["placement"] = {"mode": "STORAGE"}
        meas["pose"] = {"x": x, "y": y, "rotation": rot}
        state["system_status"] = "IDLE"
        state["last_updated"] = datetime.now().isoformat()
        self._write_state(state)
        print(f"[MOCK LAB] Recentered {target_id} at ({x:.1f},{y:.1f}) slot=({si},{sj}) rot=0 deg")

    # --- In-air manipulation (see ``new_primitives.md``) ---

    async def pick_component(self, target_id: str, params: Dict[str, Any]):
        print(f"[MOCK LAB] Pick {target_id}...")
        state = self._read_state()
        if is_holding(state):
            print(
                f"[MOCK LAB] Refusing pick: already HOLDING (held={held_tag(state)}). "
                "PLACE_FROM_HOVER first."
            )
            return
        comp = (state.get("components") or {}).get(target_id)
        if not comp:
            print(f"[MOCK LAB] pick: {target_id} not in state")
            return
        if is_stored(comp):
            print(
                f"[MOCK LAB] Refusing pick: {target_id} is STORED. "
                "Use PLACE_FROM_STORAGE or recenter first."
            )
            return
        meas = comp.setdefault("measurables", default_measurables())
        pose = dict(meas.get("pose") or {})
        px = float(pose.get("x", 0.0))
        py = float(pose.get("y", 0.0))
        prot = float(pose.get("rotation", 0.0))

        state["system_status"] = SYSTEM_STATUS_BUSY
        self._write_state(state)

        await asyncio.sleep(1.2)

        state = self._read_state()
        comp = state["components"][target_id]
        tun = comp.setdefault("tunables", default_tunables())
        # Part is now in the gripper -- presence conceptually "in-air"; we keep
        # it as BREADBOARD (not STORAGE) so sidebar/context rules treat it as
        # a normal on-table part being manipulated. The top-level HOLDING
        # field is the authoritative source-of-truth for "in gripper".
        tun["nominal_pose"] = {"x": px, "y": py, "rotation": prot, "z": DEFAULT_HOVER_Z_MM}
        tun["placement"] = {"mode": PLACEMENT_MODE_PICK}

        set_holding(
            state,
            tag_id=target_id,
            x=px,
            y=py,
            rotation=prot,
            z=DEFAULT_HOVER_Z_MM,
        )
        state["last_updated"] = datetime.now().isoformat()
        self._write_state(state)
        print(
            f"[MOCK LAB] Picked {target_id} at ({px:.1f},{py:.1f},rot={prot:.1f}) "
            f"-> HOLDING @ z={DEFAULT_HOVER_Z_MM:.1f}mm"
        )

    async def hover_component(self, target_id: str, target_pose: Dict[str, float]):
        print(f"[MOCK LAB] Hover {target_id} -> {target_pose}")
        state = self._read_state()
        if not is_holding(state):
            print("[MOCK LAB] Refusing hover: not HOLDING. PICK_COMPONENT first.")
            return
        held = held_tag(state)
        if held and held != target_id:
            print(
                f"[MOCK LAB] Refusing hover: currently holding {held}, "
                f"cannot hover {target_id}."
            )
            return

        tx = float(target_pose.get("target_x", target_pose.get("x", 0.0)))
        ty = float(target_pose.get("target_y", target_pose.get("y", 0.0)))
        trot = float(target_pose.get("rotation", 0.0))
        tz = float(target_pose.get("z", DEFAULT_HOVER_Z_MM))

        state["system_status"] = SYSTEM_STATUS_BUSY
        self._write_state(state)

        await asyncio.sleep(1.2)

        state = self._read_state()
        comp = (state.get("components") or {}).get(target_id)
        if isinstance(comp, dict):
            tun = comp.setdefault("tunables", default_tunables())
            meas = comp.setdefault("measurables", default_measurables())
            tun["nominal_pose"] = {"x": tx, "y": ty, "rotation": trot, "z": tz}
            tun["placement"] = {"mode": PLACEMENT_MODE_HOVER}
            noise_x = random.uniform(-0.3, 0.3)
            noise_y = random.uniform(-0.3, 0.3)
            meas["pose"] = {
                "x": tx + noise_x,
                "y": ty + noise_y,
                "rotation": trot,
                "z": tz,
            }

        set_holding(state, tag_id=target_id, x=tx, y=ty, rotation=trot, z=tz)
        state["last_updated"] = datetime.now().isoformat()
        self._write_state(state)
        print(
            f"[MOCK LAB] Hovered {target_id} -> ({tx:.1f},{ty:.1f},rot={trot:.1f},z={tz:.1f})"
        )

    async def place_from_hover(self, target_id: str, target_pose: Dict[str, float]):
        print(f"[MOCK LAB] PlaceFromHover {target_id} -> {target_pose}")
        state = self._read_state()
        if not is_holding(state):
            print("[MOCK LAB] Refusing place_from_hover: not HOLDING.")
            return
        held = held_tag(state)
        if held and held != target_id:
            print(
                f"[MOCK LAB] Refusing place_from_hover: currently holding {held}, "
                f"cannot place {target_id}."
            )
            return
        tx = float(target_pose.get("target_x", target_pose.get("x", 0.0)))
        ty = float(target_pose.get("target_y", target_pose.get("y", 0.0)))
        trot = float(target_pose.get("rotation", 0.0))
        if is_storage_region(tx, ty):
            print(
                f"[MOCK LAB] Refusing place_from_hover: target ({tx},{ty}) is in "
                "storage quadrant (use STORE_COMPONENT instead)."
            )
            return

        state["system_status"] = SYSTEM_STATUS_BUSY
        self._write_state(state)

        await asyncio.sleep(1.5)

        state = self._read_state()
        comp = state["components"].get(target_id)
        if isinstance(comp, dict):
            noise_x = random.uniform(-0.5, 0.5)
            noise_y = random.uniform(-0.5, 0.5)
            tun = comp.setdefault("tunables", default_tunables())
            meas = comp.setdefault("measurables", default_measurables())
            set_presence_and_storage(comp, PRESENCE_BREADBOARD, in_storage=False, slot=None)
            # z is no longer meaningful once placed -- strip it from the nominal pose.
            tun["nominal_pose"] = {"x": tx, "y": ty, "rotation": trot}
            tun["placement"] = {"mode": PLACEMENT_MODE_MANUAL}
            meas["pose"] = {
                "x": tx + noise_x,
                "y": ty + noise_y,
                "rotation": trot,
            }

        clear_holding(state)
        state["last_updated"] = datetime.now().isoformat()
        self._write_state(state)
        print(f"[MOCK LAB] Placed {target_id} from hover at ({tx:.1f},{ty:.1f},rot={trot:.1f})")

    async def scan_rotate_in_place(self, target_id: str, params: Dict[str, Any]):
        """
        Constant-rate theta sweep. Two dispatch branches share this primitive:

        - **Held** (``system_status == HOLDING`` and held tag matches): sweep
          the in-air part's rotation while XY + Z stay locked at the current
          hover pose. System remains ``HOLDING`` on completion.
        - **Placed** (``system_status == IDLE`` and target on breadboard):
          rotate the placed part in situ on the table (XY locked at the
          measured pose). System returns to ``IDLE`` on completion.

        See ``new_primitives.md`` §7 and ``labautomation_new_primitives.md``
        §2.4 for the corresponding real-lab dispatch.
        """
        theta_min = float(params.get("theta_min", 0.0))
        theta_max = float(params.get("theta_max", 0.0))
        speed = float(params.get("speed_deg_per_s", 0.0))
        axis = str(params.get("axis", "z"))
        if speed <= 0.0:
            print("[MOCK LAB] scan_rotate: speed_deg_per_s must be > 0")
            return

        state = self._read_state()
        holding_now = is_holding(state)
        held = held_tag(state) if holding_now else None

        # Decide dispatch branch.
        if holding_now:
            if held and held != target_id:
                print(
                    f"[MOCK LAB] Refusing scan_rotate: currently holding {held}, "
                    f"cannot rotate {target_id}."
                )
                return
            mode = "held"
            holding = get_holding(state)
            base_pose = dict(holding.get("nominal_pose") or {})
            base_x = float(base_pose.get("x", 0.0))
            base_y = float(base_pose.get("y", 0.0))
            base_z: Optional[float] = float(base_pose.get("z", DEFAULT_HOVER_Z_MM))
        else:
            status = state.get("system_status") or SYSTEM_STATUS_IDLE
            if status != SYSTEM_STATUS_IDLE:
                print(
                    f"[MOCK LAB] Refusing scan_rotate: system_status={status}, "
                    "need IDLE or HOLDING."
                )
                return
            comp = (state.get("components") or {}).get(target_id)
            if not isinstance(comp, dict):
                print(f"[MOCK LAB] scan_rotate: {target_id} not in state.")
                return
            presence = (comp.get("tunables") or {}).get("presence")
            if presence != PRESENCE_BREADBOARD:
                print(
                    f"[MOCK LAB] Refusing scan_rotate: {target_id} presence={presence} "
                    "(need on breadboard for placed-mode scan)."
                )
                return
            mode = "placed"
            cur_pose = dict((comp.get("measurables") or {}).get("pose") or {})
            base_x = float(cur_pose.get("x", 0.0))
            base_y = float(cur_pose.get("y", 0.0))
            base_z = None  # Placed parts: z is implicit / not stored on nominal_pose.

        print(
            f"[MOCK LAB] ScanRotate mode={mode} {target_id}: {theta_min} deg->{theta_max} deg "
            f"@ {speed} deg/s axis={axis}"
        )

        total_deg = abs(theta_max - theta_min)
        duration_s = total_deg / speed if speed > 0 else 0.0
        duration_s = min(duration_s, 10.0)  # cap mock sleep for UI responsiveness
        steps = max(1, min(20, int(duration_s * 4)))
        step_sleep = duration_s / steps if steps > 0 else 0.0

        # Placed-mode: transition to BUSY during the sweep so the UI shows
        # motion progress, then back to IDLE. Held-mode: stay HOLDING.
        if mode == "placed":
            state = self._read_state()
            state["system_status"] = SYSTEM_STATUS_BUSY
            state["last_updated"] = datetime.now().isoformat()
            self._write_state(state)

        for i in range(1, steps + 1):
            frac = i / steps
            cur_rot = theta_min + (theta_max - theta_min) * frac
            state = self._read_state()
            comp = (state.get("components") or {}).get(target_id)
            if isinstance(comp, dict):
                tun = comp.setdefault("tunables", default_tunables())
                meas = comp.setdefault("measurables", default_measurables())
                if mode == "held":
                    tun["nominal_pose"] = {
                        "x": base_x, "y": base_y, "rotation": cur_rot, "z": base_z,
                    }
                    meas["pose"] = {
                        "x": base_x, "y": base_y, "rotation": cur_rot, "z": base_z,
                    }
                else:
                    # Placed: keep pose shape z-less (matches MOVE_COMPONENT
                    # / PLACE_FROM_HOVER semantics once the part is on-table).
                    tun["nominal_pose"] = {"x": base_x, "y": base_y, "rotation": cur_rot}
                    meas["pose"] = {"x": base_x, "y": base_y, "rotation": cur_rot}
            if mode == "held":
                set_holding(state, tag_id=target_id, x=base_x, y=base_y, rotation=cur_rot, z=base_z)
            state["last_updated"] = datetime.now().isoformat()
            self._write_state(state)
            await asyncio.sleep(step_sleep)

        if mode == "placed":
            state = self._read_state()
            state["system_status"] = SYSTEM_STATUS_IDLE
            state["last_updated"] = datetime.now().isoformat()
            self._write_state(state)
            print(
                f"[MOCK LAB] ScanRotate (placed) done {target_id}: swept {total_deg:.1f} deg in "
                f"{duration_s:.2f}s (final rot={theta_max:.1f} deg) -- back to IDLE"
            )
        else:
            print(
                f"[MOCK LAB] ScanRotate (held) done {target_id}: swept {total_deg:.1f} deg in "
                f"{duration_s:.2f}s (final rot={theta_max:.1f} deg) -- remaining HOLDING"
            )

    async def confirm_holding_tag(self, tag_id: str):
        print(f"[MOCK LAB] ConfirmHoldingTag {tag_id}")
        state = self._read_state()
        if not is_holding(state):
            print("[MOCK LAB] confirm_holding_tag: system not HOLDING; nothing to confirm.")
            return
        _confirm_holding_tag(state, tag_id)
        state["last_updated"] = datetime.now().isoformat()
        self._write_state(state)
        print(f"[MOCK LAB] Confirmed held tag: {tag_id}")

    def set_cobyla_reference_from_png_bytes(self, data: bytes) -> Tuple[bool, str]:
        """Same API as real lab; mock optimize does not use it, but UI can test the flow."""
        if not data or len(data) < 8:
            return False, "empty body"
        try:
            from PIL import Image
            import numpy as np
        except ImportError:
            return False, "Pillow and numpy required (pip install Pillow numpy)"
        try:
            pil = Image.open(BytesIO(data))
            pil.load()
            rgb = np.array(pil.convert("RGB"))
        except Exception as e:
            return False, f"could not decode PNG: {e}"
        if rgb.ndim != 3 or rgb.shape[2] != 3:
            return False, "decoded image must have 3 channels"
        bgr = rgb[:, :, ::-1].copy()
        self._cobyla_reference_bgr = bgr
        h, w = bgr.shape[:2]
        print(f"[MOCK LAB] Cobyla reference image set ({w}x{h} BGR)")
        return True, f"stored {w}x{h} BGR reference (mock)"

    def clear_cobyla_reference(self) -> None:
        self._cobyla_reference_bgr = None
        print("[MOCK LAB] Cobyla reference image cleared")

    def get_cobyla_reference_status(self) -> Dict[str, Any]:
        ref = self._cobyla_reference_bgr
        if ref is None:
            return {"available": True, "set": False}
        h, w = ref.shape[:2]
        return {
            "available": True,
            "set": True,
            "width": int(w),
            "height": int(h),
            "channels": int(ref.shape[2]),
        }

    def get_cobyla_reference_png_bytes(self) -> Optional[bytes]:
        ref = self._cobyla_reference_bgr
        if ref is None:
            return None
        try:
            from PIL import Image
            import numpy as np
        except ImportError:
            return None
        rgb = ref[:, :, ::-1]
        img = Image.fromarray(np.ascontiguousarray(rgb))
        buf = BytesIO()
        img.save(buf, format="PNG", compress_level=6)
        return buf.getvalue()

    def get_video_feed_status(self) -> Dict[str, Any]:
        return {"connected": True, "source": "/api/video-feed/stream"}

    async def observe_measurables_for_tag(self, tag_id: str) -> Dict[str, Any]:
        meta = self._catalog_meta_for_tag(tag_id)
        ctype = (meta or {}).get("type") or ""
        state = self._read_state()
        comp = (state.get("components") or {}).get(tag_id)
        if not isinstance(comp, dict):
            return {}
        if ctype != "OPTICAL_CAMERA":
            return self.return_measurables_for_tag(tag_id)
        png = self.capture_table_cam(1, exposure=0.2)
        meas = comp.setdefault("measurables", default_measurables())
        if png:
            cap_dir = os.path.abspath(os.path.join(SCHEMAS_DIR, "mock_camera_captures"))
            os.makedirs(cap_dir, exist_ok=True)
            rel_name = f"{tag_id}_last.png"
            out_path = os.path.join(cap_dir, rel_name)
            with open(out_path, "wb") as f:
                f.write(png)
            meas["camera_image"] = {
                "path": out_path,
                "source": "mock_table_cam",
                "cam_id": 1,
                "format": "png",
            }
        state["last_updated"] = datetime.now().isoformat()
        self._write_state(state)
        return self.return_measurables_for_tag(tag_id)

    def capture_table_cam(self, cam_id: int, exposure: float = 0.2) -> bytes:
        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError:
            print("[MOCK LAB] capture_table_cam: Pillow not installed")
            return None
        if cam_id not in (1, 2):
            return None

        w, h = 640, 480
        img = Image.new("RGB", (w, h), (18, 22, 30))
        draw = ImageDraw.Draw(img)
        grid = (36, 40, 52)
        for x in range(0, w, 40):
            draw.line([(x, 0), (x, h)], fill=grid, width=1)
        for y in range(0, h, 40):
            draw.line([(0, y), (w, y)], fill=grid, width=1)

        try:
            font = ImageFont.load_default()
        except Exception:
            font = None

        title = f"MOCK table cam {cam_id}"
        sub = f"exposure={exposure:g}s -- LAB_MODE=MOCK"
        hint = "Capture / Set Cobyla reference use this image for UI testing."
        if font:
            draw.text((24, 20), title, fill=(226, 232, 240), font=font)
            draw.text((24, 38), sub, fill=(148, 163, 184), font=font)
            draw.text((24, 58), hint, fill=(100, 116, 139), font=font)
        else:
            draw.text((24, 20), title, fill=(226, 232, 240))
            draw.text((24, 38), sub, fill=(148, 163, 184))

        cx = w // 2 + (cam_id - 1) * 55
        cy = h // 2 - 10
        r = 28
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(248, 113, 113), width=3)
        draw.line([(cx - 40, cy), (cx + 40, cy)], fill=(251, 191, 36), width=2)

        buf = BytesIO()
        img.save(buf, format="PNG", compress_level=6)
        return buf.getvalue()

    def get_video_stream(self):
        import time
        while True:
            yield b''
            break
