import json
import os
from typing import Any, Dict, List, Optional

from lab_model.component_model import get_measurables, get_tunables


class LabCommunicator:
    """
    Abstract Base Class for Lab Communication.
    """

    #: Absolute path to the component catalog JSON this backend loads from.
    #: Each concrete implementation is expected to set this in ``__init__``
    #: (mock and real lab use *different* catalog files -- see README).
    catalog_file: Optional[str] = None

    def get_catalog(self) -> List[Dict[str, Any]]:
        """
        Return the component catalog as a list of dicts (the JSON under
        :attr:`catalog_file`). Kept as a plain file read so callers always see
        on-disk edits without restarting the backend.
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

    def get_lab_state(self) -> Dict[str, Any]:
        raise NotImplementedError

    def return_tunables_for_tag(self, tag_id: str) -> Dict[str, Any]:
        """Saved tunables slice (no I/O). Prefer this name over legacy ``get_*``."""
        st = self.get_lab_state()
        comp = (st.get("components") or {}).get(tag_id)
        return get_tunables(comp) if isinstance(comp, dict) else {}

    def return_measurables_for_tag(self, tag_id: str) -> Dict[str, Any]:
        """Saved measurables slice (no I/O). Prefer this name over legacy ``get_*``."""
        st = self.get_lab_state()
        comp = (st.get("components") or {}).get(tag_id)
        return get_measurables(comp) if isinstance(comp, dict) else {}

    def get_tunables_for_tag(self, tag_id: str) -> Dict[str, Any]:
        """Slice of lab state: commanded intent for one component (see refactor.md)."""
        return self.return_tunables_for_tag(tag_id)

    def get_measurables_for_tag(self, tag_id: str) -> Dict[str, Any]:
        """Slice of lab state: lab-reported values for one component."""
        return self.return_measurables_for_tag(tag_id)

    async def observe_measurables_for_tag(self, tag_id: str) -> Dict[str, Any]:
        """
        Poll / refresh lab-reported values for one component (e.g. camera capture → ``measurables``).
        Default: return saved measurables only. Mock/real may update state before returning.
        """
        return self.return_measurables_for_tag(tag_id)

    async def move_component(self, target_id: str, target_pose: Dict[str, float]):
        raise NotImplementedError

    async def move_motor(self, target_id: str, motor_id: int, distance: float):
        raise NotImplementedError

    async def motor_send_home(self, target_id: str, motor_id: int):
        """Move motor by -tracked angle so cumulative angle becomes 0 (hardware move)."""
        raise NotImplementedError

    async def motor_set_zero(self, target_id: str, motor_id: int):
        """Set current physical position as angle 0 in software (no hardware move)."""
        raise NotImplementedError

    async def optimize_component(self, target_id: str, strategy: str, params: Dict[str, Any]):
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
        """Move a STORED part to the next free inventory cell (row-major packing) at cell center."""
        raise NotImplementedError

    async def recenter_stored_in_inventory(self, target_id: str):
        """Move a STORED part to the center of its assigned (or inferred) cell at standard storage rotation (0°)."""
        raise NotImplementedError

    # --- In-air manipulation (see ``new_primitives.md``) ---

    async def pick_component(self, target_id: str, params: Dict[str, Any]):
        """
        Approach ``target_id``, close gripper, retract to safe Z. Transitions
        ``system_status`` IDLE → BUSY → HOLDING and sets ``state["holding"]``.
        ``params`` reserved for future options (e.g. custom safe Z).
        """
        raise NotImplementedError

    async def hover_component(self, target_id: str, target_pose: Dict[str, float]):
        """
        Reposition an **already held** part in mid-air to
        ``(target_x, target_y, rotation, z)``. Does NOT grasp. Requires
        ``system_status == "HOLDING"`` and ``state["holding"].tag_id == target_id``.
        """
        raise NotImplementedError

    async def place_from_hover(self, target_id: str, target_pose: Dict[str, float]):
        """
        Place a held part on the breadboard at ``(target_x, target_y, rotation)``.
        HOLDING → BUSY → IDLE; clears ``state["holding"]``.
        """
        raise NotImplementedError

    async def scan_rotate_in_place(self, target_id: str, params: Dict[str, Any]):
        """
        Sweep the held part's rotation from ``theta_min`` to ``theta_max`` at
        ``speed_deg_per_s`` around ``axis`` (default ``"z"``). Used to scan for
        laser-on-camera alignment. Requires HOLDING; remains HOLDING on success.
        """
        raise NotImplementedError

    async def confirm_holding_tag(self, tag_id: str):
        """
        Operator confirms which tag is actually in the gripper (used to clear
        ``requires_operator_confirm`` after a startup gripper-closed reconciliation).
        """
        raise NotImplementedError

    def get_gripper_status(self) -> Dict[str, Any]:
        """
        Hardware poll: is the gripper physically closed on something?
        Expected shape::

            {"closed": bool, "confidence": float | None, "source": str}

        Default for lab backends without gripper introspection: ``closed=False``.
        Real lab overrides this; mock returns ``closed=True`` only when the
        ``MOCK_GRIPPER_CLOSED_ON_BOOT`` dev flag is set at startup.
        """
        return {"closed": False, "confidence": None, "source": "default"}

    def get_video_feed_status(self) -> Dict[str, Any]:
        return {"connected": True, "source": "/static/mock_feed.svg"}

    def get_video_stream(self):
        """Returns a generator yielding MJPEG frames."""
        raise NotImplementedError

    def capture_table_cam(self, cam_id: int, exposure: float = 0.2):
        """Capture one image from table recorder camera (cam_id 1 or 2). Returns PNG bytes or None if not supported. Mock lab returns a synthetic PNG for UI testing."""
        return None

    def get_cobyla_reference_png_bytes(self) -> Optional[bytes]:
        """PNG encoding of stored Cobyla reference for UI preview, or None if unset."""
        return None

    def refresh_pose_from_camera(self) -> None:
        """
        Re-localize component poses from the overhead / table camera pipeline and write
        **measurables.pose** (and related fields). Used by **POST /api/lab-state/refresh-pose**.
        Default: no-op.
        """
        return
