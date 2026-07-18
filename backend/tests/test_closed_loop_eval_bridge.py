"""Tests for closed-loop eval sync + shared camera capture."""
from __future__ import annotations

import os
import tempfile
import unittest
from typing import Any, Dict
from unittest.mock import MagicMock

from lab_model.language.measurables.capture import capture_png_for_tag, camera_image_meta_from_png
from lab_model.execution.optimization.eval_sync import sync_measurables_from_objective_eval
from lab_model.execution.optimization.kernels import apply_kernel_hooks, get_kernel, validate_kernel_ids
from lab_model.execution.optimization.spec import ObjectiveSpec


class EvalSyncTests(unittest.TestCase):
    def test_sync_writes_centroid_and_scalar(self) -> None:
        state: Dict[str, Any] = {
            "components": {
                "tag_20": {"statecontrol": {"measurables": {}}},
                "tag_22": {"statecontrol": {"measurables": {}}},
                "tag_50": {"statecontrol": {"measurables": {}}},
            }
        }
        objective = ObjectiveSpec.model_validate(
            {
                "type": "weighted_sum",
                "minimize": True,
                "terms": [
                    {
                        "id": "centroid_rms_px",
                        "weight": 1.0,
                        "source": {
                            "tag_id": "tag_20",
                            "kind": "derived_centroid",
                            "from": "measurables.camera_image",
                            "target_px": {"x": 512.0, "y": 384.0},
                        },
                        "metric": "rms_distance_px",
                    },
                    {
                        "id": "laser_power",
                        "weight": 0.25,
                        "source": {
                            "tag_id": "tag_50",
                            "kind": "measurable_scalar",
                            "path": "measurables.output_power_readback_mw",
                            "normalize": {"min": 0.0, "max": 100.0},
                        },
                        "metric": "one_minus_normalized",
                    },
                ],
            }
        )
        measurements = {
            "centroid_rms_px": {"centroid_x": 70.0, "centroid_y": 40.0},
            "laser_power": {"scalar": 42.0},
        }
        camera_images = {
            "tag_22": {"path": "/tmp/frame.png", "format": "png", "source": "test"},
        }
        catalog = {
            "tag_20": {"type": "OPTICAL_MIRROR"},
            "tag_22": {"type": "OPTICAL_CAMERA"},
            "tag_50": {"type": "LASER_SOURCE"},
        }
        touched = sync_measurables_from_objective_eval(
            state,
            objective,
            measurements,
            catalog_map=catalog,
            camera_images=camera_images,
        )
        self.assertIn("tag_22", touched["tags"])
        self.assertEqual(
            state["components"]["tag_22"]["statecontrol"]["measurables"]["centroid_x_px"],
            70.0,
        )
        power = state["components"]["tag_50"]["statecontrol"]["measurables"][
            "output_power_readback_mw"
        ]
        self.assertIsInstance(power, dict)
        self.assertEqual(power.get("domain"), "scalar")
        self.assertEqual(power.get("data"), 42.0)
        cam = state["components"]["tag_22"]["statecontrol"]["measurables"]["camera_image"]
        self.assertIsInstance(cam, dict)
        self.assertEqual(cam.get("domain"), "spatial")
        self.assertEqual(cam.get("data", {}).get("kind"), "file")
        self.assertEqual(cam.get("data", {}).get("href"), "/tmp/frame.png")


class CaptureHelperTests(unittest.TestCase):
    def test_capture_png_routes_table_cam(self) -> None:
        bridge = MagicMock()
        bridge.return_tunables_for_tag.return_value = {"exposure_time_ms": 100}
        bridge._table_cam_connected = {1: True}
        bridge.capture_table_cam.return_value = b"PNGDATA"
        out = capture_png_for_tag(
            bridge,
            "tag_22",
            {"type": "OPTICAL_CAMERA", "properties": {"vision_device_index": 1}},
        )
        self.assertEqual(out, b"PNGDATA")
        bridge.capture_table_cam.assert_called()

    def test_camera_image_meta_writes_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bridge = MagicMock()
            bridge._camera_captures_dir.return_value = tmp
            meta = camera_image_meta_from_png(
                bridge,
                "tag_22",
                {"type": "OPTICAL_CAMERA"},
                b"\x89PNG\r\n\x1a\n",
                filename="tag_22_test.png",
                source="unit_test",
            )
            self.assertTrue(os.path.isfile(meta["path"]))
            self.assertEqual(meta["format"], "png")
            self.assertEqual(meta["source"], "unit_test")


class KernelHookTests(unittest.TestCase):
    def test_image_features_kernel_registered(self) -> None:
        self.assertIsNotNone(get_kernel("ensemble.eval.image_features"))
        validate_kernel_ids(
            ["ensemble.eval.image_features", "measurable.materialize.camera"]
        )

    def test_real_defaults_without_kernels(self) -> None:
        ctx: Dict[str, Any] = {"backend": "real"}
        notes = apply_kernel_hooks([], hook="evaluate", context=ctx)
        self.assertTrue(ctx.get("use_image_features"))
        self.assertTrue(ctx.get("sync_measurables"))
        self.assertTrue(ctx.get("write_camera_image"))
        self.assertEqual(notes["flags"]["use_image_features"], True)

    def test_materialize_kernel_sets_flags(self) -> None:
        ctx: Dict[str, Any] = {"backend": "real"}
        apply_kernel_hooks(
            ["measurable.materialize.camera"],
            hook="evaluate",
            context=ctx,
        )
        self.assertTrue(ctx["write_camera_image"])
        self.assertTrue(ctx["sync_measurables"])


if __name__ == "__main__":
    unittest.main()
