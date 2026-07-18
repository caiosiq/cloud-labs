from __future__ import annotations

import copy
import unittest

from mock_edge.host.runtime_mode import RuntimeLabProxy, RuntimeModeError


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
        pass

    def get_runtime_info(self):
        return {
            "active_mode": "mock",
            "physical_armed": False,
            "last_error": None,
        }

    def supports_primitive(self, action):
        return True


class RuntimeModeTests(unittest.TestCase):
    def test_mujoco_switch_parked(self) -> None:
        proxy = RuntimeLabProxy(FakeMock())
        with self.assertRaises(RuntimeModeError):
            proxy.switch_mode("mujoco")

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
