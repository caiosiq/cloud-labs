"""Phase 1: edge metrics + MeasurementInvalid policy."""
from __future__ import annotations

import math
import unittest

from cloudlabs_edge_dev.optimization.metrics import (
    MeasurementInvalid,
    evaluate_weighted_sum,
    get_metric,
    invalid_penalty,
    metric_minimize_value,
    metric_one_minus_normalized,
    metric_rms_distance,
    metric_squared_error,
)
from cloudlabs_edge_dev.optimization.spec import ObjectiveDecl, ObjectiveTerm


def _term(metric: str, **params) -> ObjectiveTerm:
    return ObjectiveTerm(
        id="t1",
        weight=1.0,
        metric=metric,
        measurable_path="measurables.x",
        params=params,
    )


class MetricTests(unittest.TestCase):
    def test_ratio_to_ref(self) -> None:
        from cloudlabs_edge_dev.optimization.metrics import metric_ratio_to_ref

        term = _term("ratio_to_ref", value_ref=100.0, feature_index=0)
        self.assertAlmostEqual(
            metric_ratio_to_ref({"features": [50.0]}, term), 2.0
        )
        self.assertAlmostEqual(
            metric_ratio_to_ref({"features": [200.0]}, term), 0.5
        )

    def test_ratio_from_ref(self) -> None:
        from cloudlabs_edge_dev.optimization.metrics import metric_ratio_from_ref

        term = _term("ratio_from_ref", value_ref=100.0, feature_index=0)
        self.assertAlmostEqual(
            metric_ratio_from_ref({"features": [50.0]}, term), 0.5
        )
        self.assertAlmostEqual(
            metric_ratio_from_ref({"features": [200.0]}, term), 2.0
        )

    def test_ratio_to_ref_missing_raises(self) -> None:
        from cloudlabs_edge_dev.optimization.metrics import metric_ratio_to_ref

        term = _term("ratio_to_ref")
        with self.assertRaises(MeasurementInvalid):
            metric_ratio_to_ref({"features": [1.0]}, term)

    def test_one_minus_normalized(self) -> None:
        term = _term("one_minus_normalized", normalize={"min": 0.0, "max": 1.0})
        self.assertAlmostEqual(
            metric_one_minus_normalized({"scalar": 0.25}, term), 0.75
        )

    def test_one_minus_missing_raises(self) -> None:
        term = _term("one_minus_normalized")
        with self.assertRaises(MeasurementInvalid):
            metric_one_minus_normalized({}, term)

    def test_squared_error(self) -> None:
        term = _term("squared_error", target_scalar=2.0)
        self.assertAlmostEqual(metric_squared_error({"scalar": 5.0}, term), 9.0)

    def test_rms_distance(self) -> None:
        term = _term(
            "rms_distance",
            feature_index=[0, 1],
            target=[0.0, 0.0],
            rms_scale_px=1.0,
            loss_cap=1.0e6,
        )
        self.assertAlmostEqual(
            metric_rms_distance({"features": [3.0, 4.0]}, term), 5.0
        )

    def test_rms_distance_normalized_default_cap(self) -> None:
        term = _term(
            "rms_distance",
            feature_index=[0, 1],
            target=[0.0, 0.0],
            rms_scale_px=10.0,
            loss_cap=2.0,
        )
        # 5 px / 10 = 0.5
        self.assertAlmostEqual(
            metric_rms_distance({"features": [3.0, 4.0]}, term), 0.5
        )

    def test_rms_empty_features_raises(self) -> None:
        term = _term("rms_distance", target=[0.0, 0.0])
        with self.assertRaises(MeasurementInvalid):
            metric_rms_distance({"features": []}, term)
        with self.assertRaises(MeasurementInvalid):
            metric_rms_distance({}, term)

    def test_minimize_value_abs(self) -> None:
        term = _term("minimize_value", feature_index=0, use_abs=True)
        self.assertAlmostEqual(
            metric_minimize_value({"features": [-2.5]}, term), 2.5
        )

    def test_nan_raises(self) -> None:
        term = _term("one_minus_normalized")
        with self.assertRaises(MeasurementInvalid):
            metric_one_minus_normalized({"scalar": float("nan")}, term)

    def test_weighted_sum_penalty_on_invalid(self) -> None:
        obj = ObjectiveDecl(
            terms=[
                ObjectiveTerm(
                    id="bad",
                    weight=2.0,
                    metric="rms_distance",
                    measurable_path="measurables.x",
                    params={"target": [0.0, 0.0]},
                )
            ]
        )
        loss, terms = evaluate_weighted_sum(obj, {}, on_invalid="penalty")
        self.assertTrue(math.isfinite(loss))
        self.assertGreater(loss, 0.0)
        self.assertAlmostEqual(terms["bad"], invalid_penalty(obj.terms[0]))

    def test_weighted_sum_raise(self) -> None:
        obj = ObjectiveDecl(
            terms=[
                ObjectiveTerm(
                    id="bad",
                    weight=1.0,
                    metric="rms_distance",
                    measurable_path="measurables.x",
                    params={"target": [0.0, 0.0]},
                )
            ]
        )
        with self.assertRaises(MeasurementInvalid):
            evaluate_weighted_sum(obj, {}, on_invalid="raise")

    def test_registry_aliases(self) -> None:
        self.assertIsNotNone(get_metric("rms_distance"))
        self.assertIs(get_metric("rms_distance_px"), get_metric("rms_distance"))


if __name__ == "__main__":
    unittest.main()
