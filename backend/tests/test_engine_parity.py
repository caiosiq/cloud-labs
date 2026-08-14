"""Phase 1: coordinator vs edge metric parity on identical inputs."""
from __future__ import annotations

import unittest

from cloudlabs_edge_dev.optimization.metrics import evaluate_weighted_sum as edge_eval
from cloudlabs_edge_dev.optimization.spec import ObjectiveDecl, ObjectiveTerm
from lab_model.execution.optimization.metrics.weighted_sum import (
    evaluate_weighted_sum as coord_eval,
)
from lab_model.execution.optimization.spec import (
    BoundsSpec,
    ObjectiveSourceSpec,
    ObjectiveSpec,
    ObjectiveTermSpec,
)


class EngineParityTests(unittest.TestCase):
    def test_one_minus_and_squared_error_match(self) -> None:
        # Coordinator IR
        coord_obj = ObjectiveSpec(
            minimize=True,
            terms=[
                ObjectiveTermSpec(
                    id="power",
                    weight=0.4,
                    metric="one_minus_normalized",
                    source=ObjectiveSourceSpec(
                        tag_id="tag_20",
                        kind="measurable_scalar",
                        path="measurables.last_optimization_score",
                        normalize=BoundsSpec(min=0.0, max=1.0),
                    ),
                ),
                ObjectiveTermSpec(
                    id="match",
                    weight=1.0,
                    metric="squared_error",
                    source=ObjectiveSourceSpec(
                        tag_id="tag_22",
                        kind="torchscript_scalar",
                        kernel_id="demo.roi_mean_score",
                        target_scalar=0.5,
                    ),
                ),
            ],
        )
        measurements = {
            "power": {"scalar": 0.8},
            "match": {"scalar": 0.7},
        }
        c_loss, c_terms = coord_eval(coord_obj, measurements)

        # Edge IR (params carry normalize / target_scalar)
        edge_obj = ObjectiveDecl(
            minimize=True,
            terms=[
                ObjectiveTerm(
                    id="power",
                    weight=0.4,
                    metric="one_minus_normalized",
                    measurable_path="measurables.last_optimization_score",
                    params={"normalize": {"min": 0.0, "max": 1.0}},
                ),
                ObjectiveTerm(
                    id="match",
                    weight=1.0,
                    metric="squared_error",
                    measurable_path="measurables.score",
                    params={"target_scalar": 0.5},
                ),
            ],
        )
        e_loss, e_terms = edge_eval(edge_obj, measurements, on_invalid="raise")
        self.assertAlmostEqual(c_loss, e_loss)
        self.assertAlmostEqual(c_terms["power"], e_terms["power"])
        self.assertAlmostEqual(c_terms["match"], e_terms["match"])

    def test_rms_distance_features_match(self) -> None:
        coord_obj = ObjectiveSpec(
            terms=[
                ObjectiveTermSpec(
                    id="center",
                    weight=1.0,
                    metric="rms_distance",
                    source=ObjectiveSourceSpec.model_validate(
                        {
                            "tag_id": "tag_22",
                            "kind": "torchscript_features",
                            "kernel_id": "builtin.roi_centroid",
                            "feature_index": [0, 1],
                            "target_px": {"x": 10.0, "y": 20.0},
                            "rms_scale_px": 1.0,
                            "loss_cap": 1.0e6,
                        }
                    ),
                )
            ]
        )
        measurements = {"center": {"features": [13.0, 24.0]}}
        c_loss, _ = coord_eval(coord_obj, measurements)

        edge_obj = ObjectiveDecl(
            terms=[
                ObjectiveTerm(
                    id="center",
                    weight=1.0,
                    metric="rms_distance",
                    kernel_id="builtin.roi_centroid",
                    capture_id="cam",
                    params={
                        "feature_index": [0, 1],
                        "target": [10.0, 20.0],
                        "rms_scale_px": 1.0,
                        "loss_cap": 1.0e6,
                    },
                )
            ]
        )
        e_loss, _ = edge_eval(edge_obj, measurements, on_invalid="raise")
        self.assertAlmostEqual(c_loss, e_loss)
        self.assertAlmostEqual(c_loss, 5.0)


if __name__ == "__main__":
    unittest.main()
