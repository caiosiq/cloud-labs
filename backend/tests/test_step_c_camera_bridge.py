"""Step C — real camera frame → BGR → TorchScript (no invented frames on real)."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from lab_model.measurables.capture import read_camera_bgr_for_tag
from lab_model.measurables.materialize import materialize_measurable
from lab_model.measurables.resolve_data import resolve_tensor_with_state_path


class ReadCameraBgrTests(unittest.TestCase):
    def test_read_camera_bgr_decodes_png(self) -> None:
        try:
            import cv2
            import numpy as np
        except ImportError:
            self.skipTest("opencv/numpy not installed")

        img = np.zeros((48, 64, 3), dtype=np.uint8)
        img[:, :] = (10, 40, 200)  # BGR
        ok, buf = cv2.imencode(".png", img)
        assert ok
        png = buf.tobytes()

        bridge = MagicMock()
        bridge.catalog_map = {
            "tag_22": {
                "hardware_binding": {"backend": "table_cam", "cam_id": 1},
            }
        }
        bridge._table_cam_connected = {1: True}
        bridge.capture_table_cam.return_value = png

        bgr = read_camera_bgr_for_tag(bridge, "tag_22", bridge.catalog_map["tag_22"])
        self.assertIsNotNone(bgr)
        assert bgr is not None
        self.assertEqual(bgr.shape, (48, 64, 3))
        self.assertEqual(int(bgr[0, 0, 0]), 10)
        self.assertEqual(int(bgr[0, 0, 2]), 200)

    def test_read_camera_bgr_none_when_capture_fails(self) -> None:
        bridge = MagicMock()
        bridge.catalog_map = {"tag_22": {}}
        bridge._table_cam_connected = {1: True}
        bridge.capture_table_cam.return_value = None
        self.assertIsNone(read_camera_bgr_for_tag(bridge, "tag_22", {}))


class TorchscriptFromCapturedFrameTests(unittest.TestCase):
    def test_demo_mean_score_tracks_brightness(self) -> None:
        try:
            import cv2
            import numpy as np
            from lab_model.optimization.kernels.torchscript_runtime import (
                run_torchscript_output,
            )
        except ImportError:
            self.skipTest("deps missing")

        dark = np.full((32, 32, 3), 20, dtype=np.uint8)
        bright = np.full((32, 32, 3), 220, dtype=np.uint8)
        kind_d, dark_v = run_torchscript_output("demo.image_mean_score", dark)
        kind_b, bright_v = run_torchscript_output("demo.image_mean_score", bright)
        self.assertEqual(kind_d, "scalar")
        self.assertEqual(kind_b, "scalar")
        self.assertLess(float(dark_v), float(bright_v))

    def test_real_ensemble_torchscript_term_uses_capture(self) -> None:
        try:
            import cv2
            import numpy as np
        except ImportError:
            self.skipTest("opencv not installed")

        from lab_communicator.real.ensemble import RealEnsembleBackend, RealEnsembleHardwareBridge
        from lab_model.optimization.router import ActuatorRouter
        from lab_model.optimization.spec import OptimizeEnsembleParameters

        img = np.full((40, 40, 3), 180, dtype=np.uint8)
        ok, buf = cv2.imencode(".png", img)
        assert ok
        png = buf.tobytes()

        state = {
            "components": {
                "tag_22": {
                    "statecontrol": {"measurables": {}, "tunables": {}},
                    "telemetry": {},
                }
            }
        }
        catalog = {
            "tag_22": {
                "component_class": "OPTICAL_CAMERA",
                "hardware_binding": {"backend": "table_cam", "cam_id": 1},
            }
        }
        comm = MagicMock()
        comm.current_state = state
        comm.catalog_map = catalog
        comm._table_cam_connected = {1: True}
        comm.capture_table_cam.return_value = png
        comm.return_tunables_for_tag.return_value = {}

        bridge = RealEnsembleHardwareBridge(communicator=comm, variables_by_id={}, settle_ms=0)
        payload = {
            "mode": "ensemble",
            "variables": [
                {
                    "id": "m1",
                    "tag_id": "tag_20",
                    "path": "tunables.nominal_motor_positions.1",
                    "physical_type": "continuous",
                    "unit": "deg",
                    "bounds": {"min": -1.0, "max": 1.0},
                    "delta": True,
                }
            ],
            "objective": {
                "type": "weighted_sum",
                "minimize": True,
                "terms": [
                    {
                        "id": "img_mean",
                        "weight": 1.0,
                        "source": {
                            "kind": "torchscript_scalar",
                            "kernel_id": "demo.image_mean_score",
                            "tag_id": "tag_22",
                            "target_scalar": 0.5,
                        },
                        "metric": "squared_error",
                    }
                ],
            },
            "solver": {
                "blocks": [
                    {
                        "id": "b0",
                        "variable_ids": ["m1"],
                        "max_evals": 2,
                    }
                ]
            },
        }
        spec = OptimizeEnsembleParameters.model_validate(payload)
        backend = RealEnsembleBackend(bridge=bridge, spec=spec)
        meas, camera_images = backend._measurements_for_objective(spec.objective)
        self.assertIn("img_mean", meas)
        self.assertGreater(float(meas["img_mean"]["scalar"]), 0.5)
        self.assertIn("tag_22", camera_images)

        router = MagicMock(spec=ActuatorRouter)
        loss, terms = backend.evaluate_loss({}, spec.objective, router=router)
        self.assertGreaterEqual(loss, 0.0)
        self.assertIn("img_mean", terms)


class MeasurableTensorBgrLayoutTests(unittest.TestCase):
    def test_resolve_camera_image_is_bgr(self) -> None:
        try:
            import cv2
            import numpy as np
            import os
            import tempfile
        except ImportError:
            self.skipTest("deps missing")

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "frame.png")
            bgr = np.zeros((5, 7, 3), dtype=np.uint8)
            bgr[:, :] = (5, 50, 250)
            cv2.imwrite(path, bgr)
            tensor = materialize_measurable(
                "tag_22",
                "camera_image",
                {"path": path, "format": "png"},
            )
            self.assertEqual(tensor.axes.get("c"), "bgr")
            resolved = resolve_tensor_with_state_path(tensor, filesystem_path=path)
            self.assertEqual(resolved.shape, (5, 7, 3))
            self.assertEqual(int(resolved.data[0, 0, 0]), 5)
            self.assertEqual(int(resolved.data[0, 0, 2]), 250)


if __name__ == "__main__":
    unittest.main()
