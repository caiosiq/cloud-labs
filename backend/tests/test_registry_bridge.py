"""Phase 3: cloud-labs ↔ lab_automation registry bridge (optional lab_automation)."""
from __future__ import annotations

import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from lab_communicator.real import video as real_video


class TestRegistryVideoBridge(unittest.TestCase):
    def test_capture_overhead_uses_experiment_capture_still_for_tag(self) -> None:
        exp = SimpleNamespace(
            capture_still_for_tag=MagicMock(return_value=b"\x89PNGfake"),
        )
        comm = SimpleNamespace(experiment=exp)
        png = real_video.capture_overhead_cam(comm, exposure=0.05)
        self.assertEqual(png, b"\x89PNGfake")
        exp.capture_still_for_tag.assert_called_once_with("tag_99", exposure_s=0.05)

    def test_registry_camera_for_recorder_cam(self) -> None:
        cam = SimpleNamespace(tag_id="tag_22", recorder_cam_id=1)
        exp = SimpleNamespace(
            find_tag_id_for_recorder_cam=MagicMock(return_value="tag_22"),
            get_camera_component=MagicMock(return_value=cam),
        )
        comm = SimpleNamespace(experiment=exp)
        got = real_video._registry_camera_for_recorder_cam(comm, 1)
        self.assertIs(got, cam)


if __name__ == "__main__":
    unittest.main()
