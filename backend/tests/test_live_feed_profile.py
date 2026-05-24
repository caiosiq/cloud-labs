"""Phase 7 — TeleOp live-feed preview profile selection."""
from __future__ import annotations

import os
import threading
import unittest
from pathlib import Path
from typing import Any, Dict

from lab_communicator.mock.communicator import MockLabCommunicator
from lab_communicator.shared.lab_view_config import (
    TableCamPreviewConfig,
    bootstrap_lab_view,
    load_table_cam_preview_config,
)
from lab_model.domain.component import new_component_entry
from lab_model.orchestration.live_feed import _resolve_live_feed_profile

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_MOCK_LAB_VIEW = _PROJECT_ROOT / "backend" / "lab_communicator" / "mock" / "lab_view"


class _FakeHost:
    def __init__(self, components: Dict[str, Any]) -> None:
        self._state_lock = threading.Lock()
        self.current_state = {"components": components}


class TestLiveFeedProfile(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ["LAB_VIEW_PATH"] = str(_MOCK_LAB_VIEW)
        bootstrap_lab_view(str(_PROJECT_ROOT))

    def _teleop_component(self, *, active: bool, ready: bool) -> Dict[str, Any]:
        entry = new_component_entry(
            "tag_22",
            "OPTICAL_CAMERA",
            presence="breadboard",
            nominal_pose={"x": 0.0, "y": 0.0, "rotation": 0.0},
            meas_pose={"x": 0.0, "y": 0.0, "rotation": 0.0},
        )
        top = entry["telemetry"]["teleop"]
        top["active"] = active
        top["ready"] = ready
        return entry

    def test_profile_teleop_when_session_active_and_ready(self) -> None:
        host = _FakeHost({"tag_22": self._teleop_component(active=True, ready=True)})
        self.assertEqual(_resolve_live_feed_profile(host, "tag_22"), "teleop")

    def test_profile_default_when_teleop_inactive(self) -> None:
        host = _FakeHost({"tag_22": self._teleop_component(active=False, ready=False)})
        self.assertEqual(_resolve_live_feed_profile(host, "tag_22"), "default")

    def test_profile_default_when_active_but_not_ready(self) -> None:
        host = _FakeHost({"tag_22": self._teleop_component(active=True, ready=False)})
        self.assertEqual(_resolve_live_feed_profile(host, "tag_22"), "default")

    def test_table_cam_preview_config_teleop_profile(self) -> None:
        cfg = TableCamPreviewConfig(
            teleop_scale=0.25,
            teleop_jpeg_quality=50,
            teleop_poll_fps=20,
            teleop_fetch_timeout_s=0.08,
            teleop_stream_drain_frames=12,
        )
        prof = cfg.profile("teleop")
        self.assertEqual(prof["scale"], 0.25)
        self.assertEqual(prof["jpeg_quality"], 50)
        self.assertEqual(prof["poll_fps"], 20)
        self.assertAlmostEqual(prof["fetch_timeout_s"], 0.08)
        self.assertEqual(prof["stream_drain_frames"], 12)

    def test_load_mock_table_cam_preview_json(self) -> None:
        cfg = load_table_cam_preview_config()
        prof = cfg.profile("teleop")
        self.assertEqual(prof["scale"], 0.25)
        self.assertEqual(prof["poll_fps"], 20)

    def test_mock_table_cam_live_set_stores_profile(self) -> None:
        lab = MockLabCommunicator()
        lab.table_cam_connect(1)
        ok, _ = lab.table_cam_live_set(1, True, profile="teleop")
        self.assertTrue(ok)
        self.assertEqual(lab._table_cam_stream_profile[1], "teleop")
        ok, _ = lab.table_cam_live_set(1, False)
        self.assertTrue(ok)
        self.assertEqual(lab._table_cam_stream_profile[1], "default")


if __name__ == "__main__":
    unittest.main()
