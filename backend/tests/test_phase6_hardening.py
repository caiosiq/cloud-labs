"""Phase 6: clearance, max-delta, abort keep_best/rollback."""
from __future__ import annotations

import unittest
from typing import Dict

import numpy as np

from cloudlabs_edge_dev.optimization.capture import StaticCaptureSource
from cloudlabs_edge_dev.optimization.guards import (
    MaxDeltaError,
    check_max_delta,
    max_delta_limits,
    settle_final_values,
)
from cloudlabs_edge_dev.optimization.router import RecordingRouter
from cloudlabs_edge_dev.optimization.session import run_optimization_session
from cloudlabs_edge_dev.optimization.spec import parse_pipeline
from cloudlabs_edge_dev.optimization.tensors import camera_bgr_tensor


class _LandscapeCapture(StaticCaptureSource):
    def __init__(self, truth: Dict[str, float]) -> None:
        frame = camera_bgr_tensor(np.zeros((4, 4, 3), dtype=np.uint8), tag_id="tag_22")
        super().__init__({"cam": frame})
        self.truth = dict(truth)
        self.applied: Dict[str, float] = dict(truth)

    def set_applied(self, physical: Dict[str, float]) -> None:
        self.applied = dict(physical)

    def read_measurable(self, path: str, *, tag_id: str) -> float:
        err = 0.0
        for k, t in self.truth.items():
            d = float(self.applied.get(k, t)) - float(t)
            err += d * d
        return 1.0 / (1.0 + err)


class _RouterWithCapture(RecordingRouter):
    def __init__(self, landscape: _LandscapeCapture, **kwargs) -> None:
        super().__init__(**kwargs)
        self.landscape = landscape

    def apply_eval(self, physical_values, *, block_id: str) -> None:  # noqa: ANN001
        super().apply_eval(physical_values, block_id=block_id)
        self.landscape.set_applied(dict(physical_values))


def _pipeline(*, max_evals: int = 20, constraints=None, keep_best=True, rollback=False):
    solver = {
        "type": "block_cobyla",
        "max_total_evals": max_evals,
        "keep_best": keep_best,
        "rollback_on_fail": rollback,
        "settle_ms": 0,
        "blocks": [
            {
                "id": "motors",
                "variable_ids": ["v_a"],
                "max_evals": max_evals,
                "passes": 1,
                "rhobeg_u": 0.3,
                "rhoend_u": 0.05,
            }
        ],
    }
    if constraints is not None:
        solver["constraints"] = constraints
    return {
        "schema_version": 1,
        "session_label": "phase6",
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
                "bounds": {"min": -3.0, "max": 3.0},
                "delta": True,
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
                    "params": {
                        "tag_id": "tag_20",
                        "normalize": {"min": 0.0, "max": 1.0},
                    },
                }
            ],
        },
        "solver": solver,
    }


class GuardsUnitTests(unittest.TestCase):
    def test_max_delta_limits_parse(self) -> None:
        limits = max_delta_limits(
            [{"type": "max_delta_from_start", "enabled": True, "limits": {"deg": 0.5}}]
        )
        self.assertEqual(limits, {"deg": 0.5})
        self.assertIsNone(max_delta_limits([{"type": "max_delta_from_start", "enabled": False}]))

    def test_check_max_delta_raises(self) -> None:
        spec = parse_pipeline(_pipeline())
        with self.assertRaises(MaxDeltaError):
            check_max_delta(
                {"v_a": 2.0},
                {"v_a": 0.0},
                spec.variables,
                {"deg": 1.0},
            )

    def test_settle_rollback(self) -> None:
        out = settle_final_values(
            x0={"v_a": 0.0},
            current={"v_a": 1.5},
            best={"v_a": 0.8},
            keep_best=True,
            rollback_on_fail=True,
            aborted=True,
        )
        self.assertEqual(out, {"v_a": 0.0})


class Phase6SessionTests(unittest.TestCase):
    def test_clearance_refuses_session(self) -> None:
        landscape = _LandscapeCapture({"v_a": 1.0})
        router = _RouterWithCapture(landscape, clearance_ok=False)
        result = run_optimization_session(
            _pipeline(max_evals=10),
            {"v_a": 0.0},
            capture=landscape,
            router=router,
        )
        self.assertTrue(result.aborted)
        self.assertIsNotNone(result.refusal)
        self.assertFalse(router.engaged)

    def test_max_delta_refuses_step_without_apply(self) -> None:
        landscape = _LandscapeCapture({"v_a": 2.5})
        router = _RouterWithCapture(landscape)
        result = run_optimization_session(
            _pipeline(
                max_evals=8,
                constraints=[
                    {
                        "type": "max_delta_from_start",
                        "enabled": True,
                        "limits": {"deg": 0.25},
                    }
                ],
            ),
            {"v_a": 0.0},
            capture=landscape,
            router=router,
        )
        # Applies may include settle / x0; none should exceed 0.25 from x0.
        for ev in router.events:
            if ev[0] != "apply":
                continue
            vals = ev[2]
            self.assertLessEqual(abs(float(vals["v_a"]) - 0.0), 0.25 + 1e-6)
        self.assertGreater(result.evals, 0)
        self.assertTrue(any(r.get("refused") == "max_delta_from_start" for r in result.trace))

    def test_abort_keep_best_settles(self) -> None:
        landscape = _LandscapeCapture({"v_a": 1.0})
        router = _RouterWithCapture(landscape)
        calls = {"n": 0}

        def should_abort() -> bool:
            calls["n"] += 1
            return calls["n"] > 4

        result = run_optimization_session(
            _pipeline(max_evals=30, keep_best=True, rollback=False),
            {"v_a": 0.0},
            capture=landscape,
            router=router,
            should_abort=should_abort,
        )
        self.assertTrue(result.aborted)
        self.assertEqual(result.final_values, result.final_values)  # sanity
        # Last settle/apply should match keep_best final.
        self.assertIsNotNone(router.last_applied)
        self.assertAlmostEqual(
            float(router.last_applied["v_a"]),
            float(result.final_values["v_a"]),
            places=5,
        )

    def test_abort_rollback_to_x0(self) -> None:
        landscape = _LandscapeCapture({"v_a": 1.0})
        router = _RouterWithCapture(landscape)
        calls = {"n": 0}

        def should_abort() -> bool:
            calls["n"] += 1
            return calls["n"] > 3

        result = run_optimization_session(
            _pipeline(max_evals=30, keep_best=True, rollback=True),
            {"v_a": 0.0},
            capture=landscape,
            router=router,
            should_abort=should_abort,
        )
        self.assertTrue(result.aborted)
        self.assertAlmostEqual(float(result.final_values["v_a"]), 0.0, places=5)


if __name__ == "__main__":
    unittest.main()
