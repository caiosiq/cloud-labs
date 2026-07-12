"""Unit tests for real bench ensemble helpers (no hardware required)."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np

from lab_communicator.real.ensemble import (
    RealEnsembleBackend,
    RealEnsembleHardwareBridge,
    _read_scalar_from_state,
)
from lab_model.optimization.metrics.image_features import (
    compute_beam_centroid_px,
    compute_beam_power_scalar,
)
from lab_model.optimization.spec import OptimizeEnsembleParameters


class TestBeamCentroid(unittest.TestCase):
    def test_centroid_finds_bright_spot(self) -> None:
        try:
            import cv2
        except ImportError:
            self.skipTest("opencv not installed")

        img = np.zeros((100, 120, 3), dtype=np.uint8)
        cv2.circle(img, (70, 40), 8, (255, 255, 255), -1)
        centroid = compute_beam_centroid_px(img)
        self.assertIsNotNone(centroid)
        cx, cy = centroid
        self.assertAlmostEqual(cx, 70.0, delta=3.0)
        self.assertAlmostEqual(cy, 40.0, delta=3.0)

    def test_power_scalar_in_range(self) -> None:
        try:
            import cv2
        except ImportError:
            self.skipTest("opencv not installed")

        img = np.zeros((64, 64, 3), dtype=np.uint8)
        cv2.circle(img, (32, 32), 6, (240, 240, 240), -1)
        power = compute_beam_power_scalar(img)
        self.assertIsNotNone(power)
        assert power is not None
        self.assertGreater(power, 0.0)
        self.assertLessEqual(power, 1.0)


class TestReadScalarFromState(unittest.TestCase):
    def test_reads_nested_measurable(self) -> None:
        state = {
            "components": {
                "tag_20": {
                    "statecontrol": {
                        "measurables": {
                            "last_optimization_score": {"scalar": 0.42},
                        }
                    },
                    "telemetry": {"teleop": {"active": False}, "live_feed": {}},
                }
            }
        }
        val = _read_scalar_from_state(
            state, "tag_20", "measurables.last_optimization_score"
        )
        self.assertAlmostEqual(val, 0.42)


class TestRealEnsembleMeasurements(unittest.TestCase):
    def test_derived_centroid_from_mock_capture(self) -> None:
        try:
            import cv2
        except ImportError:
            self.skipTest("opencv not installed")

        png_bytes = self._spot_png_bytes()
        comm = MagicMock()
        comm.current_state = {"components": {}}
        comm.catalog_map = {
            "tag_20": {"type": "OPTICAL_MIRROR", "motor_controller": "mirror_ctrl", "motor_ids": [1]},
            "tag_22": {
                "type": "OPTICAL_CAMERA",
                "hardware_binding": {"backend": "recorder_tcp", "recorder_cam_id": 1},
            },
        }
        comm.return_tunables_for_tag.return_value = {}
        comm.capture_table_cam.return_value = png_bytes
        comm._table_cam_connected = {1: True}

        bridge = RealEnsembleHardwareBridge(
            communicator=comm,
            variables_by_id={},
            settle_ms=0,
        )
        spec = OptimizeEnsembleParameters.model_validate(
            {
                "variables": [
                    {
                        "id": "v1",
                        "tag_id": "tag_20",
                        "path": "tunables.nominal_motor_positions.1",
                        "physical_type": "continuous",
                        "unit": "deg",
                        "bounds": {"min": -1, "max": 1},
                    }
                ],
                "objective": {
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
                        }
                    ]
                },
                "solver": {
                    "blocks": [{"id": "b1", "variable_ids": ["v1"]}],
                },
            }
        )
        backend = RealEnsembleBackend(bridge=bridge, spec=spec)
        objective = spec.objective
        meas = backend._measurements_for_objective(objective)
        self.assertIn("centroid_rms_px", meas)
        self.assertAlmostEqual(meas["centroid_rms_px"]["centroid_x"], 70.0, delta=4.0)
        self.assertAlmostEqual(meas["centroid_rms_px"]["centroid_y"], 40.0, delta=4.0)
        comm.capture_table_cam.assert_called_with(1, exposure=0.2)

    @staticmethod
    def _spot_png_bytes() -> bytes:
        import cv2

        img = np.zeros((100, 120, 3), dtype=np.uint8)
        cv2.circle(img, (70, 40), 8, (255, 255, 255), -1)
        ok, buf = cv2.imencode(".png", img)
        assert ok
        return buf.tobytes()


class TestRealMotorApply(unittest.TestCase):
    def test_sync_move_motor_updates_store(self) -> None:
        comm = MagicMock()
        comm.catalog_map = {"tag_20": {"motor_controller": "mirror_ctrl", "motor_ids": [1]}}
        comm._motor_catalog_ok.return_value = True
        controller = MagicMock()
        comm.experiment = SimpleNamespace(mirror_ctrl=controller)

        bridge = RealEnsembleHardwareBridge(
            communicator=comm,
            variables_by_id={},
        )

        with patch("lab_communicator.real.ensemble.motor_rot") as motor_rot:
            motor_rot.get_angle.return_value = 0.0
            bridge._sync_move_motor("tag_20", 1, 1.5)
            controller.move_motor.assert_called_once_with(1, 1.5, wait_completion=True)
            motor_rot.add_delta.assert_called_once_with("tag_20", 1, 1.5)


if __name__ == "__main__":
    unittest.main()
