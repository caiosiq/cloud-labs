"""Phase 6.4: same pipeline JSON is valid across mock / sim / real edge catalogs."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

import numpy as np

from cloudlabs_edge_dev.optimization.capture import StaticCaptureSource
from cloudlabs_edge_dev.optimization.router import RecordingRouter
from cloudlabs_edge_dev.optimization.session import run_optimization_session
from cloudlabs_edge_dev.optimization.spec import parse_pipeline
from cloudlabs_edge_dev.optimization.tensors import camera_bgr_tensor
from lab_model.execution.optimization.pipeline import compile_pipeline
from lab_model.execution.optimization.spec import OptimizeEnsembleParameters


_REPO = Path(__file__).resolve().parents[2]
_EDGE_KERNEL_DIRS = [
    ("mock", _REPO / "mock_backend" / "cloudlabs_edge" / "kernels"),
    ("sim", _REPO / "simulation_edge" / "cloudlabs_edge" / "kernels"),
    ("real", _REPO.parent / "lab_automation" / "cloudlabs_edge" / "kernels"),
]

_REQUIRED_BUILTINS = {
    "builtin.roi_centroid",
    "builtin.beam_power",
    "builtin.gaussian_beam_fit",
    "builtin.beam_shift",
}


def _golden_pipeline() -> dict:
    return {
        "schema_version": 1,
        "session_label": "parity-two-mirror-power",
        "variables": [
            {
                "id": "v_m20_m1",
                "tag_id": "tag_20",
                "actuator": {
                    "kind": "motor",
                    "controller": "wifi_stepper1",
                    "motor_id": 1,
                },
                "physical_type": "continuous",
                "unit": "deg",
                "bounds": {"min": -1.0, "max": 1.0},
                "delta": True,
            },
            {
                "id": "v_m18_m1",
                "tag_id": "tag_18",
                "actuator": {
                    "kind": "motor",
                    "controller": "wifi_stepper1",
                    "motor_id": 1,
                },
                "physical_type": "continuous",
                "unit": "deg",
                "bounds": {"min": -1.0, "max": 1.0},
                "delta": True,
            },
        ],
        "capture": [],
        "objective": {
            "type": "weighted_sum",
            "minimize": True,
            "terms": [
                {
                    "id": "power",
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
            "max_total_evals": 4,
            "keep_best": True,
            "rollback_on_fail": False,
            "constraints": [
                {
                    "type": "max_delta_from_start",
                    "enabled": True,
                    "limits": {"deg": 1.0, "mm": 5.0},
                }
            ],
            "blocks": [
                {
                    "id": "mirrors",
                    "variable_ids": ["v_m20_m1", "v_m18_m1"],
                    "max_evals": 4,
                    "passes": 1,
                    "rhobeg_u": 0.2,
                    "rhoend_u": 0.05,
                }
            ],
        },
    }


class PipelineParityTests(unittest.TestCase):
    def test_edge_catalogs_share_builtins(self) -> None:
        for name, path in _EDGE_KERNEL_DIRS:
            if not path.is_dir():
                self.skipTest(f"{name} kernels dir missing: {path}")
            manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
            ids = {row["id"] for row in manifest.get("kernels") or []}
            missing = _REQUIRED_BUILTINS - ids
            self.assertFalse(missing, f"{name} missing builtins: {missing}")
            for kid in _REQUIRED_BUILTINS:
                row = next(r for r in manifest["kernels"] if r["id"] == kid)
                art = path / str(row["artifact"])
                self.assertTrue(art.is_file(), f"{name} missing artifact {art}")

    def test_golden_pipeline_parses_and_runs(self) -> None:
        raw = _golden_pipeline()
        spec = parse_pipeline(raw)
        self.assertEqual(spec.schema_version, 1)

        class _Cap(StaticCaptureSource):
            def __init__(self) -> None:
                frame = camera_bgr_tensor(
                    np.zeros((8, 8, 3), dtype=np.uint8), tag_id="tag_20"
                )
                super().__init__({"cam": frame})
                self.applied = {"v_m20_m1": 0.0, "v_m18_m1": 0.0}

            def read_measurable(self, path: str, *, tag_id: str) -> float:
                # Prefer both mirrors near 0.3
                err = sum((float(self.applied[k]) - 0.3) ** 2 for k in self.applied)
                return 1.0 / (1.0 + err)

        class _R(RecordingRouter):
            def __init__(self, cap: _Cap) -> None:
                super().__init__()
                self.cap = cap

            def apply_eval(self, physical_values, *, block_id: str) -> None:  # noqa: ANN001
                super().apply_eval(physical_values, block_id=block_id)
                self.cap.applied = dict(physical_values)

        cap = _Cap()
        router = _R(cap)
        result = run_optimization_session(
            raw,
            {"v_m20_m1": 0.0, "v_m18_m1": 0.0},
            capture=cap,
            router=router,
        )
        self.assertGreater(result.evals, 0)
        self.assertFalse(result.aborted)
        self.assertIn("v_m20_m1", result.final_values)

    def test_ensemble_ir_compiles_to_same_shape(self) -> None:
        """Coordinator compile_pipeline accepts the two-mirror example shape."""
        example = _REPO / "schemas" / "ensemble_optimization_examples" / "two_mirror_mock.json"
        if not example.is_file():
            self.skipTest("two_mirror_mock.json missing")
        payload = json.loads(example.read_text(encoding="utf-8"))
        params = dict(payload.get("parameters") or {})
        # Ensure Phase 6 constraints ride along.
        solver = dict(params.get("solver") or {})
        solver.setdefault(
            "constraints",
            [{"type": "max_delta_from_start", "enabled": True, "limits": {"deg": 3.0}}],
        )
        params["solver"] = solver
        # Strip fields compile doesn't need from example if any.
        catalog = {
            "tag_20": {"motor_controller": "wifi_stepper1"},
            "tag_18": {"motor_controller": "wifi_stepper1"},
        }
        # Validate ensemble IR first.
        OptimizeEnsembleParameters.model_validate(params)
        pipeline = compile_pipeline(params, catalog)
        self.assertEqual(pipeline.get("schema_version"), 1)
        self.assertGreaterEqual(len(pipeline.get("variables") or []), 2)
        parse_pipeline(pipeline)


if __name__ == "__main__":
    unittest.main()
