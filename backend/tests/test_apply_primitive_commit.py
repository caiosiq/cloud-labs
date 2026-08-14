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
    SYSTEM_STATUS_BUSY,
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

    def test_recenter_in_storage_updates_nominal_pose(self) -> None:
        """After Refresh Pose leaves a part off-center, RECENTER must commit cell center."""
        state = _seed_component(x=-115.0, y=-78.0)
        tun0 = state["components"]["tag_10"]["statecontrol"]["tunables"]
        tun0["presence"] = "storage"
        tun0["placement"] = {"mode": "STORAGE"}
        tun0["storage"] = {"in_storage": True, "slot": {"i": 1, "j": 2}}
        ok = apply_in_air_commit(
            state,
            "RECENTER_IN_STORAGE",
            {"action": "RECENTER_IN_STORAGE", "target_id": "tag_10", "parameters": {}},
            {
                "tag_id": "tag_10",
                "pose": {"x": -120.0, "y": -80.0, "rotation": 0.0},
                "slot_i": 1,
                "slot_j": 2,
                "presence": "STORED",
            },
        )
        self.assertTrue(ok)
        tun = state["components"]["tag_10"]["statecontrol"]["tunables"]
        self.assertEqual(tun["presence"], "storage")
        self.assertEqual(tun["nominal_pose"]["x"], -120.0)
        self.assertEqual(tun["nominal_pose"]["y"], -80.0)
        self.assertEqual(tun["nominal_pose"]["rotation"], 0.0)
        self.assertEqual(tun["storage"]["slot"]["i"], 1)
        self.assertEqual(tun["storage"]["slot"]["j"], 2)

    def test_start_teleop_marks_active_ready_and_system_teleop(self) -> None:
        from lab_model.language.domain.holding import SYSTEM_STATUS_TELEOP

        state = _seed_component("tag_22")
        ok = apply_in_air_commit(
            state,
            "START_TELEOP",
            {"action": "START_TELEOP", "target_id": "tag_22"},
            {"tag_id": "tag_22", "active": True},
        )
        self.assertTrue(ok)
        teleop = state["components"]["tag_22"]["telemetry"]["teleop"]
        self.assertTrue(teleop["active"])
        self.assertTrue(teleop["ready"])
        self.assertEqual(state["system_status"], SYSTEM_STATUS_TELEOP)

    def test_start_end_live_feed_commits_stream_channel(self) -> None:
        state = _seed_component("tag_22")
        ok = apply_in_air_commit(
            state,
            "START_LIVE_FEED",
            {
                "action": "START_LIVE_FEED",
                "target_id": "tag_22",
                "channel": "stream",
            },
            {"channel": "tag_22.camera_image", "active": True},
        )
        self.assertTrue(ok)
        stream = state["components"]["tag_22"]["telemetry"]["live_feed"]["stream"]
        self.assertTrue(stream["live"])
        self.assertTrue(stream["connected"])

        ok2 = apply_in_air_commit(
            state,
            "END_LIVE_FEED",
            {
                "action": "END_LIVE_FEED",
                "target_id": "tag_22",
                "channel": "all",
            },
            {"active": False},
        )
        self.assertTrue(ok2)
        stream2 = state["components"]["tag_22"]["telemetry"]["live_feed"]["stream"]
        self.assertFalse(stream2["live"])
        self.assertFalse(stream2["connected"])

    def test_start_teleop_pending_then_ready_phases(self) -> None:
        from lab_model.language.domain.holding import (
            SYSTEM_STATUS_BUSY,
            SYSTEM_STATUS_TELEOP,
        )

        state = _seed_component("tag_22")
        ok = apply_in_air_commit(
            state,
            "START_TELEOP",
            {"action": "START_TELEOP", "target_id": "tag_22", "_teleop_phase": "pending"},
            {},
        )
        self.assertTrue(ok)
        teleop = state["components"]["tag_22"]["telemetry"]["teleop"]
        self.assertTrue(teleop["active"])
        self.assertFalse(teleop["ready"])
        # Acquiring: monitor shows BUSY (matches TeleOp board Loading).
        self.assertEqual(state["system_status"], SYSTEM_STATUS_BUSY)
        ok2 = apply_in_air_commit(
            state,
            "START_TELEOP",
            {"action": "START_TELEOP", "target_id": "tag_22", "_teleop_phase": "ready"},
            {"tag_id": "tag_22"},
        )
        self.assertTrue(ok2)
        self.assertTrue(state["components"]["tag_22"]["telemetry"]["teleop"]["ready"])
        self.assertEqual(state["system_status"], SYSTEM_STATUS_TELEOP)

    def test_end_teleop_clears_session(self) -> None:
        from lab_model.language.domain.holding import SYSTEM_STATUS_TELEOP

        state = _seed_component("tag_22")
        apply_in_air_commit(
            state,
            "START_TELEOP",
            {"action": "START_TELEOP", "target_id": "tag_22"},
            {"tag_id": "tag_22", "active": True},
        )
        self.assertEqual(state["system_status"], SYSTEM_STATUS_TELEOP)
        ok = apply_in_air_commit(
            state,
            "END_TELEOP",
            {"action": "END_TELEOP", "target_id": "tag_22"},
            {"tag_id": "tag_22"},
        )
        self.assertTrue(ok)
        teleop = state["components"]["tag_22"]["telemetry"]["teleop"]
        self.assertFalse(teleop["active"])
        self.assertFalse(teleop["ready"])
        self.assertEqual(state["system_status"], SYSTEM_STATUS_IDLE)

    def test_end_teleop_commits_final_pose(self) -> None:
        """END_TELEOP must keep the teleop end pose (not revert to start)."""
        state = _seed_component("tag_22")
        tun = state["components"]["tag_22"]["statecontrol"]["tunables"]
        tun["nominal_pose"] = {"x": 10.0, "y": 20.0, "rotation": 0.0}
        apply_in_air_commit(
            state,
            "START_TELEOP",
            {"action": "START_TELEOP", "target_id": "tag_22"},
            {"tag_id": "tag_22", "active": True},
        )
        # Breadboard → rz mode: only rotation should update.
        ok = apply_in_air_commit(
            state,
            "END_TELEOP",
            {"action": "END_TELEOP", "target_id": "tag_22"},
            {
                "tag_id": "tag_22",
                "active": False,
                "final_pose": {"x": 99.0, "y": 88.0, "z": 50.0, "rotation": 42.5},
            },
        )
        self.assertTrue(ok)
        pose = state["components"]["tag_22"]["statecontrol"]["tunables"]["nominal_pose"]
        self.assertEqual(pose["x"], 10.0)
        self.assertEqual(pose["y"], 20.0)
        self.assertAlmostEqual(float(pose["rotation"]), 42.5)
        teleop = state["components"]["tag_22"]["telemetry"]["teleop"]
        self.assertFalse(teleop["active"])

    def test_set_exposure_commits_tunable(self) -> None:
        state = _seed_component("tag_22")
        state["components"]["tag_22"]["statecontrol"]["tunables"][
            "exposure_time_ms"
        ] = 200.0
        ok = apply_in_air_commit(
            state,
            "SET_EXPOSURE",
            {
                "action": "SET_EXPOSURE",
                "target_id": "tag_22",
                "parameters": {"exposure_time_ms": 50.0},
            },
            {"tag_id": "tag_22", "exposure_time_ms": 50.0},
        )
        self.assertTrue(ok)
        self.assertEqual(
            state["components"]["tag_22"]["statecontrol"]["tunables"][
                "exposure_time_ms"
            ],
            50.0,
        )

    def test_set_laser_output_commits_tunable(self) -> None:
        state = _seed_component("tag_11")
        ok = apply_in_air_commit(
            state,
            "SET_LASER_OUTPUT",
            {
                "action": "SET_LASER_OUTPUT",
                "target_id": "tag_11",
                "parameters": {"output_power_mw": 12.5},
            },
            {"tag_id": "tag_11", "output_power_mw": 12.5},
        )
        self.assertTrue(ok)
        self.assertEqual(
            state["components"]["tag_11"]["statecontrol"]["tunables"][
                "output_power_mw"
            ],
            12.5,
        )

    def test_move_motor_commits_absolute_angle_from_edge(self) -> None:
        state = _seed_component("tag_20")
        state["components"]["tag_20"]["statecontrol"]["tunables"][
            "nominal_motor_positions"
        ] = {"1": 0.0}
        ok = apply_in_air_commit(
            state,
            "MOVE_MOTOR",
            {
                "action": "MOVE_MOTOR",
                "target_id": "tag_20",
                "parameters": {"motor_id": 1, "distance": 12.5},
            },
            {
                "tag_id": "tag_20",
                "motor_id": 1,
                "units": "deg",
                "distance_deg": 12.5,
                "angle_deg": 12.5,
            },
        )
        self.assertTrue(ok)
        nmp = state["components"]["tag_20"]["statecontrol"]["tunables"][
            "nominal_motor_positions"
        ]
        self.assertAlmostEqual(float(nmp["1"]), 12.5)

    def test_move_motor_commits_relative_delta_without_absolute(self) -> None:
        state = _seed_component("tag_20")
        state["components"]["tag_20"]["statecontrol"]["tunables"][
            "nominal_motor_positions"
        ] = {"1": 10.0}
        ok = apply_in_air_commit(
            state,
            "MOVE_MOTOR",
            {
                "action": "MOVE_MOTOR",
                "target_id": "tag_20",
                "parameters": {"motor_id": 1, "distance": 2.5},
            },
            {"tag_id": "tag_20", "motor_id": 1, "units": "steps", "steps": 2},
        )
        self.assertTrue(ok)
        nmp = state["components"]["tag_20"]["statecontrol"]["tunables"][
            "nominal_motor_positions"
        ]
        # No absolute angle → add params/result delta to prior tunable (2 from steps).
        self.assertAlmostEqual(float(nmp["1"]), 12.0)

    def test_set_motor_setpoint_commits_angle(self) -> None:
        state = _seed_component("tag_20")
        ok = apply_in_air_commit(
            state,
            "SET_MOTOR_SETPOINT",
            {
                "action": "SET_MOTOR_SETPOINT",
                "target_id": "tag_20",
                "parameters": {"motor_id": 1, "angle_deg": 70.0},
            },
            {"tag_id": "tag_20", "motor_id": 1, "units": "deg", "angle_deg": 70.0},
        )
        self.assertTrue(ok)
        nmp = state["components"]["tag_20"]["statecontrol"]["tunables"][
            "nominal_motor_positions"
        ]
        self.assertAlmostEqual(float(nmp["1"]), 70.0)

    def test_motor_set_zero_commits_zero(self) -> None:
        state = _seed_component("tag_20")
        state["components"]["tag_20"]["statecontrol"]["tunables"][
            "nominal_motor_positions"
        ] = {"1": 33.0}
        ok = apply_in_air_commit(
            state,
            "MOTOR_SET_ZERO",
            {
                "action": "MOTOR_SET_ZERO",
                "target_id": "tag_20",
                "parameters": {"motor_id": 1},
            },
            {"tag_id": "tag_20", "motor_id": 1, "angle_deg": 0.0},
        )
        self.assertTrue(ok)
        nmp = state["components"]["tag_20"]["statecontrol"]["tunables"][
            "nominal_motor_positions"
        ]
        self.assertAlmostEqual(float(nmp["1"]), 0.0)

    def test_record_tunables_commits_checked_poses_only(self) -> None:
        state = _seed_component("tag_20")
        state["components"]["tag_9"] = {
            "id": "tag_9",
            "statecontrol": {
                "tunables": {
                    "presence": "breadboard",
                    "nominal_pose": {"x": 1.0, "y": 2.0, "rotation": 3.0},
                },
                "measurables": {},
            },
        }
        ok = apply_in_air_commit(
            state,
            "RECORD_TUNABLES",
            {
                "action": "RECORD_TUNABLES",
                "parameters": {
                    "tag_ids": ["tag_20"],
                    "tunable_paths": ["nominal_pose"],
                },
            },
            {
                "tag_ids": ["tag_20"],
                "values": {
                    "tag_20": {
                        "nominal_pose": {"x": 100.0, "y": 200.0, "rotation": 45.0}
                    }
                },
                "poses": {
                    "tag_20": {"x": 100.0, "y": 200.0, "rotation": 45.0},
                },
            },
        )
        self.assertTrue(ok)
        pose20 = state["components"]["tag_20"]["statecontrol"]["tunables"][
            "nominal_pose"
        ]
        self.assertAlmostEqual(float(pose20["x"]), 100.0)
        self.assertAlmostEqual(float(pose20["y"]), 200.0)
        # Unchecked tag stays frozen.
        pose9 = state["components"]["tag_9"]["statecontrol"]["tunables"]["nominal_pose"]
        self.assertAlmostEqual(float(pose9["x"]), 1.0)

    def test_merge_keeps_coordinator_teleop_over_edge_inactive(self) -> None:
        from lab_model.language.domain.holding import SYSTEM_STATUS_TELEOP

        state = _seed_component("tag_22")
        apply_in_air_commit(
            state,
            "START_TELEOP",
            {"action": "START_TELEOP", "target_id": "tag_22"},
            {"tag_id": "tag_22", "active": True},
        )
        edge = {
            "system_status": SYSTEM_STATUS_IDLE,
            "runtime_sync": {"status": "ready"},
            "components": {
                "tag_22": {
                    "telemetry": {
                        "teleop": {"active": False, "ready": False},
                        "live_feed": {"stream": {"live": True}},
                    }
                }
            },
        }
        merged = merge_lab_state_for_twin(state, edge, backend_id="real.default")
        self.assertEqual(merged["system_status"], SYSTEM_STATUS_TELEOP)
        teleop = merged["components"]["tag_22"]["telemetry"]["teleop"]
        self.assertTrue(teleop["active"])
        self.assertTrue(teleop["ready"])
        # live_feed session is coordinator-owned (same as teleop). Edge claiming
        # live must not flip Twin after teleop seeded an idle stream channel.
        self.assertFalse(
            merged["components"]["tag_22"]["telemetry"]["live_feed"]["stream"]["live"]
        )

        apply_in_air_commit(
            state,
            "START_LIVE_FEED",
            {
                "action": "START_LIVE_FEED",
                "target_id": "tag_22",
                "channel": "stream",
            },
            {"active": True},
        )
        edge_idle_live = {
            "system_status": SYSTEM_STATUS_IDLE,
            "runtime_sync": {"status": "ready"},
            "components": {
                "tag_22": {
                    "telemetry": {
                        "teleop": {"active": False},
                        "live_feed": {"stream": {"live": False}},
                    }
                }
            },
        }
        merged2 = merge_lab_state_for_twin(
            state, edge_idle_live, backend_id="real.default"
        )
        self.assertTrue(
            merged2["components"]["tag_22"]["telemetry"]["live_feed"]["stream"]["live"]
        )


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
            client.transport = EdgeTransport.IN_PROCESS
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
            client.transport = EdgeTransport.HTTP
            client.execute_command = AsyncMock(return_value=fake_result)
            edge_for.return_value = client

            import main as main_mod

            out = await main_mod._southbound_execute(
                {"action": "PICK_COMPONENT", "target_id": "tag_10", "parameters": {}},
                backend_id="real.default",
            )
            self.assertTrue(out.ok)
            self.assertEqual(store.snapshot()["system_status"], SYSTEM_STATUS_HOLDING)

    async def test_southbound_sets_busy_during_http_execute(self) -> None:
        from lab_model.execution.edge.client import EdgeExecuteResult, EdgeTransport
        import asyncio
        import main as main_mod

        fake_result = EdgeExecuteResult(
            ok=True,
            transport=EdgeTransport.HTTP,
            result={
                "tag_id": "tag_10",
                "pose": {"x": 50.0, "y": 60.0, "rotation": 0.0},
            },
        )
        tmp = Path(tempfile.mkdtemp(prefix="sb_busy_"))
        self.addCleanup(shutil.rmtree, tmp, True)
        store = LabStateStore("real.default", str(tmp / "lab_state.json"))
        store.replace_state(_seed_component(), persist=True)

        rt = MagicMock()
        rt.backend_id = "real.default"
        rt.lab_state_store = store
        rt.paths.lab_state_json = store.path

        saw_busy = {"value": False}

        async def _slow_execute(*_a, **_k):
            await asyncio.sleep(0.01)
            saw_busy["value"] = store.snapshot().get("system_status") == SYSTEM_STATUS_BUSY
            return fake_result

        with patch("main._edge_client_for") as edge_for, patch(
            "main.require_backend", return_value=rt
        ):
            client = MagicMock()
            client.transport = EdgeTransport.HTTP
            client.execute_command = AsyncMock(side_effect=_slow_execute)
            edge_for.return_value = client

            out = await main_mod._southbound_execute(
                {
                    "action": "MOVE_COMPONENT",
                    "target_id": "tag_10",
                    "parameters": {"target_x": 50.0, "target_y": 60.0, "rotation": 0.0},
                },
                backend_id="real.default",
            )
            self.assertTrue(out.ok)
            self.assertTrue(saw_busy["value"])
            self.assertEqual(store.snapshot()["system_status"], SYSTEM_STATUS_IDLE)

    async def test_southbound_restore_prior_on_http_failure(self) -> None:
        from lab_model.execution.edge.client import EdgeExecuteResult, EdgeTransport
        import main as main_mod

        fake_result = EdgeExecuteResult(
            ok=False,
            transport=EdgeTransport.HTTP,
            error="arm fault",
        )
        tmp = Path(tempfile.mkdtemp(prefix="sb_fail_"))
        self.addCleanup(shutil.rmtree, tmp, True)
        store = LabStateStore("real.default", str(tmp / "lab_state.json"))
        seed = _seed_component()
        seed["system_status"] = SYSTEM_STATUS_IDLE
        store.replace_state(seed, persist=True)

        rt = MagicMock()
        rt.backend_id = "real.default"
        rt.lab_state_store = store
        rt.paths.lab_state_json = store.path

        with patch("main._edge_client_for") as edge_for, patch(
            "main.require_backend", return_value=rt
        ):
            client = MagicMock()
            client.transport = EdgeTransport.HTTP
            client.execute_command = AsyncMock(return_value=fake_result)
            edge_for.return_value = client

            with self.assertRaises(Exception):
                await main_mod._southbound_execute(
                    {
                        "action": "MOVE_COMPONENT",
                        "target_id": "tag_10",
                        "parameters": {"target_x": 1.0, "target_y": 2.0, "rotation": 0.0},
                    },
                    backend_id="real.default",
                )
            self.assertEqual(store.snapshot()["system_status"], SYSTEM_STATUS_IDLE)


if __name__ == "__main__":
    unittest.main()
