from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from lab_model.coordinator.state.simulation_presets import (
    AUTHORING_KIND,
    SimulationPresetError,
    build_simulation_state_from_authoring,
    list_simulation_presets,
    load_simulation_preset,
    normalize_simulation_state,
    save_simulation_preset,
    simulation_preset_authoring_document,
)


def _state(*, x: float = 10.0, y: float = 20.0) -> dict:
    return {
        "system_status": "BUSY",
        "session_lease": {"lease_id": "do-not-save"},
        "simulator": {"pid": 123},
        "simulation_reset": {"revision": "do-not-save"},
        "holding": {"tag_id": "tag_13", "requires_operator_confirm": True},
        "components": {
            "tag_13": {
                "id": "tag_13",
                "type": "OPTICAL_POLARIZER",
                "statecontrol": {
                    "tunables": {
                        "presence": "breadboard",
                        "nominal_pose": {"x": x, "y": y, "rotation": -90.0},
                        "reported_pose": {"x": x + 3.0, "y": y - 1.0, "rotation": -89.0},
                        "nominal_motor_positions": {"1": 2.0},
                        "storage": {"in_storage": False, "slot": None},
                        "placement": {"mode": "MANUAL"},
                    },
                    "measurables": {
                        "pose": {"x": x + 4.0, "y": y, "rotation": -88.0},
                        "camera_image": "stale.png",
                    },
                },
                "telemetry": {"teleop": {"active": True}},
            }
        },
    }


LAYOUT = {
    "lab_bounds_mm": {"x_min": -100.0, "x_max": 100.0, "y_min": -100.0, "y_max": 100.0},
    "storage": {"grid_nx": 2, "grid_ny": 2},
}


