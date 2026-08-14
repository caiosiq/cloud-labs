"""Normalized spatial losses + beam-presence latch for OPTIMIZE."""
from __future__ import annotations

import math
import unittest

from cloudlabs_edge_dev.optimization.metrics import (
    DEFAULT_INVALID_PENALTY,
    evaluate_weighted_sum,
    metric_beam_presence,
    metric_minimize_value,
    metric_rms_distance,
    normalize_spatial_loss,
)
from cloudlabs_edge_dev.optimization.presence import apply_peak_presence_gate
from cloudlabs_edge_dev.optimization.spec import ObjectiveDecl, ObjectiveTerm


def _rms_term(**params):
    return ObjectiveTerm(
        id="align",
        weight=1.0,
        metric="rms_distance",
        capture_id="cap_1",
        kernel_id="builtin.roi_centroid",
        params={
            "feature_index": [0, 1],
            "target": [50.0, 50.0],
            "latch_peak_ref": True,
            "min_peak_ratio": 0.5,
            "peak_feature_index": 2,
            "rms_scale_px": 100.0,
            "loss_cap": 2.0,
            **params,
        },
    )


class NormalizedLossTests(unittest.TestCase):
    def test_rms_normalized_by_scale(self) -> None:
        term = _rms_term()
        # 50 px error / 100 scale = 0.5
        loss = metric_rms_distance({"features": [50.0, 100.0, 1.0]}, term)
        self.assertAlmostEqual(loss, 0.5, places=5)

    def test_rms_soft_cap(self) -> None:
        term = _rms_term()
        # Far miss: 500 px / 100 = 5 → capped at 2
        loss = metric_rms_distance({"features": [550.0, 50.0, 1.0]}, term)
        self.assertAlmostEqual(loss, 2.0, places=5)

    def test_rms_uses_fov_diagonal_from_frame_hw(self) -> None:
        term = ObjectiveTerm(
            id="align",
            weight=1.0,
            metric="rms_distance",
            capture_id="cap_1",
            kernel_id="builtin.roi_centroid",
            params={"feature_index": [0, 1], "target": [0.0, 0.0], "loss_cap": 2.0},
        )
        # frame 30x40 → diag=50; point at (30,40) → rms=50 → loss=1
        loss = metric_rms_distance(
            {"features": [30.0, 40.0, 1.0], "frame_hw": [30, 40]},
            term,
        )
        self.assertAlmostEqual(loss, 1.0, places=5)

    def test_value_ref_latch_for_ratio_to_ref(self) -> None:
        from cloudlabs_edge_dev.optimization.metrics import metric_ratio_to_ref
        from cloudlabs_edge_dev.optimization.value_ref import apply_value_ref_latch

        term = ObjectiveTerm(
            id="power",
            weight=1.0,
            metric="ratio_to_ref",
            capture_id="cap_1",
            kernel_id="builtin.beam_power",
            params={"feature_index": 0, "latch_value_ref": True},
        )
        value_refs: dict = {}
        m1 = {"power": {"features": [1000.0, 0.8, 0.0]}}
        apply_value_ref_latch(m1, [term], value_refs)
        self.assertAlmostEqual(value_refs["power"], 1000.0)
        self.assertAlmostEqual(metric_ratio_to_ref(m1["power"], term), 1.0)

        m2 = {"power": {"features": [2000.0, 0.9, 0.0]}}
        apply_value_ref_latch(m2, [term], value_refs)
        self.assertAlmostEqual(metric_ratio_to_ref(m2["power"], term), 0.5)

        m3 = {"power": {"features": [500.0, 0.4, 0.0]}}
        apply_value_ref_latch(m3, [term], value_refs)
        self.assertAlmostEqual(metric_ratio_to_ref(m3["power"], term), 2.0)
        term = _rms_term()
        peak_refs: dict = {}
        apply_peak_presence_gate(
            {"align": {"features": [48.0, 49.0, 200.0]}}, [term], peak_refs
        )
        measurements2 = {"align": {"features": [10.0, 12.0, 40.0]}}
        apply_peak_presence_gate(measurements2, [term], peak_refs)
        self.assertFalse(measurements2["align"].get("presence_ok"))

        raw = metric_rms_distance(measurements2["align"], term)
        self.assertAlmostEqual(raw, 2.0, places=5)

        loss, parts = evaluate_weighted_sum(
            ObjectiveDecl(terms=[term]),
            measurements2,
            on_invalid="penalty",
        )
        self.assertLessEqual(loss, 2.0 + 1e-9)
        self.assertGreaterEqual(loss, 1.5)
        self.assertEqual(DEFAULT_INVALID_PENALTY, 2.0)

    def test_minimize_value_normalized(self) -> None:
        term = ObjectiveTerm(
            id="width",
            weight=1.0,
            metric="minimize_value",
            capture_id="cap_1",
            kernel_id="builtin.gaussian_beam_fit",
            params={
                "feature_index": 3,
                "normalize_by_fov": True,
                "value_scale_px": 40.0,
                "loss_cap": 2.0,
            },
        )
        # sigma=20 / 40 = 0.5
        loss = metric_minimize_value({"features": [1.0, 2.0, 3.0, 20.0, 20.0]}, term)
        self.assertAlmostEqual(loss, 0.5, places=5)

    def test_beam_presence_default_absent_is_normalized(self) -> None:
        term = ObjectiveTerm(
            id="pres",
            weight=1.0,
            metric="beam_presence",
            capture_id="cap_1",
            kernel_id="builtin.roi_centroid",
            params={"peak_ref": 100.0, "min_peak_ratio": 0.5, "peak_feature_index": 2},
        )
        self.assertEqual(metric_beam_presence({"features": [1.0, 2.0, 20.0]}, term), 2.0)

    def test_normalize_spatial_helper(self) -> None:
        term = _rms_term(rms_scale_px=200.0, loss_cap=1.5)
        self.assertAlmostEqual(
            normalize_spatial_loss(100.0, {}, term),
            0.5,
            places=5,
        )
        self.assertAlmostEqual(
            normalize_spatial_loss(1000.0, {}, term),
            1.5,
            places=5,
        )

    def test_opt_out_latch(self) -> None:
        term = _rms_term()
        term.params["latch_peak_ref"] = False
        peak_refs: dict = {}
        measurements = {"align": {"features": [1.0, 2.0, 200.0]}}
        dbg = apply_peak_presence_gate(measurements, [term], peak_refs)
        self.assertEqual(dbg, {})
        self.assertNotIn("align", peak_refs)


if __name__ == "__main__":
    unittest.main()
