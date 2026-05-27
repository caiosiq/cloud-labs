"""OPTIMIZE param normalization and loss-metric allow-list."""
from __future__ import annotations

import unittest

from lab_model.catalog.schema import catalog_optimize_strategies
from lab_model.orchestration.optimize_params import (
    DEFAULT_AXIS,
    DEFAULT_TOLERANCE_RATIO,
    DEFAULT_VIDEO_EXPOSURE,
    allowed_loss_metrics,
    normalize_optimize_params,
)
from lab_model.orchestration.optimize_policy import refuse_if_loss_metric_not_allowed


def _motor_row() -> dict:
    return {
        "tag_id": "tag_2",
        "motor_ids": [1, 3],
        "capabilities": {},
    }


class TestOptimizeParams(unittest.TestCase):
    def test_newton_defaults_and_axis_clamp(self) -> None:
        out = normalize_optimize_params(None, "tag_9", "NEWTON", {})
        self.assertEqual(out["strategy"], "NEWTON")
        self.assertEqual(out["axis"], DEFAULT_AXIS)
        self.assertAlmostEqual(out["tolerance_ratio"], DEFAULT_TOLERANCE_RATIO)
        self.assertAlmostEqual(out["video_exposure"], DEFAULT_VIDEO_EXPOSURE)
        self.assertEqual(out["loss_metric"], "centroid_match")

        bad_axis = normalize_optimize_params(
            None, "tag_9", "NEWTON", {"axis": "z", "tolerance_ratio": 2.0}
        )
        self.assertEqual(bad_axis["axis"], DEFAULT_AXIS)
        self.assertAlmostEqual(bad_axis["tolerance_ratio"], 0.5)

    def test_cobyla_strips_newton_fields_and_adds_motors(self) -> None:
        row = _motor_row()
        out = normalize_optimize_params(
            row,
            "tag_2",
            "COBYLA",
            {"sensor_component": "tag_22", "axis": "x", "tolerance_ratio": 0.1},
        )
        self.assertEqual(out["strategy"], "COBYLA")
        self.assertNotIn("axis", out)
        self.assertNotIn("tolerance_ratio", out)
        self.assertEqual(out["motor_ids"], [1, 3])
        self.assertEqual(out["loss_metric"], "reference_match")

    def test_video_exposure_alias_and_clamp(self) -> None:
        out = normalize_optimize_params(
            None, "tag_9", "NEWTON", {"exposure": 0.5}
        )
        self.assertAlmostEqual(out["video_exposure"], 0.5)
        self.assertNotIn("exposure", out)

        clamped = normalize_optimize_params(
            None, "tag_9", "NEWTON", {"video_exposure": 999.0}
        )
        self.assertAlmostEqual(clamped["video_exposure"], 30.0)

    def test_invalid_loss_metric_marked_for_refusal(self) -> None:
        row = _motor_row()
        strategies = catalog_optimize_strategies(row)
        self.assertIn("reference_match", allowed_loss_metrics(row, "COBYLA"))
        self.assertNotIn("centroid_match", allowed_loss_metrics(row, "COBYLA"))

        out = normalize_optimize_params(
            row, "tag_2", "COBYLA", {"loss_metric": "centroid_match"}
        )
        self.assertEqual(out["_loss_metric_invalid"], "centroid_match")
        refusal = refuse_if_loss_metric_not_allowed(row, "COBYLA", out)
        self.assertTrue(refusal)
        self.assertIn("centroid_match", refusal.reason or "")


if __name__ == "__main__":
    unittest.main()
