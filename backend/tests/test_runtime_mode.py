from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from lab_communicator.runtime_mode import RuntimeLabProxy, RuntimeModeError


class FakeMock:
    def __init__(self):
        self.state = {
            "system_status": "IDLE",
            "components": {},
            "holding": {},
            "optimization_target_id": None,
        }
        self.loaded = None

    def get_lab_state(self):
        return copy.deepcopy(self.state)

    def get_catalog(self):
        return []

    def set_lab_state(self, state):
        self.loaded = copy.deepcopy(state)
        self.state = copy.deepcopy(state)

    def shutdown_lab_processes(self):
        return None

    def stop_teleop_sweeper(self, **kwargs):
        del kwargs


class FakeMujoco(FakeMock):
    def __init__(self, state, catalog, layout):
        del catalog, layout
        super().__init__()
        self.state = copy.deepcopy(state)
        self.stopped = False

    def simulator_status(self):
        return {
            "running": not self.stopped,
            "pid": 456,
            "viewer": True,
            "realtime": True,
            "last_error": None,
        }

    def supports_primitive(self, action):
        return action == "MOVE_COMPONENT"

    def shutdown_lab_processes(self):
        self.stopped = True


class RuntimeModeTests(unittest.TestCase):
    @patch("lab_communicator.runtime_mode.load_layout_document", return_value={})
    @patch("lab_communicator.runtime_mode.MujocoLabCommunicator", FakeMujoco)
    def test_switches_mock_to_mujoco_and_back_without_physical_option(
        self,
        _layout,
    ):
        mock = FakeMock()
        proxy = RuntimeLabProxy(mock)
        info = proxy.switch_mode("mujoco")
        self.assertEqual(info["active_mode"], "mujoco")
        self.assertFalse(info["physical_armed"])
        self.assertTrue(proxy.supports_primitive("MOVE_COMPONENT"))
        self.assertFalse(proxy.supports_primitive("PICK_COMPONENT"))

        proxy.get_lab_state()["system_status"]
        info = proxy.switch_mode("mock")
        self.assertEqual(info["active_mode"], "mock")
        self.assertIsNotNone(mock.loaded)

    def test_refuses_busy_and_reserved_switches(self):
        mock = FakeMock()
        proxy = RuntimeLabProxy(mock)
        mock.state["system_status"] = "BUSY"
        with self.assertRaises(RuntimeModeError):
            proxy.switch_mode("mujoco")

        mock.state["system_status"] = "IDLE"
        _, token = proxy.reserve_operation()
        try:
            with self.assertRaises(RuntimeModeError):
                proxy.switch_mode("mujoco")
        finally:
            proxy.release_operation(token)

    def test_physical_mode_cannot_be_selected(self):
        proxy = RuntimeLabProxy(FakeMock())
        with self.assertRaises(RuntimeModeError):
            proxy.switch_mode("physical")


if __name__ == "__main__":
    unittest.main()

