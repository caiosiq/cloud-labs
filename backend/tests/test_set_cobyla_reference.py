"""SET_COBYLA_REFERENCE primitive — pin lab reference from measurables."""
from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from lab_communicator.mock.communicator import MockLabCommunicator
from lab_communicator.shared.lab_view_config import bootstrap_lab_view
from lab_model.domain.component import get_measurables, measurables_bucket
from lab_model.orchestration.cobyla_reference import (
    cobyla_reference_path,
    refuse_if_no_measurable_camera_image,
)
from lab_model.state.state_machine import PrimitiveRefusalError


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


class TestSetCobylaReference(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.lab = _make_mock_lab(Path(self._tmpdir.name))
        self.camera_tag = "tag_99"

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_refuse_when_no_camera_image(self) -> None:
        with self.lab._state_lock:
            self.lab.current_state.pop("optimization_reference", None)
            entry = self.lab.current_state["components"]["tag_22"]
            measurables_bucket(entry)["camera_image"] = None

        refusal = refuse_if_no_measurable_camera_image(
            self.lab.current_state, "tag_22"
        )
        self.assertTrue(refusal)

        async def run() -> None:
            with self.assertRaises(PrimitiveRefusalError):
                await self.lab.set_cobyla_reference("tag_22")

        asyncio.run(run())

    def test_pins_reference_from_measurable(self) -> None:
        png = Path(self._tmpdir.name) / "ref.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n")

        with self.lab._state_lock:
            self.lab.current_state.pop("optimization_reference", None)
            entry = self.lab.current_state["components"][self.camera_tag]
            measurables_bucket(entry)["camera_image"] = {
                "path": str(png),
                "source": "test_cam",
                "cam_id": 1,
                "format": "png",
            }

        async def run() -> None:
            payload = await self.lab.set_cobyla_reference(self.camera_tag)
            self.assertEqual(payload["sensor_component"], self.camera_tag)
            self.assertEqual(os.path.abspath(payload["path"]), str(png.resolve()))

        asyncio.run(run())

        with self.lab._state_lock:
            path = cobyla_reference_path(self.lab.current_state, self.camera_tag)
            self.assertEqual(path, str(png.resolve()))
