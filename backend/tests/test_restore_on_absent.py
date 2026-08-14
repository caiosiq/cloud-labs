"""Restore-on-absent + distance barrier when OPTIMIZE loses the beam."""
from __future__ import annotations

import unittest
from typing import Any, Dict, Mapping
from unittest import mock

import numpy as np

from cloudlabs_edge_dev.optimization.capture import StaticCaptureSource
from cloudlabs_edge_dev.optimization.router import RecordingRouter
from cloudlabs_edge_dev.optimization.session import (
    DEFAULT_ABSENT_STEP_BARRIER,
    run_optimization_session,
)
from cloudlabs_edge_dev.optimization.tensors import camera_bgr_tensor


class _AppliedCapture(StaticCaptureSource):
    def __init__(self) -> None:
        frame = camera_bgr_tensor(np.zeros((8, 8, 3), dtype=np.uint8), tag_id="tag_22")
        super().__init__({"cam": frame})
        self.applied: Dict[str, float] = {"v_a": 0.0}

    def set_applied(self, physical: Mapping[str, float]) -> None:
        self.applied = {k: float(v) for k, v in physical.items()}


class _RouterWithCapture(RecordingRouter):
    def __init__(self, landscape: _AppliedCapture) -> None:
        super().__init__()
        self.landscape = landscape

    def apply_eval(self, physical_values, *, block_id: str) -> None:  # noqa: ANN001
        super().apply_eval(physical_values, block_id=block_id)
        self.landscape.set_applied(physical_values)


def _pipeline(*, max_evals: int = 6, barrier: float = 1.0) -> Dict[str, Any]:
    return {
        "schema_version": 1,
        "session_label": "restore-absent",
        "variables": [
            {
                "id": "v_a",
                "tag_id": "tag_20",
                "actuator": {
                    "kind": "motor",
                    "controller": "wifi_stepper1",
                    "motor_id": 1,
                },
                "physical_type": "continuous",
                "unit": "deg",
                "bounds": {"min": -10.0, "max": 10.0},
                "delta": True,
            }
        ],
        "capture": [{"id": "cam", "tag_id": "tag_22", "field": "camera_image"}],
        "objective": {
            "type": "weighted_sum",
            "minimize": True,
            "terms": [
                {
                    "id": "align",
                    "weight": 1.0,
                    "metric": "rms_distance",
                    "capture_id": "cam",
                    "kernel_id": "builtin.roi_centroid",
                    "params": {
                        "feature_index": [0, 1],
                        "target": [4.0, 4.0],
                        "latch_peak_ref": True,
                        "min_peak_ratio": 0.5,
                        "peak_feature_index": 2,
                        "rms_scale_px": 10.0,
                        "loss_cap": 2.0,
                    },
                }
            ],
        },
        "solver": {
            "type": "block_cobyla",
            "max_total_evals": max_evals,
            "keep_best": True,
            "settle_ms": 0,
            "absent_step_barrier": barrier,
            "blocks": [
                {
                    "id": "motors",
                    "variable_ids": ["v_a"],
                    "max_evals": max_evals,
                    "passes": 1,
                    "rhobeg_u": 0.5,
                    "rhoend_u": 0.1,
                }
            ],
        },
    }


class RestoreOnAbsentTests(unittest.TestCase):
    def test_absent_restores_and_adds_distance_barrier(self) -> None:
        landscape = _AppliedCapture()
        router = _RouterWithCapture(landscape)
        calls = {"n": 0}

        def fake_collect(pipeline, capture, *, kernels_dir, physical):  # noqa: ANN001
            calls["n"] += 1
            # Near x0 → bright peak; far step → peak collapses (absent).
            a = float(physical["v_a"])
            peak = 100.0 if abs(a) < 1.0 else 10.0
            measurements = {"align": {"features": [4.0, 4.0, peak]}}
            dbg = {
                "capture": {
                    "ok": True,
                    "frames": [
                        {
                            "capture_id": "cam",
                            "tag_id": "tag_22",
                            "shape": [8, 8, 3],
                            "layout": "bgr",
                        }
                    ],
                },
                "kernel": {"ok": True, "terms": {"align": {"kind": "features"}}},
            }
            return measurements, dbg

        with mock.patch(
            "cloudlabs_edge_dev.optimization.session._collect_measurements",
            side_effect=fake_collect,
        ):
            result = run_optimization_session(
                _pipeline(max_evals=4, barrier=1.0),
                {"v_a": 0.0},
                capture=landscape,
                router=router,
            )

        self.assertGreaterEqual(result.evals, 2)
        absent_rows = [
            row
            for row in result.trace
            if (row.get("stages") or {}).get("actuate_restore")
        ]
        self.assertTrue(absent_rows, "expected at least one presence-restore eval")
        row = absent_rows[0]
        restore = row["stages"]["actuate_restore"]
        self.assertTrue(restore.get("ok"))
        self.assertEqual(restore.get("reason"), "presence_absent")
        self.assertGreater(float(restore.get("du_from_last_present") or 0), 0.0)
        self.assertGreater(float(restore.get("barrier") or 0), 0.0)
        self.assertIn("__absent_step_barrier__", row.get("terms") or {})
        # Loss = absent cap (~2) + barrier (>0)
        self.assertGreaterEqual(float(row["loss"]), 2.0 + 0.05)
        # Hardware left at last-present (x0), not the ejected trial.
        self.assertAlmostEqual(float(landscape.applied["v_a"]), 0.0, places=5)
        # Apply sequence includes a restore block id.
        apply_blocks = [e[1] for e in router.events if e[0] == "apply"]
        self.assertTrue(any(str(b).endswith(":restore") for b in apply_blocks))
        self.assertEqual(DEFAULT_ABSENT_STEP_BARRIER, 1.0)


if __name__ == "__main__":
    unittest.main()
