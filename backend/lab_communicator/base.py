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

    async def optimize_component(self, target_id: str, strategy: str, params: Dict[str, Any]):
        raise NotImplementedError

    async def remove_component(self, target_id: str):
        raise NotImplementedError

    async def add_component_to_state(self, component_data: Dict[str, Any]):
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