class SimulationPresetTests(unittest.TestCase):
    def test_authoring_document_round_trip_updates_pose_without_loading(self) -> None:
        base = normalize_simulation_state(
            _state(), catalog_tag_ids=["tag_13"], layout=LAYOUT
        )
        document = simulation_preset_authoring_document(base, base="default")
        self.assertEqual(document["kind"], AUTHORING_KIND)
        document["components"]["tag_13"]["pose"] = {
            "x": -30,
            "y": 15,
            "rotation": 45,
        }
        authored = build_simulation_state_from_authoring(
            document,
            base_state=base,
            catalog_tag_ids=["tag_13"],
            layout=LAYOUT,
        )
        pose = authored["components"]["tag_13"]["statecontrol"]["tunables"][
            "nominal_pose"
        ]
        self.assertEqual(pose, {"x": -30.0, "y": 15.0, "rotation": 45.0})

    def test_authoring_rejects_bad_shape_and_unknown_component(self) -> None:
        base = normalize_simulation_state(
            _state(), catalog_tag_ids=["tag_13"], layout=LAYOUT
        )
        document = simulation_preset_authoring_document(base, base="default")
        del document["components"]["tag_13"]["pose"]["rotation"]
        with self.assertRaisesRegex(SimulationPresetError, "pose.rotation is required"):
            build_simulation_state_from_authoring(
                document,
                base_state=base,
                catalog_tag_ids=["tag_13"],
                layout=LAYOUT,
            )

        document = simulation_preset_authoring_document(base, base="default")
        document["components"]["tag_99"] = document["components"].pop("tag_13")
        with self.assertRaisesRegex(SimulationPresetError, "nor defined"):
            build_simulation_state_from_authoring(
                document,
                base_state=base,
                catalog_tag_ids=["tag_13"],
                layout=LAYOUT,
            )

    def test_authoring_materializes_known_catalog_component_from_empty_base(self) -> None:
        document = {
            "schema_version": 1,
            "kind": AUTHORING_KIND,
            "base": "current",
            "components": {
                "tag_23": {
                    "presence": "breadboard",
                    "pose": {"x": -30, "y": 15, "rotation": 45},
                }
            },
        }
        authored = build_simulation_state_from_authoring(
            document,
            base_state={"system_status": "IDLE", "components": {}},
            catalog_tag_ids=["tag_23"],
            catalog_components={
                "tag_23": {
                    "tag_id": "tag_23",
                    "type": "OPTICAL_LENS",
                    "size": {"width": 62, "height": 62},
                }
            },
            layout=LAYOUT,
        )
        component = authored["components"]["tag_23"]
        self.assertEqual(component["type"], "OPTICAL_LENS")
        self.assertEqual(
            component["statecontrol"]["tunables"]["nominal_pose"],
            {"x": -30.0, "y": 15.0, "rotation": 45.0},
        )

    def test_authoring_rejects_overlapping_targets(self) -> None:
        base = _state(x=-30.0, y=0.0)
        base["components"]["tag_14"] = copy = json.loads(
            json.dumps(base["components"]["tag_13"])
        )
        copy["id"] = "tag_14"
        copy["statecontrol"]["tunables"]["nominal_pose"] = {
            "x": 30.0,
            "y": 0.0,
            "rotation": 0.0,
        }
        normalized = normalize_simulation_state(
            base,
            catalog_tag_ids=["tag_13", "tag_14"],
            layout=LAYOUT,
        )
        document = simulation_preset_authoring_document(normalized, base="default")
        with self.assertRaisesRegex(SimulationPresetError, "overlaps"):
            build_simulation_state_from_authoring(
                document,
                base_state=normalized,
                catalog_tag_ids=["tag_13", "tag_14"],
                catalog_components={
                    "tag_13": {"size": {"width": 62, "height": 62}},
                    "tag_14": {"size": {"width": 62, "height": 62}},
                },
                layout=LAYOUT,
            )

    def test_hand_authored_component_may_omit_telemetry(self) -> None:
        state = _state()
        del state["components"]["tag_13"]["telemetry"]
        normalized = normalize_simulation_state(
            state, catalog_tag_ids=["tag_13"], layout=LAYOUT
        )
        tunables = normalized["components"]["tag_13"]["statecontrol"]["tunables"]
        self.assertEqual(tunables["nominal_pose"]["x"], 10.0)
        self.assertIn("telemetry", normalized["components"]["tag_13"])

    def test_normalize_scrubs_transient_fields_and_measurement_noise(self) -> None:
        normalized = normalize_simulation_state(
            _state(), catalog_tag_ids=["tag_13"], layout=LAYOUT
        )
        self.assertEqual(normalized["system_status"], "IDLE")
        self.assertNotIn("session_lease", normalized)
        self.assertNotIn("simulator", normalized)
        self.assertNotIn("simulation_reset", normalized)
        self.assertIsNone(normalized["holding"]["tag_id"])
        comp = normalized["components"]["tag_13"]
        tun = comp["statecontrol"]["tunables"]
        meas = comp["statecontrol"]["measurables"]
        self.assertEqual(tun["reported_pose"], tun["nominal_pose"])
        self.assertEqual(meas["pose"], tun["nominal_pose"])
        self.assertIsNone(meas["camera_image"])
        self.assertFalse(comp["telemetry"]["teleop"]["active"])

    def test_save_list_load_round_trip_and_duplicate_guard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            states = root / "states"
            default_path = root / "lab_state.json"
            default_path.write_text(json.dumps(_state(x=1.0)), encoding="utf-8")

            path = save_simulation_preset(
                str(states),
                "polarizer_test",
                _state(x=-30.0),
                catalog_tag_ids=["tag_13"],
                layout=LAYOUT,
            )
            self.assertEqual(path.name, "polarizer_test.json")
            doc = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(doc["kind"], "cloud_labs_workspace_state")

            rows = list_simulation_presets(str(states))
            self.assertEqual([row["name"] for row in rows], ["polarizer_test"])
            self.assertTrue(rows[0]["valid"])

            loaded = load_simulation_preset(
                str(states),
                "polarizer_test",
                default_state_path=str(default_path),
                catalog_tag_ids=["tag_13"],
                layout=LAYOUT,
            )
            self.assertEqual(
                loaded["components"]["tag_13"]["statecontrol"]["tunables"]["nominal_pose"]["x"],
                -30.0,
            )
            with self.assertRaises(FileExistsError):
                save_simulation_preset(
                    str(states),
                    "polarizer_test",
                    _state(),
                    catalog_tag_ids=["tag_13"],
                    layout=LAYOUT,
                )

    def test_default_selector_reads_root_lab_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            default_path = root / "lab_state.json"
            default_path.write_text(json.dumps(_state(x=42.0)), encoding="utf-8")
            loaded = load_simulation_preset(
                str(root / "states"),
                "default",
                default_state_path=str(default_path),
                catalog_tag_ids=["tag_13"],
                layout=LAYOUT,
            )
            pose = loaded["components"]["tag_13"]["statecontrol"]["tunables"]["nominal_pose"]
            self.assertEqual(pose["x"], 42.0)

    def test_rejects_unsafe_names_unknown_tags_and_out_of_bounds_pose(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SimulationPresetError):
                save_simulation_preset(str(Path(tmp) / "states"), "../escape", _state())
        with self.assertRaises(SimulationPresetError):
            normalize_simulation_state(_state(), catalog_tag_ids=["tag_other"], layout=LAYOUT)
        with self.assertRaises(SimulationPresetError):
            normalize_simulation_state(
                _state(x=101.0), catalog_tag_ids=["tag_13"], layout=LAYOUT
            )


if __name__ == "__main__":
    unittest.main()
