import copy
import json
import unittest
from pathlib import Path
from unittest import mock

from simulation_edge.bootstrap import _resolve_catalog_rows
from simulation_edge.host.simulation_host import SimulationHost


LAB_VIEW = Path(__file__).resolve().parents[1] / "lab_view"


class FakeMuJoCoClient:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.calls = []
        self.stopped = False

    def stop(self):
        self.stopped = True

    def move_component(self, tag_id, **kwargs):
        self.calls.append((tag_id, dict(kwargs)))
        if self.fail:
            raise RuntimeError("preflight rejected")
        return {
            "x_mm": kwargs["target_x_mm"],
            "y_mm": kwargs["target_y_mm"],
            "rotation_deg": kwargs["target_rotation_deg"],
        }


def make_host() -> SimulationHost:
    layout = json.loads((LAB_VIEW / "layout.json").read_text(encoding="utf-8"))
    state = json.loads((LAB_VIEW / "lab_state.json").read_text(encoding="utf-8"))
    return SimulationHost(
        state,
        _resolve_catalog_rows(LAB_VIEW),
        layout,
        enable_mujoco=False,
    )


def tunables(host: SimulationHost, tag_id: str):
    return host.current_state["components"][tag_id]["statecontrol"]["tunables"]


class StoragePrimitiveTests(unittest.IsolatedAsyncioTestCase):
    def test_restart_adopts_coordinator_state_without_changing_edge_session(self):
        host = make_host()
        original_session = host.edge_session_id
        requested = host.get_lab_state()
        requested["components"]["tag_18"]["statecontrol"]["tunables"][
            "nominal_pose"
        ] = {"x": 217.0, "y": -173.0, "rotation": 180.0}
        requested["active_backend_id"] = "sim.default"
        requested["simulator"] = {"pid": 12345}
        client = FakeMuJoCoClient()
        host._client = client

        with mock.patch.object(host, "_start_mujoco") as start_mujoco:
            host.restart_mujoco(lab_state=requested)

        self.assertTrue(client.stopped)
        start_mujoco.assert_called_once_with(show_viewer=None, realtime=None)
        self.assertEqual(host.edge_session_id, original_session)
        self.assertEqual(
            tunables(host, "tag_18")["nominal_pose"],
            {"x": 217.0, "y": -173.0, "rotation": 180.0},
        )
        self.assertEqual(host._poses["tag_18"]["x"], 217.0)
        self.assertNotIn("active_backend_id", host.current_state)
        self.assertNotIn("simulator", host.current_state)

    async def test_move_component_uses_short_edge_grasp(self):
        host = make_host()
        client = FakeMuJoCoClient()
        host._client = client

        await host.move_component(
            "tag_11",
            x=-250.0,
            y=200.0,
            rotation=0.0,
        )

        self.assertEqual(client.calls[0][1]["grasp_policy"], "short_edges")
        self.assertEqual(client.calls[0][1]["pickup_context"], "table")

    async def test_store_uses_next_slot_and_short_edge_grasp(self):
        host = make_host()
        client = FakeMuJoCoClient()
        host._client = client

        result = await host.store_component("tag_11")

        self.assertEqual(result["storage"]["slot"], {"i": 3, "j": 0})
        self.assertEqual((result["x"], result["y"]), (-46.0, -316.0))
        self.assertEqual(client.calls[0][1]["grasp_policy"], "short_edges")
        self.assertEqual(client.calls[0][1]["pickup_context"], "table")
        self.assertEqual(tunables(host, "tag_11")["presence"], "storage")

    async def test_place_button_and_drag_share_place_from_storage_motion(self):
        host = make_host()
        client = FakeMuJoCoClient()
        host._client = client

        result = await host.place_from_storage(
            "tag_9",
            x=250.0,
            y=200.0,
            rotation=45.0,
        )

        self.assertEqual(client.calls[0][1]["grasp_policy"], "short_edges")
        self.assertEqual(client.calls[0][1]["pickup_context"], "storage")
        self.assertEqual(result["presence"], "breadboard")
        self.assertEqual(tunables(host, "tag_9")["storage"]["slot"], None)
        self.assertEqual(tunables(host, "tag_9")["nominal_pose"]["rotation"], 45.0)

    async def test_failed_motion_does_not_commit_storage_transition(self):
        host = make_host()
        host._client = FakeMuJoCoClient(fail=True)

        with self.assertRaisesRegex(RuntimeError, "preflight rejected"):
            await host.store_component("tag_11")

        self.assertEqual(tunables(host, "tag_11")["presence"], "breadboard")
        self.assertIsNone(tunables(host, "tag_11")["storage"]["slot"])

    async def test_recenter_preserves_slot_and_uses_storage_pickup(self):
        host = make_host()
        client = FakeMuJoCoClient()
        host._client = client
        host._set_pose("tag_9", -307.0, -310.0, 12.0)

        result = await host.recenter_stored_in_inventory("tag_9")

        self.assertEqual((result["x"], result["y"]), (-316.0, -316.0))
        self.assertEqual(result["rotation"], 0.0)
        self.assertEqual(result["storage"]["slot"], {"i": 0, "j": 0})
        self.assertEqual(client.calls[0][1]["grasp_policy"], "short_edges")
        self.assertEqual(client.calls[0][1]["pickup_context"], "storage")
        self.assertEqual(tunables(host, "tag_9")["presence"], "storage")
        self.assertEqual(
            tunables(host, "tag_9")["storage"]["slot"],
            {"i": 0, "j": 0},
        )

    async def test_repack_moves_to_a_different_free_slot(self):
        host = make_host()
        client = FakeMuJoCoClient()
        host._client = client

        result = await host.repack_storage_slot("tag_9")

        self.assertEqual(result["storage"]["slot"], {"i": 3, "j": 0})
        self.assertEqual((result["x"], result["y"]), (-46.0, -316.0))
        self.assertEqual(client.calls[0][1]["grasp_policy"], "short_edges")
        self.assertEqual(client.calls[0][1]["pickup_context"], "storage")
        self.assertNotEqual(result["storage"]["slot"], {"i": 0, "j": 0})

    async def test_failed_recenter_preserves_pose_and_slot(self):
        host = make_host()
        host._set_pose("tag_9", -307.0, -310.0, 12.0)
        before = copy.deepcopy(tunables(host, "tag_9"))
        host._client = FakeMuJoCoClient(fail=True)

        with self.assertRaisesRegex(RuntimeError, "preflight rejected"):
            await host.recenter_stored_in_inventory("tag_9")

        self.assertEqual(tunables(host, "tag_9"), before)

    async def test_failed_repack_preserves_pose_and_slot(self):
        host = make_host()
        before = copy.deepcopy(tunables(host, "tag_9"))
        host._client = FakeMuJoCoClient(fail=True)

        with self.assertRaisesRegex(RuntimeError, "preflight rejected"):
            await host.repack_storage_slot("tag_9")

        self.assertEqual(tunables(host, "tag_9"), before)

    async def test_recenter_requires_an_assigned_slot(self):
        host = make_host()
        tunables(host, "tag_9")["storage"]["slot"] = None

        with self.assertRaisesRegex(ValueError, "no assigned storage slot"):
            await host.recenter_stored_in_inventory("tag_9")

    def test_simulation_edge_advertises_storage_recovery_primitives(self):
        capabilities = json.loads(
            (
                LAB_VIEW.parent / "cloudlabs_edge" / "capabilities.json"
            ).read_text(encoding="utf-8")
        )

        for primitive in ("REPACK_STORAGE", "RECENTER_IN_STORAGE"):
            self.assertIn(primitive, SimulationHost.supported_primitives)
            self.assertIn(
                primitive,
                capabilities["supported_primitives"],
            )


if __name__ == "__main__":
    unittest.main()
