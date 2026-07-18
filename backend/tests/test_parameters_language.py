"""UC parameters: catalog bag + GET_PARAMETERS read primitive."""
from __future__ import annotations

import unittest

from lab_model.language.parameters import (
    catalog_parameters_bag,
    normalize_catalog_row_parameters,
    parameters_from_catalog_row,
)
from lab_model.language.primitives.ids import PrimitiveId, READ_PRIMITIVE_IDS
from lab_model.language.primitives.registry import PRIMITIVE_REGISTRY


class ParametersLanguageTests(unittest.TestCase):
    def test_get_parameters_is_read_primitive(self) -> None:
        self.assertIn(PrimitiveId.GET_PARAMETERS, READ_PRIMITIVE_IDS)
        meta = PRIMITIVE_REGISTRY[PrimitiveId.GET_PARAMETERS]
        self.assertTrue(meta.get("read_only"))
        self.assertEqual(meta.get("handler"), "return_parameters_for_tag")

    def test_normalize_migrates_properties(self) -> None:
        row = {"id": "cam", "type": "OPTICAL_CAMERA", "properties": {"max_fps": 30}}
        normalize_catalog_row_parameters(row)
        self.assertEqual(row["parameters"]["max_fps"], 30)

    def test_parameters_from_catalog_merges_structural(self) -> None:
        row = {
            "id": "cam_gripper_1",
            "type": "OPTICAL_CAMERA",
            "tag_id": "tag_22",
            "height_mm": 175,
            "parameters": {"manufacturer": "Acme", "max_fps": 60},
        }
        params = parameters_from_catalog_row(row)
        self.assertEqual(params["type"], "OPTICAL_CAMERA")
        self.assertEqual(params["manufacturer"], "Acme")
        self.assertEqual(params["max_fps"], 60)
        self.assertEqual(catalog_parameters_bag(row)["max_fps"], 60)


if __name__ == "__main__":
    unittest.main()
