"""Phase E tests: objective graph compiler and objective preflight."""
from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from typing import Any, Dict

from lab_model.coordinator.backends.lab_view_config import bootstrap_lab_view
from lab_model.execution.optimization.compiler import compile_objective_graph, compile_objective_payload
from lab_model.execution.optimization.errors import EnsemblePreflightError
from lab_model.execution.optimization.graph import ObjectiveGraphSpec
from lab_model.execution.optimization.metrics import weighted_sum as _metrics  # noqa: F401 â€” register
from lab_model.execution.optimization.preflight import preflight_ensemble, preflight_objective_sources
from cloudlabs.objective import ObjectiveGraphBuilder, objective_term
from lab_model.execution.optimization.spec import ObjectiveSpec

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_MOCK_LAB_VIEW = _PROJECT_ROOT / "mock_edge" / "lab_view"
_LAB_STATE = _MOCK_LAB_VIEW / "lab_state.json"
_GRAPH_EXAMPLE = (
    _PROJECT_ROOT
    / "schemas"
    / "objective_graph_examples"
    / "two_term_mock.json"
)
_ENSEMBLE_EXAMPLE = (
    _PROJECT_ROOT
    / "schemas"
    / "ensemble_optimization_examples"
    / "two_mirror_mock.json"
)


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


class ObjectiveCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ["LAB_VIEW_PATH"] = str(_MOCK_LAB_VIEW)
        bootstrap_lab_view(str(_PROJECT_ROOT))
        with open(_LAB_STATE, "r", encoding="utf-8") as handle:
            cls.fixture_runtime = json.load(handle)
        with open(_GRAPH_EXAMPLE, "r", encoding="utf-8") as handle:
            cls.graph_example = json.load(handle)
        with open(_ENSEMBLE_EXAMPLE, "r", encoding="utf-8") as handle:
            cls.ensemble_example = json.load(handle)

    def test_compile_camera_centroid_term(self) -> None:
        graph = ObjectiveGraphSpec(
            terms=[
                {
                    "id": "cam",
                    "tag_id": "tag_22",
                    "field": "camera_image",
                    "weight": 1.0,
                    "target_px": {"x": 100.0, "y": 200.0},
                }
            ]
        )
        compiled = compile_objective_graph(graph)
        term = compiled.terms[0]
        self.assertEqual(term.metric, "rms_distance_px")
        self.assertEqual(term.source.kind, "derived_centroid")
        self.assertEqual(term.source.from_, "measurables.camera_image")
        self.assertEqual(term.source.target_px, {"x": 100.0, "y": 200.0})

    def test_compile_scalar_term(self) -> None:
        graph = ObjectiveGraphSpec(
            terms=[
                {
                    "id": "pwr",
                    "tag_id": "tag_20",
                    "field": "output_power_readback_mw",
                    "weight": 0.5,
                }
            ]
        )
        compiled = compile_objective_graph(graph)
        term = compiled.terms[0]
        self.assertEqual(term.metric, "one_minus_normalized")
        self.assertEqual(term.source.kind, "measurable_scalar")
        self.assertEqual(term.source.path, "measurables.output_power_readback_mw")

    def test_graph_example_matches_ensemble_objective_shape(self) -> None:
        compiled = compile_objective_payload(self.graph_example)
        expected = self.ensemble_example["parameters"]["objective"]
        # Power term in ensemble example uses last_optimization_score â€” graph uses output_power.
        self.assertEqual(compiled["type"], expected["type"])
        centroid = compiled["terms"][0]
        self.assertEqual(centroid["metric"], "rms_distance_px")
        self.assertEqual(centroid["source"]["kind"], "derived_centroid")

    def test_sdk_builder_compiles(self) -> None:
        built = (
            ObjectiveGraphBuilder()
            .term(
                term_id="score",
                tag_id="tag_20",
                field="last_optimization_score",
                weight=1.0,
                metric="one_minus_normalized",
            )
            .compile()
        )
        self.assertEqual(built["terms"][0]["source"]["path"], "measurables.last_optimization_score")

    def test_objective_term_helper(self) -> None:
        raw = objective_term(
            term_id="t1",
            tag_id="tag_20",
            field="camera_image",
            target_px={"x": 1.0, "y": 2.0},
        )
        compiled = compile_objective_payload(
            {"version": 1, "type": "weighted_sum", "minimize": True, "terms": [raw]}
        )
        self.assertEqual(compiled["terms"][0]["metric"], "rms_distance_px")

    def test_preflight_rejects_unknown_metric(self) -> None:
        payload = _minimal_ensemble_payload()
        payload["objective"]["terms"][0]["metric"] = "not_a_metric"
        with self.assertRaises(EnsemblePreflightError) as ctx:
            preflight_ensemble(self.fixture_runtime, payload)
        self.assertIn("unknown metric", ctx.exception.message)

    def test_preflight_rejects_missing_objective_tag(self) -> None:
        payload = _minimal_ensemble_payload()
        payload["objective"]["terms"][0]["source"]["tag_id"] = "tag_missing"
        with self.assertRaises(EnsemblePreflightError):
            preflight_ensemble(self.fixture_runtime, payload)

    def test_preflight_rejects_bad_measurable_path(self) -> None:
        payload = _minimal_ensemble_payload()
        payload["objective"]["terms"][0]["source"]["path"] = "measurables.not_on_component"
        with self.assertRaises(EnsemblePreflightError):
            preflight_ensemble(self.fixture_runtime, payload)

    def test_preflight_objective_sources_on_compiled_graph(self) -> None:
        compiled = compile_objective_graph(ObjectiveGraphSpec.model_validate(self.graph_example))
        preflight_objective_sources(self.fixture_runtime, compiled)

    def test_runtime_objective_passthrough(self) -> None:
        runtime = self.ensemble_example["parameters"]["objective"]
        out = compile_objective_payload(runtime)
        self.assertEqual(out["terms"][0]["id"], runtime["terms"][0]["id"])


if __name__ == "__main__":
    unittest.main()
