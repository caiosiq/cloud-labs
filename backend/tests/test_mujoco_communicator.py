from __future__ import annotations

import asyncio
import copy
import unittest

from lab_communicator.mujoco.client import SimulatorProcessError
from lab_communicator.mujoco.communicator import MujocoLabCommunicator

from tests.test_mujoco_runtime import LAYOUT, catalog_row, component


class FakeClient:
    fail = False

    def __init__(self, scene, **kwargs):
        del kwargs
        self.scene = scene
        self.started = False
        self.stopped = False
        self.moves = []

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def status(self):
        return {
            "running": self.started and not self.stopped,
            "pid": 123,
            "viewer": False,
            "realtime": False,
            "last_error": None,
        }

    def move_component(
        self,
        tag_id,
        *,
        target_x_mm,
        target_y_mm,
        target_rotation_deg,
    ):
        if self.fail:
            raise SimulatorProcessError("planned failure")
        self.moves.append(tag_id)
        return {
            "x_mm": target_x_mm,
            "y_mm": target_y_mm,
            "rotation_deg": target_rotation_deg,
        }


def communicator_state():
    return {
        "system_status": "IDLE",
        "components": {"tag_pick": component(300, -180)},
        "holding": {},
        "optimization_step": 0,
        "optimization_target_id": None,
    }


class MujocoCommunicatorTests(unittest.TestCase):
    def make_communicator(self, client_type=FakeClient):
        return MujocoLabCommunicator(
            communicator_state(),
            [catalog_row("tag_pick")],
            LAYOUT,
            client_factory=client_type,
            show_viewer=False,
            realtime=False,
        )

    def test_success_commits_only_supported_move(self):
        communicator = self.make_communicator()
        asyncio.run(
            communicator.move_component(
                "tag_pick",
                {"target_x": 320, "target_y": -60, "rotation": 15},
            )
        )
        state = communicator.get_lab_state()
        nominal = state["components"]["tag_pick"]["statecontrol"]["tunables"][
            "nominal_pose"
        ]
        self.assertEqual(nominal, {"x": 320.0, "y": -60.0, "rotation": 15.0})
        self.assertEqual(state["system_status"], "IDLE")
        self.assertTrue(communicator.supports_primitive("MOVE_COMPONENT"))
        self.assertFalse(communicator.supports_primitive("PICK_COMPONENT"))

    def test_failure_preserves_nominal_pose(self):
        class FailingClient(FakeClient):
            def move_component(self, *args, **kwargs):
                del args, kwargs
                raise SimulatorProcessError(
                    "planned failure",
                    details={
                        "request_id": "sim-request-1",
                        "log_path": "logs/mujoco_sessions/example.jsonl",
                    },
                )

        communicator = self.make_communicator(FailingClient)
        before = copy.deepcopy(communicator.get_lab_state())
        asyncio.run(
            communicator.move_component(
                "tag_pick",
                {"target_x": 320, "target_y": -60, "rotation": 15},
            )
        )
        after = communicator.get_lab_state()
        before_pose = before["components"]["tag_pick"]["statecontrol"]["tunables"][
            "nominal_pose"
        ]
        after_pose = after["components"]["tag_pick"]["statecontrol"]["tunables"][
            "nominal_pose"
        ]
        self.assertEqual(after_pose, before_pose)
        self.assertEqual(after["system_status"], "IDLE")
        self.assertEqual(after["last_runtime_error"]["target_id"], "tag_pick")
        self.assertEqual(after["last_runtime_error"]["message"], "planned failure")
        self.assertEqual(
            after["last_runtime_error"]["details"]["request_id"],
            "sim-request-1",
        )
        self.assertEqual(
            after["last_runtime_error"]["details"]["log_path"],
            "logs/mujoco_sessions/example.jsonl",
        )

    def test_success_clears_previous_runtime_error(self):
        communicator = self.make_communicator()
        communicator.client.fail = True
        asyncio.run(
            communicator.move_component(
                "tag_pick",
                {"target_x": 310, "target_y": -80, "rotation": 0},
            )
        )
        self.assertIsNotNone(communicator.get_lab_state()["last_runtime_error"])

        communicator.client.fail = False
        asyncio.run(
            communicator.move_component(
                "tag_pick",
                {"target_x": 320, "target_y": -60, "rotation": 15},
            )
        )
        self.assertIsNone(communicator.get_lab_state()["last_runtime_error"])

    def test_lab_state_exposes_simulator_progress(self):
        class ProgressClient(FakeClient):
            def status(self):
                status = super().status()
                status["progress"] = {
                    "message": "MoveIt pre-pick feasibility seed 2/4.",
                    "phase": "prepick_feasibility",
                    "seed_index": 2,
                    "seed_total": 4,
                }
                return status

        communicator = self.make_communicator(ProgressClient)
        state = communicator.get_lab_state()

        self.assertEqual(
            state["simulator"]["progress"]["message"],
            "MoveIt pre-pick feasibility seed 2/4.",
        )


if __name__ == "__main__":
    unittest.main()
