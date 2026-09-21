from __future__ import annotations

import copy
import json
import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from simulation_edge.host.simulation_host import SimulationHost, _pose_from_component


def _row(tag_id: str) -> dict:
    return {
        "id": tag_id,
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


class SimulationComponentAdminTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.edge_root = Path(self.temp.name)
        data = self.edge_root / "data"
        data.mkdir()
        (data / "library.json").write_text(
            json.dumps(
                {"schema_version": 1, "components": {"tag_9": _row("tag_9")}}
            ),
            encoding="utf-8",
        )
        self.layout = {
            "lab_bounds_mm": {
                "x_min": -500,
                "x_max": 500,
                "y_min": -500,
                "y_max": 500,
            },
            "storage": {
                "bounds_mm": {
                    "x_min": -100,
                    "x_max": 100,
                    "y_min": -500,
                    "y_max": -300,
                },
                "grid_nx": 2,
                "grid_ny": 2,
            },
        }
        with mock.patch.dict(os.environ, {"CLOUDLABS_SKIP_RUNTIME_SYNC": "1"}):
            self.host = SimulationHost(
                {"system_status": "IDLE", "components": {}},
                [_row("tag_9")],
                self.layout,
                enable_mujoco=False,
            )
        self.host._edge_data_root = mock.Mock(return_value=self.edge_root)

        def fake_restart(
            host,
            *,
            lab_state=None,
            catalog_rows=None,
            show_viewer=None,
            realtime=None,
        ):
            if lab_state is not None:
                host.current_state = copy.deepcopy(dict(lab_state))
                host._poses = {
                    str(tag): _pose_from_component(component)
                    for tag, component in host.current_state.get("components", {}).items()
                }
            if catalog_rows is not None:
                host._replace_catalog(catalog_rows)
            return {"mujoco_running": True}

        self.host.restart_mujoco = types.MethodType(fake_restart, self.host)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_define_insert_configure_and_clear(self) -> None:
        defined = self.host.define_simulation_component(
            "tag_23",
            {
                "name": "Paper lens",
                "type": "OPTICAL_LENS",
                "parameters": {"focal_length_mm": 175},
            },
        )
        self.assertFalse(defined["runtime_restarted"])

        inserted = self.host.insert_simulation_component(
            "tag_23", x=-200, y=100, rotation=25
        )
        self.assertTrue(inserted["runtime_restarted"])
        self.assertIn("tag_23", self.host.current_state["components"])
        self.assertIn("tag_23", self.host.get_inventory()["entries"])

        configured = self.host.configure_simulation_component(
            "tag_23",
            {"parameters": {"focal_length_mm": 225, "filter": "bandpass"}},
        )
        self.assertTrue(configured["runtime_restarted"])
        detail = self.host.get_simulation_component("tag_23")
        self.assertEqual(
            detail["parameters"]["focal_length_mm"], 225
        )

        cleared = self.host.clear_simulation_components(scope="table")
        self.assertEqual(cleared["removed"], ["tag_23"])
        self.assertEqual(self.host.current_state["components"], {})

    def test_list_show_and_next_tag_expose_simple_agent_records(self) -> None:
        listed = self.host.list_simulation_components()
        self.assertEqual(listed["pair_clearance_margin_mm"], 5.0)
        self.assertEqual(
            set(listed["components"][0]),
            {
                "tag_id",
                "name",
                "type",
                "parameters",
                "housing",
                "active",
                "placement",
            },
        )
        self.assertEqual(
            listed["components"][0]["housing"],
            {
                "footprint_mm": {"width": 62.0, "depth": 62.0},
                "height_mm": 60.0,
                "clearance_radius_mm": 43.841,
            },
        )
        shown = self.host.get_simulation_component("tag_9")
        self.assertEqual(
            set(shown),
            {
                "tag_id",
                "name",
                "type",
                "parameters",
                "housing",
                "active",
                "placement",
                "pair_clearance_margin_mm",
            },
        )
        self.assertEqual(self.host.next_simulation_component_tag(), {"tag_id": "tag_10"})

    def test_storage_insert_uses_slot_center(self) -> None:
        result = self.host.insert_simulation_component(
            "tag_9", storage_slot={"i": 1, "j": 0}
        )
        self.assertEqual(result["presence"], "storage")
        self.assertEqual(result["pose"], {"x": 50.0, "y": -450.0, "rotation": 0.0})


if __name__ == "__main__":
    unittest.main()
