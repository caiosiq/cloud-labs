"""Tests for MeasurableTensor materialization (Phase D)."""
from __future__ import annotations

import os
import tempfile
import unittest

from lab_model.measurables.materialize import materialize_measurable, read_measurable_from_state
from lab_model.measurables.resolve_data import resolve_tensor_with_state_path
from lab_model.measurables.tensor import LazyRef, MeasurableTensor


class MeasurableTensorTests(unittest.TestCase):
    def test_materialize_pose(self) -> None:
        tensor = materialize_measurable(
            "tag_20",
            "pose",
            {"x": 10.0, "y": -5.0, "rotation": 45.0},
            backend_id="mock.default",
        )
        self.assertEqual(tensor.tag_id, "tag_20")
        self.assertEqual(tensor.field, "pose")
        self.assertEqual(tensor.domain, "spatial")
        self.assertEqual(tensor.shape, (3,))
        self.assertEqual(tensor.data, [10.0, -5.0, 45.0])

    def test_materialize_scalar(self) -> None:
        tensor = materialize_measurable(
            "tag_19",
            "output_power_readback_mw",
            3.14,
            backend_id="mock.default",
        )
        self.assertEqual(tensor.domain, "scalar")
        self.assertEqual(tensor.shape, ())
        self.assertAlmostEqual(tensor.data, 3.14)

    def test_materialize_camera_image_lazy(self) -> None:
        tensor = materialize_measurable(
            "tag_22",
            "camera_image",
            {"path": "/tmp/frame.png", "format": "png", "source": "mock"},
            backend_id="mock.default",
            fetch_url="/api/components/tag_22/camera-image",
        )
        self.assertIsInstance(tensor.data, LazyRef)
        self.assertEqual(tensor.data.href, "/api/components/tag_22/camera-image")
        self.assertEqual(tensor.dtype, "uint8")
        self.assertEqual(tensor.domain, "spatial")

    def test_to_api_dict_roundtrip(self) -> None:
        original = materialize_measurable("tag_20", "pose", {"x": 1.0, "y": 2.0, "rotation": 0.0})
        payload = original.to_api_dict()
        restored = MeasurableTensor.from_api_dict(payload)
        self.assertEqual(restored.tag_id, original.tag_id)
        self.assertEqual(restored.data, original.data)

    def test_read_measurable_from_state(self) -> None:
        state = {
            "components": {
                "tag_20": {
                    "statecontrol": {
                        "measurables": {"pose": {"x": 1.0, "y": 2.0, "rotation": 0.0}},
                        "tunables": {},
                    },
                    "telemetry": {
                        "teleop": {"active": False},
                        "live_feed": {"stream": {"connected": False}},
                    },
                }
            }
        }
        val = read_measurable_from_state(state, "tag_20", "pose")
        self.assertEqual(val, {"x": 1.0, "y": 2.0, "rotation": 0.0})

    def test_resolve_image_from_file(self) -> None:
        try:
            from PIL import Image
            import numpy as np
        except ImportError:
            self.skipTest("Pillow/numpy not available")

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "test.png")
            Image.new("RGB", (4, 3), color=(1, 2, 3)).save(path)
            tensor = materialize_measurable(
                "tag_22",
                "camera_image",
                {"path": path, "format": "png"},
            )
            resolved = resolve_tensor_with_state_path(tensor, filesystem_path=path)
            self.assertEqual(resolved.shape, (3, 4, 3))
            self.assertTrue(hasattr(resolved.data, "shape"))


if __name__ == "__main__":
    unittest.main()
