"""Tests for approved-library TorchScript edge kernels."""
from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any, Dict

import numpy as np

from lab_model.execution.optimization.errors import EnsemblePreflightError
from lab_model.execution.optimization.kernels import get_kernel, list_kernels, validate_kernel_ids
from lab_model.execution.optimization.kernels.torchscript_runtime import (
    clear_torchscript_caches,
    preflight_torchscript_kernel,
    resolve_artifact_path,
    run_torchscript_scalar,
    torch_available,
)
from lab_model.execution.optimization.objective_measurements import (
    collect_objective_measurements,
    plan_objective_term,
)
from lab_model.execution.optimization.preflight import preflight_objective_term
from lab_model.execution.optimization.spec import ObjectiveTermSpec, OptimizeEnsembleParameters


_KERNEL_ID = "demo.image_mean_score"
_REPO = Path(__file__).resolve().parents[2]


@unittest.skipUnless(torch_available(), "PyTorch not installed")
class TorchScriptRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_torchscript_caches()

    def test_manifest_listed_in_registry(self) -> None:
        rows = list_kernels()
        ids = {k.id for k in rows}
        self.assertIn(_KERNEL_ID, ids)
        desc = get_kernel(_KERNEL_ID)
        assert desc is not None
        self.assertEqual(desc.runtime, "torchscript")
        self.assertTrue(desc.artifact_present)

    def test_validate_accepts_torchscript_id(self) -> None:
        out = validate_kernel_ids([_KERNEL_ID, "ensemble.eval.block_cobyla"])
        self.assertEqual(out[0], _KERNEL_ID)

    def test_path_escape_rejected(self) -> None:
        from lab_model.execution.optimization.kernels.torchscript_runtime import get_manifest_entry

        entry = dict(get_manifest_entry(_KERNEL_ID) or {})
        entry["artifact"] = "../secrets.pt"
        with self.assertRaises(FileNotFoundError):
            resolve_artifact_path(entry)

    def test_run_scalar_on_known_image(self) -> None:
        preflight_torchscript_kernel(_KERNEL_ID)
        bgr = np.full((32, 32, 3), 128, dtype=np.uint8)
        score = run_torchscript_scalar(_KERNEL_ID, bgr)
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 1.0)
        self.assertAlmostEqual(score, 128 / 255.0, delta=0.02)

    def test_collect_torchscript_term(self) -> None:
        term = ObjectiveTermSpec.model_validate(
            {
                "id": "ts_mean",
                "weight": 1.0,
                "source": {
                    "tag_id": "tag_22",
                    "kind": "torchscript_scalar",
                    "kernel_id": _KERNEL_ID,
                    "from": "measurables.camera_image",
                    "normalize": {"min": 0.0, "max": 1.0},
                },
                "metric": "one_minus_normalized",
            }
        )
        plan = plan_objective_term(term)
        self.assertEqual(plan.kind, "torchscript_scalar")
        self.assertEqual(plan.kernel_id, _KERNEL_ID)

        from lab_model.execution.optimization.spec import ObjectiveSpec

        objective = ObjectiveSpec(terms=[term])
        captured: list[str] = []

        def _bgr(tag: str):
            captured.append(tag)
            return np.full((40, 40, 3), 255, dtype=np.uint8)

        meas = collect_objective_measurements(
            objective,
            state={"components": {"tag_22": {"statecontrol": {"measurables": {}}}}},
            catalog_map={"tag_22": {"type": "OPTICAL_CAMERA"}},
            capture_bgr_for_tag=_bgr,
        )
        self.assertEqual(captured, ["tag_22"])
        self.assertAlmostEqual(meas["ts_mean"]["scalar"], 1.0, delta=0.02)

    def test_preflight_unknown_kernel(self) -> None:
        term = ObjectiveTermSpec.model_validate(
            {
                "id": "ts_bad",
                "weight": 1.0,
                "source": {
                    "tag_id": "tag_22",
                    "kind": "torchscript_scalar",
                    "kernel_id": "does.not.exist",
                    "from": "measurables.camera_image",
                    "normalize": {"min": 0.0, "max": 1.0},
                },
                "metric": "one_minus_normalized",
            }
        )
        state = {
            "components": {
                "tag_22": {"statecontrol": {"measurables": {"camera_image": {}}}},
            }
        }
        with self.assertRaises(EnsemblePreflightError):
            preflight_objective_term(state, term)


@unittest.skipUnless(torch_available(), "PyTorch not installed")
class TorchScriptMockEnsembleSmoke(unittest.TestCase):
    def test_mock_landscape_runs_torchscript_term(self) -> None:
        from mock_backend.host.ensemble import MockEnsembleLandscape
        from lab_model.execution.optimization.spec import ObjectiveSpec, VariableRef

        variables = [
            VariableRef.model_validate(
                {
                    "id": "v1",
                    "tag_id": "tag_20",
                    "path": "tunables.nominal_motor_positions.1",
                    "physical_type": "continuous",
                    "unit": "deg",
                    "bounds": {"min": -3.0, "max": 3.0},
                    "delta": True,
                }
            )
        ]
        objective = ObjectiveSpec.model_validate(
            {
                "type": "weighted_sum",
                "minimize": True,
                "terms": [
                    {
                        "id": "ts_mean",
                        "weight": 1.0,
                        "source": {
                            "tag_id": "tag_22",
                            "kind": "torchscript_scalar",
                            "kernel_id": _KERNEL_ID,
                            "from": "measurables.camera_image",
                            "normalize": {"min": 0.0, "max": 1.0},
                        },
                        "metric": "one_minus_normalized",
                    }
                ],
            }
        )
        landscape = MockEnsembleLandscape(
            variables=variables,
            x0={"v1": 0.0},
            target_px=(512.0, 384.0),
        )
        meas = landscape.measurements_for_objective(
            {"v1": 0.0},
            objective,
            held=False,
        )
        self.assertIn("ts_mean", meas)
        self.assertIn("scalar", meas["ts_mean"])
        score = float(meas["ts_mean"]["scalar"])
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 1.0)

    def test_ensemble_parameters_accept_kernel_id(self) -> None:
        payload: Dict[str, Any] = {
            "mode": "ensemble",
            "variables": [
                {
                    "id": "v1",
                    "tag_id": "tag_20",
                    "path": "tunables.nominal_motor_positions.1",
                    "physical_type": "continuous",
                    "unit": "deg",
                    "bounds": {"min": -1.0, "max": 1.0},
                    "delta": True,
                }
            ],
            "objective": {
                "type": "weighted_sum",
                "minimize": True,
                "terms": [
                    {
                        "id": "ts_mean",
                        "weight": 1.0,
                        "source": {
                            "tag_id": "tag_22",
                            "kind": "torchscript_scalar",
                            "kernel_id": _KERNEL_ID,
                            "from": "measurables.camera_image",
                            "normalize": {"min": 0.0, "max": 1.0},
                        },
                        "metric": "one_minus_normalized",
                    }
                ],
            },
            "solver": {
                "blocks": [{"id": "b1", "variable_ids": ["v1"], "max_evals": 3}],
            },
            "kernels": [_KERNEL_ID],
        }
        spec = OptimizeEnsembleParameters.model_validate(payload)
        self.assertEqual(spec.kernels, [_KERNEL_ID])
        self.assertEqual(spec.objective.terms[0].source.kernel_id, _KERNEL_ID)


if __name__ == "__main__":
    unittest.main()
