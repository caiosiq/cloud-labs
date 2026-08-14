"""Early-stop when total loss ≤ solver.stop_loss."""
from __future__ import annotations

import unittest

from cloudlabs_edge_dev.optimization.capture import StaticCaptureSource
from cloudlabs_edge_dev.optimization.router import RecordingRouter
from cloudlabs_edge_dev.optimization.session import run_optimization_session
from cloudlabs_edge_dev.optimization.tensors import camera_bgr_tensor
import numpy as np


def _pipeline(*, stop_loss, max_evals=40):
    return {
        "schema_version": 1,
        "session_label": "stop_loss_test",
        "variables": [
            {
                "id": "m1",
                "tag_id": "tag_20",
                "actuator": {
                    "kind": "motor",
                    "controller": "wifi_stepper1",
                    "motor_id": 1,
                },
                "physical_type": "continuous",
                "unit": "deg",
                "bounds": {"min": -2.0, "max": 2.0},
            }
        ],
        "capture": [],
        "objective": {
            "type": "weighted_sum",
            "minimize": True,
            "terms": [
                {
                    "id": "score",
                    "weight": 1.0,
                    "metric": "one_minus_normalized",
                    "measurable_path": "measurables.last_optimization_score",
                    "params": {"normalize": {"min": 0.0, "max": 1.0}, "tag_id": "tag_20"},
                }
            ],
        },
        "solver": {
            "type": "block_cobyla",
            "max_total_evals": max_evals,
            "stop_loss": stop_loss,
            "settle_ms": 0,
            "blocks": [
                {
                    "id": "b0",
                    "variable_ids": ["m1"],
                    "max_evals": max_evals,
                    "passes": 1,
                }
            ],
        },
    }


class StopLossEarlyStopTests(unittest.TestCase):
    def test_stops_when_loss_already_good(self) -> None:
        # Score=1.0 → one_minus_normalized = 0 ≤ stop_loss 0.1 on first eval.
        cap = StaticCaptureSource(
            {
                "unused": camera_bgr_tensor(
                    np.zeros((8, 8, 3), dtype=np.uint8), tag_id="tag_22"
                )
            },
            measurables={("tag_20", "measurables.last_optimization_score"): 1.0},
        )
        router = RecordingRouter()
        progress: list = []

        def _on_progress(*, step=None, **kwargs):
            progress.append({"step": step, **kwargs})

        result = run_optimization_session(
            _pipeline(stop_loss=0.1, max_evals=40),
            {"m1": 0.0},
            capture=cap,
            router=router,
            session_id="s1",
            progress_callback=_on_progress,
        )
        self.assertTrue(result.early_stopped)
        self.assertFalse(result.aborted)
        self.assertLessEqual(result.best_loss, 0.1)
        self.assertLess(result.evals, 40)
        self.assertTrue(any(r.get("early_stop") for r in result.trace))
        self.assertTrue(progress)
        stages = progress[0].get("stages") or {}
        policy = stages.get("policy") or {}
        self.assertIn("loss", policy)
        self.assertEqual(policy.get("stop_loss"), 0.1)
        self.assertTrue(policy.get("early_stop") or policy.get("would_early_stop"))
        self.assertIn("score", policy.get("terms") or {})

    def test_no_early_stop_when_disabled(self) -> None:
        cap = StaticCaptureSource(
            {},
            measurables={("tag_20", "measurables.last_optimization_score"): 0.5},
        )
        # loss = 0.5 with no stop_loss → runs until budget (COBYLA may use fewer).
        result = run_optimization_session(
            _pipeline(stop_loss=None, max_evals=8),
            {"m1": 0.0},
            capture=cap,
            router=RecordingRouter(),
            session_id="s2",
        )
        self.assertFalse(result.early_stopped)
        self.assertFalse(any(r.get("early_stop") for r in result.trace))
        # Policy debug still present on trace rows even without early-stop.
        row = result.trace[0]
        policy = (row.get("stages") or {}).get("policy") or {}
        self.assertEqual(policy.get("stop_loss"), None)
        self.assertFalse(policy.get("would_early_stop"))
        self.assertAlmostEqual(float(policy.get("loss")), 0.5, places=5)


if __name__ == "__main__":
    unittest.main()
