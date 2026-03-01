import os
from typing import Dict, Any

class LabCommunicator:
    """
    Abstract Base Class for Lab Communication.
    """
    def get_lab_state(self) -> Dict[str, Any]:
        raise NotImplementedError

    async def move_component(self, target_id: str, target_pose: Dict[str, float]):
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
