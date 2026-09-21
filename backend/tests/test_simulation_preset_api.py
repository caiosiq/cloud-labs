from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import main
from lab_model.coordinator.state.simulation_presets import save_simulation_preset


LAYOUT = {
    "lab_bounds_mm": {"x_min": -100.0, "x_max": 100.0, "y_min": -100.0, "y_max": 100.0},
    "storage": {"grid_nx": 2, "grid_ny": 2},
}


def _state(x: float) -> dict:
    return {
        "system_status": "IDLE",
        "components": {
            "tag_13": {
                "id": "tag_13",
                "type": "OPTICAL_POLARIZER",
                "statecontrol": {
                    "tunables": {
                        "presence": "breadboard",
                        "nominal_pose": {"x": x, "y": 0.0, "rotation": -90.0},
                        "nominal_motor_positions": {},
                        "storage": {"in_storage": False, "slot": None},
                        "placement": {"mode": "MANUAL"},
                    },
                    "measurables": {},
                },
                "telemetry": {},
            }
        },
    }


class _Store:
    def __init__(self, state: dict):
        self.state = copy.deepcopy(state)
        self.replace_calls = []

    def snapshot(self):
        return copy.deepcopy(self.state)

    def replace_state(self, state, **kwargs):
        self.state = copy.deepcopy(state)
        self.replace_calls.append((copy.deepcopy(state), kwargs))

    def mutate(self, fn, **kwargs):
        fn(self.state)


class SimulationPresetApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.states = self.root / "states"
        (self.root / "lab_state.json").write_text(json.dumps(_state(1.0)), encoding="utf-8")
        self.rt = SimpleNamespace(
            backend_id="sim.test",
            lab_mode="SIMULATION",
            paths=SimpleNamespace(root_dir=str(self.root), states_dir=str(self.states)),
        )
        self.store = _Store(_state(10.0))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    async def test_save_endpoint_writes_workspace_preset(self) -> None:
        payload = main.SimulationPresetSaveBody(overwrite=False)
        with patch.object(main, "_require_simulation_edge", return_value=(self.rt, "edge")), patch.object(
            main,
            "_simulation_preset_context",
            return_value=(self.store, self.store.snapshot(), LAYOUT, ["tag_13"]),
        ), patch.object(main, "_assert_simulation_reset_idle"):
            result = await main.save_runtime_simulation_preset("alignment", payload)

        self.assertEqual(result["status"], "ok")
        saved = json.loads((self.states / "alignment.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["kind"], "cloud_labs_workspace_state")

    async def test_show_endpoint_returns_round_trip_authoring_json(self) -> None:
        save_simulation_preset(
            str(self.states),
            "alignment",
            _state(-30.0),
            catalog_tag_ids=["tag_13"],
            layout=LAYOUT,
        )
        with patch.object(main, "_require_simulation_edge", return_value=(self.rt, "edge")), patch.object(
            main,
            "_simulation_preset_context",
            return_value=(self.store, self.store.snapshot(), LAYOUT, ["tag_13"]),
        ):
            result = await main.show_runtime_simulation_preset("alignment")

        document = result["document"]
        self.assertEqual(document["base"], "alignment")
        self.assertEqual(document["components"]["tag_13"]["pose"]["x"], -30.0)

    async def test_write_endpoint_saves_authored_json_without_loading(self) -> None:
        document = {
            "schema_version": 1,
            "kind": "cloud_labs_simulation_preset",
            "base": "default",
            "components": {
                "tag_13": {
                    "presence": "breadboard",
                    "pose": {"x": -30, "y": 15, "rotation": 45},
                }
            },
        }
        payload = main.SimulationPresetWriteBody(document=document, overwrite=False)
        with patch.object(main, "_require_simulation_edge", return_value=(self.rt, "edge")), patch.object(
            main,
            "_simulation_preset_context",
            return_value=(self.store, self.store.snapshot(), LAYOUT, ["tag_13"]),
        ), patch.object(main, "_assert_simulation_reset_idle"):
            result = await main.write_runtime_simulation_preset("authored", payload)

        self.assertEqual(result["preset"]["name"], "authored")
        self.assertEqual(len(self.store.replace_calls), 0)
        saved = json.loads((self.states / "authored.json").read_text(encoding="utf-8"))
        pose = saved["lab_state"]["components"]["tag_13"]["statecontrol"]["tunables"][
            "nominal_pose"
        ]
        self.assertEqual(pose, {"x": -30.0, "y": 15.0, "rotation": 45.0})

    async def test_load_endpoint_restarts_edge_then_replaces_working_state(self) -> None:
        save_simulation_preset(
            str(self.states),
            "alignment",
            _state(-30.0),
            catalog_tag_ids=["tag_13"],
            layout=LAYOUT,
        )
        restart = AsyncMock(return_value={"status": "ok", "simulator": {"pid": 42}})
        mode_info = AsyncMock(return_value={"active_mode": "mujoco"})
        with patch.object(main, "_require_simulation_edge", return_value=(self.rt, "edge")), patch.object(
            main,
            "_simulation_preset_context",
            return_value=(self.store, self.store.snapshot(), LAYOUT, ["tag_13"]),
        ), patch.object(main, "_assert_simulation_reset_idle"), patch.object(
            main, "_restart_mujoco_edge", restart
        ), patch.object(main, "_edge_runtime_mode_info", mode_info):
            result = await main.load_runtime_simulation_preset("alignment")

        restart.assert_awaited_once()
        self.assertEqual(len(self.store.replace_calls), 1)
        loaded = self.store.state["components"]["tag_13"]["statecontrol"]["tunables"]
        self.assertEqual(loaded["nominal_pose"]["x"], -30.0)
        self.assertEqual(result["preset"], "alignment")
        event = self.store.state["simulation_reset"]
        self.assertEqual(event["selector"], "alignment")
        self.assertEqual(event["revision"], result["simulation_reset"]["revision"])
        self.assertEqual(
            restart.await_args.args[1]["simulation_reset"]["revision"],
            event["revision"],
        )

    async def test_refresh_endpoint_publishes_cross_window_reset_revision(self) -> None:
        restart = AsyncMock(return_value={"status": "ok", "simulator": {"pid": 42}})
        mode_info = AsyncMock(return_value={"active_mode": "mujoco"})
        with patch.object(main, "_require_simulation_edge", return_value=(self.rt, "edge")), patch.object(
            main,
            "_simulation_preset_context",
            return_value=(self.store, self.store.snapshot(), LAYOUT, ["tag_13"]),
        ), patch.object(main, "_assert_simulation_reset_idle"), patch.object(
            main, "_restart_mujoco_edge", restart
        ), patch.object(main, "_edge_runtime_mode_info", mode_info):
            result = await main.refresh_mujoco_runtime()

        event = self.store.state["simulation_reset"]
        self.assertEqual(event["selector"], "current")
        self.assertEqual(event["revision"], result["simulation_reset"]["revision"])
        self.assertTrue(event["revision"])
        self.assertEqual(
            restart.await_args.args[1]["simulation_reset"]["revision"],
            event["revision"],
        )


if __name__ == "__main__":
    unittest.main()
