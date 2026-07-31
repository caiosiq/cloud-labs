"""Cooperative cancel during ensemble optimization (Phase C)."""
from __future__ import annotations

import copy
import json
import os
import unittest
from pathlib import Path

from mock_backend.host.ensemble import build_mock_ensemble_backend, run_mock_ensemble_session
from lab_model.coordinator.backends.lab_view_config import bootstrap_lab_view
from lab_model.execution.optimization.preflight import preflight_ensemble

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_MOCK_LAB_VIEW = _PROJECT_ROOT / "mock_backend" / "lab_view"
_LAB_STATE = _MOCK_LAB_VIEW / "lab_state.json"


def _small_payload() -> dict:
    return {
        "mode": "ensemble",
        "session_label": "abort-test",
        "variables": [
            {
                "id": "v_m20_m1",
                "tag_id": "tag_20",
                "path": "tunables.nominal_motor_positions.1",
                "physical_type": "continuous",
                "unit": "deg",
                "bounds": {"min": -3.0, "max": 3.0},
                "delta": True,
            }
        ],
        "objective": {
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
                }
            ],
        },
        "solver": {
            "type": "block_cobyla",
            "max_total_evals": 80,
            "blocks": [
                {
                    "id": "block_one",
                    "variable_ids": ["v_m20_m1"],
                    "max_evals": 80,
                    "passes": 2,
                }
            ],
        },
    }


class EnsembleAbortTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ["LAB_VIEW_PATH"] = str(_MOCK_LAB_VIEW)
        bootstrap_lab_view(str(_PROJECT_ROOT))
        with open(_LAB_STATE, "r", encoding="utf-8") as handle:
            cls.fixture_runtime = json.load(handle)

    def test_should_abort_stops_early(self) -> None:
        state = copy.deepcopy(self.fixture_runtime)
        payload = _small_payload()
        spec, _, x0 = preflight_ensemble(state, payload)
        evals_seen = {"n": 0}

        def _progress(**kwargs) -> None:
            step = kwargs.get("step")
            if step is not None:
                evals_seen["n"] = int(step)

        def _abort_after_three() -> bool:
            return evals_seen["n"] >= 3

        result = run_mock_ensemble_session(
            state,
            spec,
            x0,
            session_id="abort",
            progress_callback=_progress,
            should_abort=_abort_after_three,
        )
        self.assertLessEqual(evals_seen["n"], 10)
        self.assertGreaterEqual(evals_seen["n"], 3)
        self.assertIsNotNone(result.best_loss)

    def test_sync_measurables_written_during_eval(self) -> None:
        state = copy.deepcopy(self.fixture_runtime)
        payload = _small_payload()
        spec, _, x0 = preflight_ensemble(state, payload)
        backend = build_mock_ensemble_backend(state, spec, x0)
        block = spec.solver.blocks[0]
        router = backend.router_for_block(block, block.variable_ids)
        router.enter_continuous_block(block.variable_ids)
        backend.apply_through_router(router, x0, block_id=block.id)
        backend.evaluate_loss(x0, spec.objective, router=router)
        meas = state["components"]["tag_20"]["statecontrol"]["measurables"]
        self.assertIn("centroid_x_px", meas)
        self.assertIn("last_optimization_score", meas)


if __name__ == "__main__":
    unittest.main()
