from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import main


class SimulationComponentApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.rt = SimpleNamespace(backend_id="sim.default", lab_mode="SIMULATION")
        self.state = {"system_status": "IDLE", "components": {}}
        self.context = (object(), self.state, {}, [])

    async def test_list_proxies_complete_simulation_library(self) -> None:
        edge = AsyncMock(
            return_value={
                "schema_version": 1,
                "components": [{"tag_id": "tag_9", "active": False}],
            }
        )
        with patch.object(
            main, "_require_simulation_edge", return_value=(self.rt, "edge")
        ), patch.object(main, "_simulation_component_edge_request", edge):
            result = await main.list_runtime_simulation_components()

        self.assertEqual(result["components"][0]["tag_id"], "tag_9")
        edge.assert_awaited_once_with("edge", "GET", "/simulation/components")

    async def test_define_keeps_definition_separate_from_runtime_state(self) -> None:
        edge = AsyncMock(
            return_value={
                "status": "ok",
                "runtime_restarted": False,
                "component": {"tag_id": "tag_23"},
            }
        )
        payload = {
            "name": "Paper lens",
            "type": "OPTICAL_LENS",
            "parameters": {"focal_length_mm": 175},
        }
        with patch.object(
            main, "_require_simulation_edge", return_value=(self.rt, "edge")
        ), patch.object(
            main, "_simulation_preset_context", return_value=self.context
        ), patch.object(main, "_assert_simulation_reset_idle"), patch.object(
            main, "_simulation_component_edge_request", edge
        ):
            result = await main.define_runtime_simulation_component(
                "tag_23", payload
            )

        self.assertFalse(result["runtime_restarted"])
        edge.assert_awaited_once_with(
            "edge",
            "PUT",
            "/simulation/components/tag_23",
            payload=payload,
        )

    async def test_insert_publishes_twin_reset_revision(self) -> None:
        edge_result = {
            "status": "ok",
            "runtime_restarted": True,
            "tag_id": "tag_23",
            "lab_state": {
                "system_status": "IDLE",
                "components": {"tag_23": {}},
            },
        }
        edge = AsyncMock(return_value=edge_result)
        publish = AsyncMock(return_value={**edge_result, "simulation_reset": {"revision": "r"}})
        payload = {"x": -200, "y": 120, "rotation": 0}
        with patch.object(
            main, "_require_simulation_edge", return_value=(self.rt, "edge")
        ), patch.object(
            main, "_simulation_preset_context", return_value=self.context
        ), patch.object(main, "_assert_simulation_reset_idle"), patch.object(
            main, "_simulation_component_edge_request", edge
        ), patch.object(
            main, "_publish_simulation_component_runtime_change", publish
        ):
            result = await main.insert_runtime_simulation_component(
                "tag_23", payload
            )

        self.assertEqual(result["simulation_reset"]["revision"], "r")
        publish.assert_awaited_once()

    async def test_next_tag_proxies_simple_read(self) -> None:
        edge = AsyncMock(return_value={"tag_id": "tag_100"})
        with patch.object(
            main, "_require_simulation_edge", return_value=(self.rt, "edge")
        ), patch.object(main, "_simulation_component_edge_request", edge):
            result = await main.next_runtime_simulation_component_tag()

        self.assertEqual(result, {"tag_id": "tag_100"})
        edge.assert_awaited_once_with(
            "edge", "GET", "/simulation/components/next-tag"
        )


if __name__ == "__main__":
    unittest.main()
