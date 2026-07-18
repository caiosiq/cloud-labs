"""active_catalog.json restricts lab_automation catalog and scan scope."""
from __future__ import annotations

import unittest

from lab_model.coordinator.catalog.bundle import filter_v1_catalog_to_active_tags


class TestActiveCatalogFilter(unittest.TestCase):
    def test_filter_v1_components(self) -> None:
        doc = {
            "schema_version": 1,
            "components": {
                "tag_8": {"tag_id": "tag_8"},
                "tag_18": {"tag_id": "tag_18"},
                "tag_99": {"tag_id": "tag_99"},
            },
        }
        out = filter_v1_catalog_to_active_tags(doc, ["tag_8", "tag_99"])
        self.assertEqual(set(out["components"].keys()), {"tag_8", "tag_99"})
        self.assertEqual(doc["components"]["tag_18"]["tag_id"], "tag_18")


if __name__ == "__main__":
    unittest.main()
