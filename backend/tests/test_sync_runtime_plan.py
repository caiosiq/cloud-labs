"""Unit tests for RECORD_TUNABLES / SYNC_RUNTIME plan helpers (Phase 1)."""
from __future__ import annotations

import unittest

from lab_model.language.primitives.macros.sync_runtime import (
    build_sync_plan,
    tunable_is_recordable,
    validate_tunable_record_metadata,
)
from lab_model.language.primitives.schemas import (
    COMMAND_ADAPTER,
    RecordTunablesBody,
    SyncRuntimeBody,
)


class TunableMetadataTests(unittest.TestCase):
    def test_nominal_pose_defaults_recordable(self) -> None:
        self.assertTrue(tunable_is_recordable({}, field_id="nominal_pose"))
        self.assertTrue(
            tunable_is_recordable({"widget": "TablePose"}, field_id="nominal_pose")
        )

    def test_other_fields_default_not_recordable(self) -> None:
        self.assertFalse(
            tunable_is_recordable({"widget": "FloatRange"}, field_id="exposure_time_ms")
        )

    def test_explicit_non_recordable_requires_set_at_init(self) -> None:
        errs = validate_tunable_record_metadata(
            "open_loop",
            {"widget": "FloatRange", "recordable": False},
            scope="t",
        )
        self.assertTrue(any("set_at_init" in e for e in errs))
        self.assertFalse(any(e.startswith("__WARN__") for e in errs))

    def test_omitted_recordable_warns(self) -> None:
        errs = validate_tunable_record_metadata(
            "exposure_time_ms",
            {"widget": "FloatRange"},
            scope="t",
        )
        self.assertTrue(any(e.startswith("__WARN__") for e in errs))

    def test_recordable_skips_set_at_init(self) -> None:
        errs = validate_tunable_record_metadata(
            "nominal_pose",
            {"widget": "TablePose", "recordable": True},
            scope="t",
        )
        self.assertEqual(errs, [])


class SyncPlanTests(unittest.TestCase):
    def test_plan_sets_then_records(self) -> None:
        class Lab:
            def get_inventory(self):
                return {
                    "components": {
                        "tag_1": {"placement": "table", "localize": True},
                    }
                }

            def get_component_library(self):
                return {
                    "components": {
                        "tag_1": {
                            "tag_id": "tag_1",
                            "capabilities": {
                                "statecontrol": {
                                    "tunables": {
                                        "nominal_pose": {
                                            "widget": "TablePose",
                                            "recordable": True,
                                        },
                                        "open_loop": {
                                            "widget": "FloatRange",
                                            "recordable": False,
                                            "set_at_init": 0.0,
                                        },
                                    }
                                }
                            },
                        }
                    }
                }

        plan = build_sync_plan(Lab())
        kinds = [s["kind"] for s in plan]
        self.assertEqual(kinds.count("set"), 1)
        self.assertEqual(kinds.count("record"), 1)
        self.assertEqual(plan[0]["kind"], "set")
        self.assertEqual(plan[0]["path"], "open_loop")
        self.assertEqual(plan[1]["kind"], "record")
        self.assertEqual(plan[1]["paths"], ["nominal_pose"])


class SchemaParseTests(unittest.TestCase):
    def test_parse_record_tunables(self) -> None:
        cmd = COMMAND_ADAPTER.validate_python(
            {
                "action": "RECORD_TUNABLES",
                "parameters": {
                    "tag_ids": ["tag_22"],
                    "tunable_paths": ["nominal_pose"],
                },
            }
        )
        self.assertIsInstance(cmd, RecordTunablesBody)

    def test_parse_sync_runtime(self) -> None:
        cmd = COMMAND_ADAPTER.validate_python({"action": "SYNC_RUNTIME", "parameters": {}})
        self.assertIsInstance(cmd, SyncRuntimeBody)


if __name__ == "__main__":
    unittest.main()
