"""Tensor-native measurable language: registry schema + observe materialize."""
from __future__ import annotations

import unittest

from lab_model.language import measurables as _meas  # noqa: F401 — register
from lab_model.language.measurables import (
    MEASURABLE_REGISTRY,
    legacy_wire_view,
    materialize_measurable,
)
from lab_model.platform import export_platform_registries, validate_platform_integrity


class MeasurableTensorLanguageTests(unittest.TestCase):
    def test_plugins_declare_tensor_schema(self) -> None:
        for field_id in (
            "camera_image",
            "last_optimization_score",
            "output_power_readback_mw",
        ):
            spec = MEASURABLE_REGISTRY[field_id]
            self.assertTrue(spec.tensor.dtype)
            self.assertTrue(spec.tensor.domain)

        self.assertEqual(MEASURABLE_REGISTRY["camera_image"].tensor.layout, "lazy_image")
        self.assertEqual(
            MEASURABLE_REGISTRY["output_power_readback_mw"].tensor.units.get("value"),
            "mW",
        )

    def test_materialize_scalar_and_camera(self) -> None:
        score = materialize_measurable("tag_20", "last_optimization_score", 0.91)
        self.assertEqual(score.domain, "scalar")
        self.assertEqual(score.data, 0.91)
        envelope = score.to_api_dict()
        self.assertEqual(envelope["field"], "last_optimization_score")

        cam = materialize_measurable(
            "tag_22",
            "camera_image",
            {"path": "/tmp/x.png", "format": "png", "source": "test"},
        )
        wire = legacy_wire_view(cam.to_api_dict())
        self.assertEqual(wire["path"], "/tmp/x.png")
        self.assertEqual(wire["format"], "png")

    def test_platform_export_includes_tensor(self) -> None:
        validate_platform_integrity()
        snap = export_platform_registries()
        self.assertIn("tensor", snap["measurables"]["camera_image"])
        self.assertEqual(
            snap["measurables"]["camera_image"]["tensor"]["dtype"],
            "uint8",
        )


if __name__ == "__main__":
    unittest.main()
