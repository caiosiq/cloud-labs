"""Phase 1: edge optimization session with fake capture + recording router."""
from __future__ import annotations

import unittest
from typing import Dict

import numpy as np

from cloudlabs_edge_dev.optimization.capture import StaticCaptureSource
from cloudlabs_edge_dev.optimization.router import RecordingRouter
from cloudlabs_edge_dev.optimization.session import run_optimization_session
from cloudlabs_edge_dev.optimization.tensors import camera_bgr_tensor


class _LandscapeCapture(StaticCaptureSource):
    """Capture source whose measurable scalar is a quadratic bowl in applied state."""

    def __init__(self, truth: Dict[str, float]) -> None:
        # Dummy camera tensor (unused — objective uses measurable_path).
        frame = camera_bgr_tensor(np.zeros((4, 4, 3), dtype=np.uint8), tag_id="tag_22")
        super().__init__({"cam": frame})
        self.truth = dict(truth)
        self.applied: Dict[str, float] = dict(truth)

    def set_applied(self, physical: Dict[str, float]) -> None:
        self.applied = dict(physical)

    def read_measurable(self, path: str, *, tag_id: str) -> float:
        # Score = 1 / (1 + ||x - truth||^2) so maximize → minimize one_minus.
        err = 0.0
        for k, t in self.truth.items():
            d = float(self.applied.get(k, t)) - float(t)
            err += d * d
        return 1.0 / (1.0 + err)


class _RouterWithCapture(RecordingRouter):
    def __init__(self, landscape: _LandscapeCapture) -> None:
        super().__init__()
        self.landscape = landscape

    def apply_eval(self, physical_values, *, block_id: str) -> None:  # noqa: ANN001
        super().apply_eval(physical_values, block_id=block_id)
        self.landscape.set_applied(dict(physical_values))


class SessionTests(unittest.TestCase):
    def _pipeline(self, *, max_evals: int = 25, passes: int = 1):
        return {
            "schema_version": 1,
            "session_label": "synthetic bowl",
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
                    "bounds": {"min": -2.0, "max": 2.0},
                    "delta": True,
                },
                {
                    "id": "v_b",
                    "tag_id": "tag_20",
                    "actuator": {
                        "kind": "motor",
                        "controller": "wifi_stepper1",
                        "motor_id": 3,
                    },
                    "physical_type": "continuous",
                    "unit": "deg",
                    "bounds": {"min": -2.0, "max": 2.0},
                    "delta": True,
                },
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
                        "params": {
                            "tag_id": "tag_20",
                            "normalize": {"min": 0.0, "max": 1.0},
                        },
                    }
                ],
            },
            "solver": {
                "type": "block_cobyla",
                "max_total_evals": max_evals,
                "keep_best": True,
                "settle_ms": 0,
                "blocks": [
                    {
                        "id": "motors",
                        "variable_ids": ["v_a", "v_b"],
                        "max_evals": max_evals,
                        "passes": passes,
                        "rhobeg_u": 0.2,
                        "rhoend_u": 0.01,
                    }
                ],
            },
        }

    def test_converges_toward_truth(self) -> None:
        truth = {"v_a": 1.0, "v_b": -0.5}
        x0 = {"v_a": 0.0, "v_b": 0.0}
        landscape = _LandscapeCapture(truth)
        router = _RouterWithCapture(landscape)
        result = run_optimization_session(
            self._pipeline(max_evals=40),
            x0,
            capture=landscape,
            router=router,
        )
        self.assertGreater(result.evals, 0)
        self.assertFalse(router.engaged)
        # Should improve on x0 loss.
        landscape.set_applied(x0)
        start = 1.0 - landscape.read_measurable(
            "measurables.last_optimization_score", tag_id="tag_20"
        )
        self.assertLess(result.best_loss, start)
        # Final values closer to truth than start on L2.
        def dist(vals):
            return sum((vals[k] - truth[k]) ** 2 for k in truth) ** 0.5

        self.assertLess(dist(result.final_values), dist(x0))

    def test_max_total_evals_respected(self) -> None:
        truth = {"v_a": 0.5, "v_b": 0.5}
        landscape = _LandscapeCapture(truth)
        router = _RouterWithCapture(landscape)
        result = run_optimization_session(
            self._pipeline(max_evals=5),
            {"v_a": 0.0, "v_b": 0.0},
            capture=landscape,
            router=router,
        )
        self.assertLessEqual(result.evals, 5)

    def test_abort_mid_block(self) -> None:
        truth = {"v_a": 1.0, "v_b": 1.0}
        landscape = _LandscapeCapture(truth)
        router = _RouterWithCapture(landscape)
        calls = {"n": 0}

        def should_abort() -> bool:
            calls["n"] += 1
            return calls["n"] > 3

        result = run_optimization_session(
            self._pipeline(max_evals=40),
            {"v_a": 0.0, "v_b": 0.0},
            capture=landscape,
            router=router,
            should_abort=should_abort,
        )
        self.assertTrue(result.aborted)
        self.assertFalse(router.engaged)

    def test_block_ordering_continuous(self) -> None:
        truth = {"v_a": 0.0, "v_b": 0.0}
        landscape = _LandscapeCapture(truth)
        router = _RouterWithCapture(landscape)
        run_optimization_session(
            self._pipeline(max_evals=3),
            {"v_a": 0.0, "v_b": 0.0},
            capture=landscape,
            router=router,
        )
        kinds = [e[0] for e in router.events]
        self.assertIn("enter_continuous", kinds)
        self.assertIn("exit", kinds)
        self.assertEqual(kinds[0], "enter_continuous")
        self.assertEqual(kinds[-1], "exit")
        self.assertFalse(router.engaged)

    def test_settle_ms_sleeps_before_capture(self) -> None:
        """solver.settle_ms must pause after actuate before the next capture."""
        from unittest import mock

        truth = {"v_a": 0.0, "v_b": 0.0}
        landscape = _LandscapeCapture(truth)
        router = _RouterWithCapture(landscape)
        pipe = self._pipeline(max_evals=2)
        pipe["solver"]["settle_ms"] = 250

        with mock.patch(
            "cloudlabs_edge_dev.optimization.session.time.sleep"
        ) as sleep_mock:
            result = run_optimization_session(
                pipe,
                {"v_a": 0.0, "v_b": 0.0},
                capture=landscape,
                router=router,
            )
        self.assertGreater(result.evals, 0)
        self.assertGreaterEqual(sleep_mock.call_count, result.evals)
        for call in sleep_mock.call_args_list:
            self.assertAlmostEqual(float(call.args[0]), 0.25, places=6)
        # Trace should stamp the configured settle on successful actuates.
        stamped = [
            row.get("stages", {}).get("actuate", {}).get("settle_ms")
            for row in result.trace
            if row.get("stages", {}).get("actuate", {}).get("ok")
        ]
        self.assertTrue(stamped)
        self.assertTrue(all(s == 250 for s in stamped))


if __name__ == "__main__":
    unittest.main()
