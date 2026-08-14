"""Operator accept / good-enough early-stop (success path, keep best)."""
from __future__ import annotations

import unittest

from cloudlabs_edge_dev.optimization.capture import StaticCaptureSource
from cloudlabs_edge_dev.optimization.router import RecordingRouter
from cloudlabs_edge_dev.optimization.session import run_optimization_session
from cloudlabs_edge_dev.optimization.tensors import camera_bgr_tensor
import numpy as np


def _pipeline(*, max_evals=40, stop_loss=0.01):
    return {
        "schema_version": 1,
        "session_label": "accept_test",
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
            "keep_best": True,
            "rollback_on_fail": False,
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


class OperatorAcceptEarlyStopTests(unittest.TestCase):
    def test_accept_after_first_eval(self) -> None:
        # Mid loss so stop_loss would not fire; operator accept should.
        cap = StaticCaptureSource(
            {
                "unused": camera_bgr_tensor(
                    np.zeros((8, 8, 3), dtype=np.uint8), tag_id="tag_22"
                )
            },
            measurables={("tag_20", "measurables.last_optimization_score"): 0.4},
        )
        router = RecordingRouter()
        flag = {"go": False}

        def _should_accept() -> bool:
            return bool(flag["go"])

        def _on_progress(*, step=None, **kwargs):
            if step and int(step) >= 1:
                flag["go"] = True

        result = run_optimization_session(
            _pipeline(max_evals=40, stop_loss=0.01),
            {"m1": 0.0},
            capture=cap,
            router=router,
            session_id="accept1",
            progress_callback=_on_progress,
            should_accept=_should_accept,
        )
        self.assertTrue(result.early_stopped)
        self.assertFalse(result.aborted)
        self.assertEqual(result.early_stop_reason, "operator_accept")
        self.assertLess(result.evals, 40)
        self.assertIn("m1", result.final_values)


if __name__ == "__main__":
    unittest.main()
