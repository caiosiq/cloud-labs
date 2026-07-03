"""Phase 1 tests: ensemble spec, path resolver, normalization, pre-flight."""
from __future__ import annotations

import asyncio
import copy
import json
import os
import unittest
from pathlib import Path
from typing import Any, Dict, Optional
from unittest.mock import AsyncMock, MagicMock

from pydantic import ValidationError

from lab_communicator.shared.lab_view_config import bootstrap_lab_view
from lab_model.domain.holding import SYSTEM_STATUS_IDLE, SYSTEM_STATUS_OPTIMIZING
from lab_model.optimization.errors import EnsemblePreflightError, PathResolveError
from lab_model.optimization.normalize import NormalizedSearchSpace
from lab_model.optimization.paths import VariablePathResolver, parse_variable_path
from lab_model.optimization.preflight import preflight_ensemble
from lab_model.optimization.presets import compile_legacy_strategy
from lab_model.optimization.spec import OptimizeEnsembleParameters, VariableRef
from lab_model.orchestration.optimize_ensemble import run_optimize_ensemble
from lab_model.state.commits import commit_optimization_ensemble_complete

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_MOCK_LAB_VIEW = _PROJECT_ROOT / "backend" / "lab_communicator" / "mock" / "lab_view"
_LAB_STATE = _MOCK_LAB_VIEW / "lab_state.json"


def _minimal_ensemble_payload(**overrides: Any) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "mode": "ensemble",
        "session_label": "test",
        "variables": [
            {
                "id": "v_m1",
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
                    "id": "score",
                    "weight": 1.0,
                    "source": {
                        "tag_id": "tag_20",
                        "kind": "measurable_scalar",
                        "path": "measurables.last_optimization_score",
                    },
                    "metric": "one_minus_normalized",
                }
            ],
        },
        "solver": {
            "type": "block_cobyla",
            "blocks": [
                {
                    "id": "block_a",
                    "variable_ids": ["v_m1"],
                    "max_evals": 5,
                }
            ],
        },
    }
    base.update(overrides)
    return base


class EnsembleOptimizationSpecTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ["LAB_VIEW_PATH"] = str(_MOCK_LAB_VIEW)
        bootstrap_lab_view(str(_PROJECT_ROOT))
        with open(_LAB_STATE, "r", encoding="utf-8") as handle:
            cls.fixture_runtime = json.load(handle)

    def test_invasive_requires_touch_and_go(self) -> None:
        with self.assertRaises(ValidationError):
            VariableRef(
                id="v_y",
                tag_id="tag_lens",
                path="tunables.nominal_pose.y",
                physical_type="invasive_discrete",
                unit="mm",
                bounds={"min": -10.0, "max": 10.0},
            )

    def test_solver_unknown_variable_id_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            OptimizeEnsembleParameters.model_validate(
                _minimal_ensemble_payload(
                    solver={
                        "type": "block_cobyla",
                        "blocks": [
                            {
                                "id": "bad",
                                "variable_ids": ["missing_id"],
                                "max_evals": 3,
                            }
                        ],
                    }
                )
            )

    def test_parse_variable_path_allowlist(self) -> None:
        parsed = parse_variable_path("tunables.nominal_motor_positions.3")
        self.assertEqual(parsed.kind, "motor")
        self.assertEqual(parsed.motor_id, "3")

        pose = parse_variable_path("tunables.nominal_pose.rotation")
        self.assertEqual(pose.kind, "pose_axis")
        self.assertEqual(pose.axis, "rotation")

    def test_path_resolver_typo_fails_preflight(self) -> None:
        payload = _minimal_ensemble_payload(
            variables=[
                {
                    "id": "v_bad",
                    "tag_id": "tag_20",
                    "path": "tunable.nominal_pose.y",
                    "physical_type": "continuous",
                    "unit": "mm",
                    "bounds": {"min": -1.0, "max": 1.0},
                }
            ],
            solver={
                "type": "block_cobyla",
                "blocks": [{"id": "b", "variable_ids": ["v_bad"], "max_evals": 3}],
            },
        )
        with self.assertRaises(EnsemblePreflightError) as ctx:
            preflight_ensemble(self.fixture_runtime, payload)
        self.assertTrue(ctx.exception.errors)

    def test_path_resolver_missing_motor_index(self) -> None:
        payload = _minimal_ensemble_payload(
            variables=[
                {
                    "id": "v_missing",
                    "tag_id": "tag_20",
                    "path": "tunables.nominal_motor_positions.99",
                    "physical_type": "continuous",
                    "unit": "deg",
                    "bounds": {"min": -1.0, "max": 1.0},
                }
            ],
            solver={
                "type": "block_cobyla",
                "blocks": [{"id": "b", "variable_ids": ["v_missing"], "max_evals": 3}],
            },
        )
        with self.assertRaises(EnsemblePreflightError):
            preflight_ensemble(self.fixture_runtime, payload)

    def test_path_resolver_missing_tag(self) -> None:
        payload = _minimal_ensemble_payload()
        payload["variables"][0]["tag_id"] = "tag_does_not_exist"
        with self.assertRaises(EnsemblePreflightError):
            preflight_ensemble(self.fixture_runtime, payload)

    def test_path_resolver_success_on_fixture(self) -> None:
        spec, resolver, x0 = preflight_ensemble(
            self.fixture_runtime, _minimal_ensemble_payload()
        )
        self.assertEqual(spec.variables[0].id, "v_m1")
        self.assertIn("v_m1", x0)
        self.assertIsInstance(resolver.get("v_m1"), float)

    def test_preflight_ignores_legacy_strategy_default(self) -> None:
        """``OptimizeParameters`` injects strategy=NEWTON; ensemble schema forbids it."""
        payload = _minimal_ensemble_payload()
        payload["strategy"] = "NEWTON"
        spec, _, x0 = preflight_ensemble(self.fixture_runtime, payload)
        self.assertEqual(spec.mode, "ensemble")
        self.assertIn("v_m1", x0)

    def test_normalized_search_space_delta_round_trip(self) -> None:
        spec, _, x0 = preflight_ensemble(
            self.fixture_runtime, _minimal_ensemble_payload()
        )
        space = NormalizedSearchSpace(spec.variables, x0)
        lo, hi = space.physical_bounds("v_m1")
        start = x0["v_m1"]
        self.assertAlmostEqual(lo, start - 3.0)
        self.assertAlmostEqual(hi, start + 3.0)

        u = space.normalize_dict({"v_m1": start})
        self.assertAlmostEqual(u[0], 0.5)
        back = space.denormalize_list(u)
        self.assertAlmostEqual(back["v_m1"], start)

        at_max = space.denormalize_list([1.0])
        self.assertAlmostEqual(at_max["v_m1"], hi)

    def test_commit_optimization_ensemble_complete_writes_motor(self) -> None:
        state = copy.deepcopy(self.fixture_runtime)
        spec, _, x0 = preflight_ensemble(state, _minimal_ensemble_payload())
        start = x0["v_m1"]
        target = start + 1.25
        commit_optimization_ensemble_complete(
            state,
            spec=spec,
            session_id="testsession",
            final_values={"v_m1": target},
            best_loss=0.42,
        )
        motors = (
            state["components"]["tag_20"]["statecontrol"]["tunables"][
                "nominal_motor_positions"
            ]
        )
        self.assertAlmostEqual(float(motors["1"]), target)
        meas = state["components"]["tag_20"]["statecontrol"]["measurables"]
        self.assertAlmostEqual(float(meas["last_optimization_score"]), 0.42)
        self.assertEqual(
            state["components"]["tag_20"]["statecontrol"]["tunables"]["placement"][
                "mode"
            ],
            "ENSEMBLE",
        )

    def test_compile_legacy_strategy_shape(self) -> None:
        compiled = compile_legacy_strategy(
            target_id="tag_20", strategy="COBYLA", motor_ids=[1, 3]
        )
        self.assertEqual(compiled["mode"], "ensemble")
        self.assertEqual(len(compiled["variables"]), 2)
        preflight_ensemble(self.fixture_runtime, compiled)

    def test_run_optimize_ensemble_stub_enters_and_exits_optimizing(self) -> None:
        state = copy.deepcopy(self.fixture_runtime)
        state["system_status"] = SYSTEM_STATUS_IDLE
        lock = __import__("threading").RLock()

        host = MagicMock()
        host.log_prefix = "[TEST]"
        host._state_lock = lock
        host.current_state = state
        host._catalog_meta_for_tag = lambda tag: {"tag": tag}
        host._persist_state = MagicMock()
        host._primitive_prepare_optimization_run = MagicMock(return_value=None)
        host._primitive_finalize_optimization_run = MagicMock()

        host._primitive_run_ensemble_optimization = AsyncMock(return_value=None)

        asyncio.run(
            run_optimize_ensemble(host, "tag_20", _minimal_ensemble_payload())
        )

        self.assertEqual(host.current_state["system_status"], SYSTEM_STATUS_IDLE)
        self.assertNotIn("optimization_session", host.current_state)
        host._primitive_run_ensemble_optimization.assert_called_once()

    def test_run_optimize_ensemble_commits_on_result(self) -> None:
        state = copy.deepcopy(self.fixture_runtime)
        state["system_status"] = SYSTEM_STATUS_IDLE
        lock = __import__("threading").RLock()
        spec_payload = _minimal_ensemble_payload()
        _, _, x0 = preflight_ensemble(state, spec_payload)
        new_val = x0["v_m1"] + 0.5

        host = MagicMock()
        host.log_prefix = "[TEST]"
        host._state_lock = lock
        host.current_state = state
        host._catalog_meta_for_tag = lambda tag: {"tag": tag}
        host._persist_state = MagicMock()
        host._primitive_prepare_optimization_run = MagicMock(return_value="run_dir")
        host._primitive_finalize_optimization_run = MagicMock()

        async def _return_result(**kwargs: Any) -> Dict[str, Any]:
            return {
                "session_id": kwargs["session_id"],
                "best_loss": 0.11,
                "final_values": {"v_m1": new_val},
            }

        host._primitive_run_ensemble_optimization = AsyncMock(side_effect=_return_result)

        asyncio.run(run_optimize_ensemble(host, "tag_20", spec_payload))

        motors = state["components"]["tag_20"]["statecontrol"]["tunables"][
            "nominal_motor_positions"
        ]
        self.assertAlmostEqual(float(motors["1"]), new_val)

    def test_parse_variable_path_invalid_raises(self) -> None:
        with self.assertRaises(PathResolveError):
            parse_variable_path("components.foo.bar")


if __name__ == "__main__":
    unittest.main()
