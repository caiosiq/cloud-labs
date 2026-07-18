"""Tests for KernelMatchSpec feature / centroid targeting (Step D)."""
from __future__ import annotations

import unittest

from cloudlabs.intent import KernelMatchSpec, build_cobyla_ensemble
from cloudlabs.intent import VariableSpec


class KernelMatchFeatureTests(unittest.TestCase):
    def test_scalar_match_unchanged(self) -> None:
        term = KernelMatchSpec(
            tag_id="tag_22",
            field="camera_image",
            kernel_id="demo.image_mean_score",
            target=0.35,
        ).to_term()
        self.assertEqual(term["source"]["kind"], "torchscript_scalar")
        self.assertEqual(term["metric"], "squared_error")
        self.assertAlmostEqual(term["source"]["target_scalar"], 0.35)

    def test_centroid_pair_target(self) -> None:
        term = KernelMatchSpec(
            tag_id="tag_22",
            field="camera_image",
            kernel_id="builtin.roi_centroid",
            target=(512.0, 384.0),
        ).to_term()
        self.assertEqual(term["source"]["kind"], "torchscript_features")
        self.assertEqual(term["metric"], "rms_distance")
        self.assertEqual(term["source"]["feature_index"], [0, 1])
        self.assertEqual(term["source"]["target_px"], {"x": 512.0, "y": 384.0})

    def test_feature_channel_target(self) -> None:
        term = KernelMatchSpec(
            tag_id="tag_22",
            field="camera_image",
            kernel_id="builtin.gaussian_beam_fit",
            target=5.0,
            feature_index=3,
        ).to_term()
        self.assertEqual(term["source"]["kind"], "torchscript_features")
        self.assertEqual(term["source"]["feature_index"], 3)
        self.assertAlmostEqual(term["source"]["target_scalar"], 5.0)

    def test_build_cobyla_label_for_px(self) -> None:
        params = build_cobyla_ensemble(
            variables=[
                VariableSpec(
                    tag_id="tag_20",
                    path="tunables.nominal_motor_positions.1",
                    bounds=(-1.0, 1.0),
                )
            ],
            match_kernel=KernelMatchSpec(
                tag_id="tag_22",
                field="camera_image",
                kernel_id="builtin.roi_centroid",
                target=(100.0, 200.0),
            ),
            max_evals=4,
        )
        self.assertIn("100.0", params["session_label"])
        self.assertEqual(
            params["objective"]["terms"][0]["source"]["kernel_id"],
            "builtin.roi_centroid",
        )


if __name__ == "__main__":
    unittest.main()
