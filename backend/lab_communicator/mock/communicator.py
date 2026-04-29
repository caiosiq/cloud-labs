import json
import os
import random
from datetime import datetime
from io import BytesIO
from typing import Any, Callable, Dict, Optional, Tuple

from lab_model.component_model import (
    PRESENCE_BREADBOARD,
    PRESENCE_STORAGE,
    default_measurables,
)
from lab_model.holding import (
    DEFAULT_HOVER_Z_MM,
    SYSTEM_STATUS_BUSY,
    SYSTEM_STATUS_HOLDING,
    SYSTEM_STATUS_IDLE,
    empty_holding,
    get_holding,
    set_holding,
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

    async def _primitive_move_component(
        self, target_id: str, commanded: LabPose
    ) -> Optional[LabPose]:
        from lab_communicator.mock.primitives import primitive_move_component
        return await primitive_move_component(self, target_id, commanded)

    async def _primitive_move_motor(
        self, target_id: str, motor_id: int, distance: float
    ) -> None:
        from lab_communicator.mock.primitives import primitive_move_motor
        await primitive_move_motor(self, target_id, motor_id, distance)

    async def _primitive_optimize_component(
        self,
        *,
        target_id: str,
        strategy_name: str,
        params: Dict[str, Any],
        progress_callback: Callable[..., None],
    ) -> Optional[Dict[str, Any]]:
        from lab_communicator.mock.primitives import primitive_optimize_component
        return await primitive_optimize_component(
            self,
            target_id=target_id,
            strategy_name=strategy_name,
            params=params,
            progress_callback=progress_callback,
        )

    async def _primitive_add_component_to_state(
        self,
        component_data: Dict[str, Any],
        existing_components: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        from lab_communicator.mock.primitives import primitive_add_component_to_state
        return await primitive_add_component_to_state(
            self, component_data, existing_components
        )

    async def _primitive_pick_component(
        self, target_id: str, commanded: LabPose, params: Dict[str, Any]
    ) -> float:
        from lab_communicator.mock.primitives import primitive_pick_component
        return await primitive_pick_component(self, target_id, commanded, params)

    async def _primitive_hover_component(
        self, target_id: str, commanded: LabPose, speed: int
    ) -> Optional[LabPose]:
        from lab_communicator.mock.primitives import primitive_hover_component
        return await primitive_hover_component(self, target_id, commanded, speed)

    async def _primitive_place_from_hover(
        self, target_id: str, commanded: LabPose, params: Dict[str, Any]
    ) -> None:
        from lab_communicator.mock.primitives import primitive_place_from_hover
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
        from lab_communicator.mock.primitives import primitive_scan_rotate_in_place
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

    def _camera_captures_dir(self) -> str:
        """Directory mock writes synthetic capture PNGs into.

        Lives next to the mock state JSON so the directory layout
        matches the rest of the schemas folder. Used by
        :func:`lab_communicator.mock.primitives.primitive_observe_measurables`
        to land ``<tag>_last.png`` for the UI to pick up.
        """
        return os.path.abspath(os.path.join(SCHEMAS_DIR, "mock_camera_captures"))

    async def _primitive_observe_measurables(
        self, tag_id: str, catalog_meta: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        from lab_communicator.mock.primitives import primitive_observe_measurables
        return await primitive_observe_measurables(self, tag_id, catalog_meta)

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
