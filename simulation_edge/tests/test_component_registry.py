from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from simulation_edge.component_registry import (
    SimulationComponentError,
    configure_component,
    define_component,
    load_registry,
    merged_library,
    next_tag_id,
    reset_component_override,
    save_registry,
)


def _builtin_row(tag_id: str) -> dict:
    return {
        "id": "lens_builtin",
        "tag_id": tag_id,
        "name": "Built-in lens",
        "type": "OPTICAL_LENS",
        "size": {"width": 62, "height": 62},
        "height_mm": 60,
        "parameters": {"focal_length_mm": 150},
        "capabilities": {
            "statecontrol": {
                "tunables": {
                    "nominal_pose": {"widget": "TablePose", "recordable": True}
                },
                "measurables": {},
            },
            "telemetry": {"teleop": {}, "live_feed": {}},
            "primitives": ["MOVE_COMPONENT"],
        },
    }


class SimulationComponentRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.edge_root = Path(self.temp.name)
        data = self.edge_root / "data"
        data.mkdir()
        self.base_path = data / "library.json"
        self.base_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "components": {"tag_9": _builtin_row("tag_9")},
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_custom_definition_is_merged_without_changing_builtins(self) -> None:
        registry, row = define_component(
            self.edge_root,
            "tag_23",
            {
                "name": "Paper lens",
                "type": "OPTICAL_LENS",
                "parameters": {"focal_length_mm": 175, "filter": "longpass"},
            },
        )
        save_registry(self.edge_root, registry)

        merged = merged_library(self.edge_root)["components"]
        self.assertIn("tag_23", merged)
        self.assertEqual(row["simulation"]["model"], "generic_black_box")
        self.assertEqual(merged["tag_9"]["parameters"]["focal_length_mm"], 150)
        base = json.loads(self.base_path.read_text(encoding="utf-8"))
        self.assertEqual(list(base["components"]), ["tag_9"])

    def test_builtin_parameter_override_is_separate_and_resettable(self) -> None:
        registry, row = configure_component(
            self.edge_root,
            "tag_9",
            {"parameters": {"focal_length_mm": 200, "filter": "bandpass"}},
        )
        save_registry(self.edge_root, registry)
        self.assertEqual(row["parameters"]["focal_length_mm"], 200)
        self.assertIn("tag_9", load_registry(self.edge_root)["overrides"])

        reset, row = reset_component_override(self.edge_root, "tag_9")
        save_registry(self.edge_root, reset)
        self.assertEqual(row["parameters"]["focal_length_mm"], 150)
        self.assertNotIn("tag_9", load_registry(self.edge_root)["overrides"])

    def test_rejects_unsafe_tag_and_non_finite_parameters(self) -> None:
        with self.assertRaises(SimulationComponentError):
            define_component(self.edge_root, "../bad", {"name": "bad"})
        with self.assertRaises(SimulationComponentError):
            define_component(
                self.edge_root,
                "tag_bad_number",
                {"name": "bad", "parameters": {"value": float("nan")}},
            )
        with self.assertRaises(SimulationComponentError):
            define_component(self.edge_root, "tag_paper_lens_1", {"name": "bad"})
        with self.assertRaisesRegex(SimulationComponentError, "unsupported component field"):
            define_component(
                self.edge_root,
                "tag_23",
                {"name": "bad", "size": {"width": 10, "height": 10}},
            )

    def test_next_tag_is_one_greater_than_highest_numeric_tag(self) -> None:
        self.assertEqual(next_tag_id(["tag_2", "tag_9", "tag_99"]), "tag_100")


if __name__ == "__main__":
    unittest.main()
