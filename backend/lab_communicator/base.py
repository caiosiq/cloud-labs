import os
from typing import Any, Dict, Optional

class LabCommunicator:
    """
    Abstract Base Class for Lab Communication.
    """
    def get_lab_state(self) -> Dict[str, Any]:
        raise NotImplementedError

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
