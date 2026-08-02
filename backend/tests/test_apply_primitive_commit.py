"""Phase 3: coordinator commits after remote edge success."""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from lab_model.coordinator.state.apply_primitive_commit import (
    apply_edge_primitive_commit,
    apply_in_air_commit,
    canonical_action,
)
from lab_model.coordinator.state.lab_state_store import LabStateStore
from lab_model.coordinator.state.merge_lab_state import merge_lab_state_for_twin
from lab_model.language.domain.holding import (
    SYSTEM_STATUS_HOLDING,
    SYSTEM_STATUS_IDLE,
)


def _seed_component(tag: str = "tag_10", *, x: float = 10.0, y: float = 20.0) -> dict:
    return {
        "system_status": SYSTEM_STATUS_IDLE,
        "holding": {"tag_id": None, "nominal_pose": None, "requires_operator_confirm": False},
        "components": {
            tag: {
                "id": tag,
                "statecontrol": {
                    "tunables": {
                        "presence": "breadboard",
                        "nominal_pose": {"x": x, "y": y, "rotation": 45.0},
                        "reported_pose": {"x": x, "y": y, "rotation": 45.0},
                        "placement": {"mode": "MANUAL"},
                    },
                    "measurables": {},
                },
                "telemetry": {"teleop": {"active": False}},
            }
        },
    }


