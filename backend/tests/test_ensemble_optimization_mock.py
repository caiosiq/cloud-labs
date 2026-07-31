"""Phase 2 integration tests: mock ensemble landscape + COBYLA."""
from __future__ import annotations

import asyncio
import copy
import json
import os
import unittest
from pathlib import Path
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock

from lab_model.coordinator.backends.lab_view_config import bootstrap_lab_view
from mock_backend.host.ensemble import (
    MockActuatorRouter,
    MockEnsembleHardwareBridge,
    MockEnsembleLandscape,
    build_mock_ensemble_backend,
    run_mock_ensemble_session,
)
from lab_model.language.domain.holding import SYSTEM_STATUS_IDLE
from lab_model.execution.optimization.preflight import preflight_ensemble
from lab_model.execution.orchestration.optimize_ensemble import run_optimize_ensemble

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_MOCK_LAB_VIEW = _PROJECT_ROOT / "mock_backend" / "lab_view"
_LAB_STATE = _MOCK_LAB_VIEW / "lab_state.json"


def _four_mirror_payload() -> Dict[str, Any]:
    motors = [
        ("v_m20_m1", "tag_20", "1"),
        ("v_m20_m3", "tag_20", "3"),
        ("v_m18_m1", "tag_18", "1"),
        ("v_m18_m3", "tag_18", "3"),
    ]
    variables = [
        {
            "id": vid,
            "tag_id": tag,
            "path": f"tunables.nominal_motor_positions.{mid}",
            "physical_type": "continuous",
            "unit": "deg",
            "bounds": {"min": -3.0, "max": 3.0},
            "delta": True,
        }
        for vid, tag, mid in motors
    ]
    return {
        "mode": "ensemble",
        "session_label": "two-mirror mock",
        "variables": variables,
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
                },
                {
                    "id": "power_intensity",
                    "weight": 0.35,
                    "source": {
                        "tag_id": "tag_20",
                        "kind": "measurable_scalar",
                        "path": "measurables.last_optimization_score",
                        "normalize": {"min": 0.0, "max": 1.0},
                    },
                    "metric": "one_minus_normalized",
                },
            ],
        },
        "solver": {
            "type": "block_cobyla",
            "max_total_evals": 200,
            "keep_best": True,
            "settle_ms": 0,
            "blocks": [
                {
                    "id": "block_mirrors",
                    "variable_ids": [v["id"] for v in variables],
                    "max_evals": 100,
                    "trust_region_u": 0.3,
                    "rhobeg_u": 0.1,
                    "rhoend_u": 0.001,
                    "passes": 3,
                }
            ],
        },
    }


def _invasive_lens_payload() -> Dict[str, Any]:
    base = _four_mirror_payload()
    base["variables"].append(
        {
            "id": "v_lens_y",
            "tag_id": "tag_11",
            "path": "tunables.nominal_pose.y",
            "physical_type": "invasive_discrete",
            "unit": "mm",
            "bounds": {"min": -5.0, "max": 5.0},
            "delta": True,
            "touch_and_go": {
                "required": True,
                "gripper_tag": "tag_99",
                "safe_home_tag": "tag_99",
                "settle_ms_after_move": 0,
                "settle_ms_after_release": 0,
                "settle_ms_after_reengage": 0,
                "re_engage_on_demand": True,
                "re_engage_after_measure": False,
            },
        }
    )
    base["solver"]["blocks"].append(
        {
            "id": "block_lens",
            "variable_ids": ["v_lens_y"],
            "max_evals": 25,
            "passes": 1,
        }
    )
    base["solver"]["max_total_evals"] = 160
    return base


class EnsembleOptimizationMockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ["LAB_VIEW_PATH"] = str(_MOCK_LAB_VIEW)
        bootstrap_lab_view(str(_PROJECT_ROOT))
        with open(_LAB_STATE, "r", encoding="utf-8") as handle:
            cls.fixture_runtime = json.load(handle)

    def test_four_motor_session_converges_toward_hidden_truth(self) -> None:
        state = copy.deepcopy(self.fixture_runtime)
        payload = _four_mirror_payload()
        spec, _, x0 = preflight_ensemble(state, payload)
        landscape = MockEnsembleLandscape(variables=spec.variables, x0=x0)
        from lab_model.execution.optimization.metrics import evaluate_weighted_sum

        start_at_x0_meas = landscape.measurements_for_objective(
            x0, spec.objective, held=False
        )
        loss_x0, _ = evaluate_weighted_sum(spec.objective, start_at_x0_meas)

        result = run_mock_ensemble_session(
            state,
            spec,
            x0,
            session_id="test4",
        )
        self.assertLess(result.best_loss, loss_x0)
        self.assertGreater(result.evals, 0)
        truth_loss = landscape.loss_at_truth(spec.objective, held=False)
        self.assertLess(result.best_loss, truth_loss + 0.25)
        errors = [
            abs(result.final_values[var.id] - landscape.truth[var.id])
            for var in spec.variables
        ]
        self.assertLess(sum(errors) / len(errors), 0.45)
        self.assertLess(max(errors), 0.85)

    def test_invasive_release_required_for_low_loss(self) -> None:
        state = copy.deepcopy(self.fixture_runtime)
        payload = _invasive_lens_payload()
        spec, _, x0 = preflight_ensemble(state, payload)
        landscape = MockEnsembleLandscape(variables=spec.variables, x0=x0)
        physical = dict(x0)
        released = landscape.loss_at_truth(spec.objective, held=False)
        held_loss_meas = landscape.measurements_for_objective(
            physical, spec.objective, held=True
        )
        from lab_model.execution.optimization.metrics import evaluate_weighted_sum

        held_loss, _ = evaluate_weighted_sum(spec.objective, held_loss_meas)
        self.assertGreater(held_loss, released + 5.0)

        result = run_mock_ensemble_session(state, spec, x0, session_id="invasive")
        self.assertLess(result.best_loss, held_loss * 0.5)

    def test_continuous_block_keeps_arm_clear(self) -> None:
        state = copy.deepcopy(self.fixture_runtime)
        payload = _four_mirror_payload()
        spec, _, x0 = preflight_ensemble(state, payload)
        backend = build_mock_ensemble_backend(state, spec, x0)
        block = spec.solver.blocks[0]
        router = backend.router_for_block(block, block.variable_ids)
        self.assertIsInstance(router, MockActuatorRouter)
        router.enter_continuous_block(block.variable_ids)
        self.assertIn("enter_continuous_block", router.events)
        self.assertIn("arm_at_home", router.events)
        self.assertFalse(router.gripper_engaged)
        self.assertTrue(router.arm_at_home)
        router.apply_eval(x0, block_id=block.id)
        self.assertFalse(router.gripper_engaged)
        self.assertTrue(router.arm_at_home)
        self.assertNotIn("re_engage_on_demand", router.events)

    def test_run_optimize_ensemble_via_mock_host(self) -> None:
        state = copy.deepcopy(self.fixture_runtime)
        state["system_status"] = SYSTEM_STATUS_IDLE
        payload = _four_mirror_payload()
        lock = __import__("threading").RLock()

        host = MagicMock()
        host.log_prefix = "[MOCK-TEST]"
        host._state_lock = lock
        host.current_state = state
        host._catalog_meta_for_tag = lambda tag: {"tag": tag}
        host._persist_state = MagicMock()
        host._primitive_prepare_optimization_run = MagicMock(return_value=None)
        host._primitive_finalize_optimization_run = MagicMock()

        async def _mock_ensemble(**kwargs: Any) -> Dict[str, Any]:
            from mock_backend.host.ensemble import run_mock_ensemble_session

            spec_obj = kwargs["spec"]
            x0_map = kwargs["x0"]
            with lock:
                result = run_mock_ensemble_session(
                    state,
                    spec_obj,
                    x0_map,
                    session_id=kwargs["session_id"],
                    progress_callback=kwargs.get("progress_callback"),
                )
            return {
                "session_id": result.session_id,
                "best_loss": result.best_loss,
                "final_values": result.final_values,
            }

        host._primitive_run_ensemble_optimization = AsyncMock(side_effect=_mock_ensemble)

        asyncio.run(run_optimize_ensemble(host, "tag_20", payload))

        self.assertEqual(state["system_status"], SYSTEM_STATUS_IDLE)
        meas = state["components"]["tag_20"]["statecontrol"]["measurables"]
        self.assertIsNotNone(meas.get("last_optimization_score"))
        self.assertEqual(
            state["components"]["tag_20"]["statecontrol"]["tunables"]["placement"]["mode"],
            "ENSEMBLE",
        )


if __name__ == "__main__":
    unittest.main()
