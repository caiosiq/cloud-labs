"""edge_data library validation — fail hard, never silently backfill."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from cloudlabs_edge_dev.edge_data import (
    LibraryValidationError,
    validate_library_document,
)


class LibraryValidationTests(unittest.TestCase):
    def test_recordable_false_without_set_at_init_hard_fails(self) -> None:
        lib = {
            "schema_version": 1,
            "components": {
                "tag_1": {
                    "id": "x",
                    "type": "OPTICAL_CAMERA",
                    "tag_id": "tag_1",
                    "capabilities": {
                        "primitives": ["RECORD_TUNABLES"],
                        "statecontrol": {
                            "tunables": {
                                "exposure_time_ms": {
                                    "widget": "FloatRange",
                                    "recordable": False,
                                }
                            }
                        },
                    },
                }
            },
        }
        with self.assertRaises(LibraryValidationError) as ctx:
            validate_library_document(lib, source="test")
        self.assertIn("set_at_init", str(ctx.exception))

    def test_strict_mode_rejects_omitted_recordable(self) -> None:
        lib = {
            "schema_version": 1,
            "components": {
                "tag_1": {
                    "id": "x",
                    "type": "OPTICAL_CAMERA",
                    "tag_id": "tag_1",
                    "capabilities": {
                        "primitives": ["RECORD_TUNABLES"],
                        "statecontrol": {
                            "tunables": {
                                "exposure_time_ms": {"widget": "FloatRange"}
                            }
                        },
                    },
                }
            },
        }
        warns = validate_library_document(lib, source="test", strict_recordable=False)
        self.assertTrue(any("recordable" in w for w in warns))
        with self.assertRaises(LibraryValidationError):
            validate_library_document(lib, source="test", strict_recordable=True)

    def test_does_not_invent_missing_capabilities(self) -> None:
        lib = {
            "schema_version": 1,
            "components": {
                "tag_1": {
                    "id": "x",
                    "type": "OPTICAL_CAMERA",
                    "tag_id": "tag_1",
                }
            },
        }
        with self.assertRaises(LibraryValidationError) as ctx:
            validate_library_document(lib, source="test")
        self.assertIn("capabilities", str(ctx.exception))
        # Document left untouched — no backfill side effect.
        self.assertNotIn("capabilities", lib["components"]["tag_1"])

    def test_scaffold_library_passes_strict(self) -> None:
        from cloudlabs_edge_dev.scaffold import init_edge

        with tempfile.TemporaryDirectory() as tmp:
            root = init_edge(Path(tmp) / "cloudlabs_edge")
            doc = json.loads((root / "data" / "library.json").read_text(encoding="utf-8"))
            warns = validate_library_document(
                doc, source="scaffold", strict_recordable=True
            )
            self.assertEqual(warns, [])


if __name__ == "__main__":
    unittest.main()