class ApplyInAirCommitTests(unittest.TestCase):
    def test_canonical_aliases(self) -> None:
        self.assertEqual(canonical_action({"action": "PICK"}), "PICK_COMPONENT")
        self.assertEqual(
            canonical_action({"action": "CONFIRM_HOLDING"}), "CONFIRM_HOLDING_TAG"
        )

    def test_record_measurables_commits_lazy_envelope(self) -> None:
        state = _seed_component("tag_22")
        envelope = {
            "tag_id": "tag_22",
            "field": "camera_image",
            "dtype": "uint8",
            "shape": [480, 640, 3],
            "data": {
                "kind": "url",
                "href": "/measurables/tag_22/camera_image.jpg",
                "format": "jpeg",
            },
        }
        ok = apply_in_air_commit(
            state,
            "RECORD_MEASURABLES",
            {"action": "RECORD_MEASURABLES", "target_id": "tag_22"},
            {"tag_id": "tag_22", "measurables": {"camera_image": envelope}},
        )
        self.assertTrue(ok)
        stored = state["components"]["tag_22"]["statecontrol"]["measurables"][
            "camera_image"
        ]
        self.assertEqual(stored["shape"], [480, 640, 3])
        self.assertEqual(
            stored["data"]["href"], "/measurables/tag_22/camera_image.jpg"
        )

    def test_record_measurables_skips_empty_result(self) -> None:
        state = _seed_component("tag_22")
        ok = apply_in_air_commit(
            state,
            "RECORD_MEASURABLES",
            {"action": "RECORD_MEASURABLES", "target_id": "tag_22"},
            {"tag_id": "tag_22"},
        )
        self.assertFalse(ok)
        self.assertEqual(
            state["components"]["tag_22"]["statecontrol"]["measurables"], {}
        )

    def test_pick_sets_holding_from_deathray_shape(self) -> None:
        state = _seed_component()
        ok = apply_in_air_commit(
            state,
            "PICK_COMPONENT",
            {"action": "PICK_COMPONENT", "target_id": "tag_10", "parameters": {}},
            {"tag_id": "tag_10", "holding": True},
        )
        self.assertTrue(ok)
        self.assertEqual(state["system_status"], SYSTEM_STATUS_HOLDING)
        self.assertEqual(state["holding"]["tag_id"], "tag_10")
        pose = state["components"]["tag_10"]["statecontrol"]["tunables"]["nominal_pose"]
        self.assertEqual(pose["x"], 10.0)
        self.assertEqual(pose["y"], 20.0)
        self.assertIn("z", pose)

    def test_pick_skips_when_holding_false(self) -> None:
        state = _seed_component()
        ok = apply_in_air_commit(
            state,
            "PICK_COMPONENT",
            {"action": "PICK", "target_id": "tag_10"},
            {"tag_id": "tag_10", "holding": False},
        )
        self.assertFalse(ok)
        self.assertEqual(state["system_status"], SYSTEM_STATUS_IDLE)

    def test_hover_and_place(self) -> None:
        state = _seed_component()
        apply_in_air_commit(
            state,
            "PICK_COMPONENT",
            {"action": "PICK_COMPONENT", "target_id": "tag_10"},
            {"holding": True},
        )
        apply_in_air_commit(
            state,
            "HOVER",
            {
                "action": "HOVER",
                "target_id": "tag_10",
                "parameters": {
                    "target_x": 50.0,
                    "target_y": 60.0,
                    "rotation": 0.0,
                    "z": 245.0,
                },
            },
            {"tag_id": "tag_10", "pose": {"x": 50.0, "y": 60.0, "z": 245.0, "rotation": 0.0}},
        )
        self.assertEqual(state["system_status"], SYSTEM_STATUS_HOLDING)
        self.assertEqual(state["holding"]["nominal_pose"]["x"], 50.0)

        apply_in_air_commit(
            state,
            "PLACE_FROM_HOVER",
            {
                "action": "PLACE_FROM_HOVER",
                "target_id": "tag_10",
                "parameters": {"target_x": 70.0, "target_y": 80.0, "rotation": 10.0},
            },
            {"tag_id": "tag_10", "presence": "PLACED", "pose": {"x": 70.0, "y": 80.0}},
        )
        self.assertEqual(state["system_status"], SYSTEM_STATUS_IDLE)
        self.assertIsNone(state["holding"].get("tag_id"))

    def test_confirm_holding(self) -> None:
        state = _seed_component()
        apply_in_air_commit(
            state,
            "PICK_COMPONENT",
            {"action": "PICK_COMPONENT", "target_id": "tag_10"},
            {"holding": True},
        )
        state["holding"]["requires_operator_confirm"] = True
        ok = apply_in_air_commit(
            state,
            "CONFIRM_HOLDING_TAG",
            {"action": "CONFIRM_HOLDING", "target_id": "tag_10"},
            {
                "tag_id": "tag_10",
                "holding": True,
                "held_tag_id": "tag_10",
                "gripper_closed": True,
            },
        )
        self.assertTrue(ok)
        self.assertFalse(state["holding"]["requires_operator_confirm"])

    def test_move_to_breadboard(self) -> None:
        state = _seed_component()
        ok = apply_in_air_commit(
            state,
            "MOVE_COMPONENT",
            {
                "action": "MOVE_COMPONENT",
                "target_id": "tag_10",
                "parameters": {"target_x": 100.0, "target_y": 200.0, "rotation": 15.0},
            },
            {"tag_id": "tag_10", "pose": {"x": 101.0, "y": 199.0, "rotation": 15.0}},
        )
        self.assertTrue(ok)
        tun = state["components"]["tag_10"]["statecontrol"]["tunables"]
        self.assertEqual(tun["presence"], "breadboard")
        self.assertEqual(tun["nominal_pose"]["x"], 100.0)
        self.assertEqual(tun["reported_pose"]["x"], 101.0)

    def test_store_requires_slots(self) -> None:
        state = _seed_component()
        ok = apply_in_air_commit(
            state,
            "STORE_COMPONENT",
            {"action": "STORE_COMPONENT", "target_id": "tag_10", "parameters": {}},
            {"tag_id": "tag_10", "pose": {"x": -100.0, "y": -100.0}},
        )
        self.assertFalse(ok)

    def test_store_with_slots(self) -> None:
        state = _seed_component()
        ok = apply_in_air_commit(
            state,
            "STORE_COMPONENT",
            {"action": "STORE_COMPONENT", "target_id": "tag_10", "parameters": {}},
            {
                "tag_id": "tag_10",
                "pose": {"x": -120.0, "y": -80.0, "rotation": 0.0},
                "slot_i": 1,
                "slot_j": 2,
                "presence": "storage",
            },
        )
        self.assertTrue(ok)
        tun = state["components"]["tag_10"]["statecontrol"]["tunables"]
        self.assertEqual(tun["presence"], "storage")
        self.assertEqual(tun["storage"]["slot"]["i"], 1)
        self.assertEqual(tun["storage"]["slot"]["j"], 2)


class StoreAndMergeAfterPickTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="apply_commit_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.path = str(self.tmp / "lab_state.json")

    def test_store_pick_then_merge_wins_over_edge_idle(self) -> None:
        store = LabStateStore("real.default", self.path)
        store.replace_state(_seed_component(), persist=True)
        applied = apply_edge_primitive_commit(
            store,
            {"action": "PICK_COMPONENT", "target_id": "tag_10", "parameters": {}},
            edge_result={"tag_id": "tag_10", "holding": True},
            backend_id="real.default",
        )
        self.assertTrue(applied)
        edge_idle = {
            "system_status": SYSTEM_STATUS_IDLE,
            "holding": None,
            "runtime_sync": {"status": "ready"},
            "components": store.snapshot()["components"],
        }
        merged = merge_lab_state_for_twin(
            store.snapshot(), edge_idle, backend_id="real.default"
        )
        self.assertEqual(merged["system_status"], SYSTEM_STATUS_HOLDING)
        self.assertEqual(merged["holding"]["tag_id"], "tag_10")
        self.assertEqual(merged["runtime_sync"]["status"], "ready")


class SouthboundHookTests(unittest.IsolatedAsyncioTestCase):
    async def test_southbound_skips_in_process_commit(self) -> None:
        from lab_model.execution.edge.client import EdgeExecuteResult, EdgeTransport

        fake_result = EdgeExecuteResult(
            ok=True,
            transport=EdgeTransport.IN_PROCESS,
            result={"tag_id": "tag_10", "holding": True},
        )
        with patch("main._edge_client_for") as edge_for, patch(
            "lab_model.coordinator.state.apply_primitive_commit.apply_edge_primitive_commit"
        ) as apply_commit:
            client = MagicMock()
            client.execute_command = AsyncMock(return_value=fake_result)
            edge_for.return_value = client

            import main as main_mod

            out = await main_mod._southbound_execute(
                {"action": "PICK_COMPONENT", "target_id": "tag_10"},
                backend_id="mock.default",
            )
            self.assertTrue(out.ok)
            apply_commit.assert_not_called()

    async def test_southbound_commits_on_http(self) -> None:
        from lab_model.execution.edge.client import EdgeExecuteResult, EdgeTransport

        fake_result = EdgeExecuteResult(
            ok=True,
            transport=EdgeTransport.HTTP,
            result={"tag_id": "tag_10", "holding": True},
        )
        store = LabStateStore("real.default", str(self.tmp / "http.json") if hasattr(self, "tmp") else "")
        # IsolatedAsyncioTestCase may not have setUp from sibling — create store here.
        tmp = Path(tempfile.mkdtemp(prefix="sb_http_"))
        self.addCleanup(shutil.rmtree, tmp, True)
        store = LabStateStore("real.default", str(tmp / "lab_state.json"))
        store.replace_state(_seed_component(), persist=True)

        rt = MagicMock()
        rt.backend_id = "real.default"
        rt.lab_state_store = store
        rt.paths.lab_state_json = store.path

        with patch("main._edge_client_for") as edge_for, patch(
            "main.require_backend", return_value=rt
        ):
            client = MagicMock()
            client.execute_command = AsyncMock(return_value=fake_result)
            edge_for.return_value = client

            import main as main_mod

            out = await main_mod._southbound_execute(
                {"action": "PICK_COMPONENT", "target_id": "tag_10", "parameters": {}},
                backend_id="real.default",
            )
            self.assertTrue(out.ok)
            self.assertEqual(store.snapshot()["system_status"], SYSTEM_STATUS_HOLDING)


if __name__ == "__main__":
    unittest.main()
