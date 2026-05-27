"""GET_STORAGE read primitive."""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from lab_communicator.mock.communicator import MockLabCommunicator
from lab_communicator.shared.lab_view_config import bootstrap_lab_view
from lab_model.domain.component import PRESENCE_STORAGE, tunables_bucket
from lab_model.primitives import PrimitiveId, fetch_read_primitive


def _make_mock_lab(tmp: Path) -> MockLabCommunicator:
    lv = tmp / "lab_view"
    src = Path(__file__).resolve().parents[1] / "lab_communicator" / "mock" / "lab_view"
    shutil.copytree(src, lv)
    os.environ["LAB_VIEW_PATH"] = str(lv)
    project_root = Path(__file__).resolve().parents[1]
    bootstrap_lab_view(str(project_root))
    from lab_model import motor_rotation_store as motor_rot

    motor_rot.configure(str(lv / "motor_rotations.json"))
    return MockLabCommunicator()


class TestGetStorage(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.lab = _make_mock_lab(Path(self._tmpdir.name))

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_returns_only_stored_tags(self) -> None:
        with self.lab._state_lock:
            comps = self.lab.current_state["components"]
            for tag_id, entry in comps.items():
                tun = tunables_bucket(entry)
                stored = tag_id == "tag_18"
                tun["presence"] = PRESENCE_STORAGE if stored else "breadboard"
                tun.setdefault("storage", {})["in_storage"] = stored

        body = fetch_read_primitive(self.lab, PrimitiveId.GET_STORAGE)
        self.assertEqual(body, {"tag_ids": ["tag_18"]})

    def test_empty_when_none_stored(self) -> None:
        with self.lab._state_lock:
            comps = self.lab.current_state["components"]
            for entry in comps.values():
                tun = tunables_bucket(entry)
                tun["presence"] = "breadboard"
                tun.setdefault("storage", {})["in_storage"] = False

        body = self.lab.return_stored_tag_ids()
        self.assertEqual(body["tag_ids"], [])
