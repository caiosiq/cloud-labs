"""Refusal helpers must read v1 ``statecontrol.tunables`` shape."""

from __future__ import annotations

import unittest

from lab_model.language.domain.component import PRESENCE_BREADBOARD, new_component_entry
from lab_model.coordinator.state.state_machine import refuse_if_not_on_breadboard


class StateMachineRefusalTests(unittest.TestCase):
    def test_refuse_if_not_on_breadboard_reads_statecontrol_tunables(self) -> None:
        entry = new_component_entry(
            "tag_2",
            "OPTICAL_BEAMSPLITTER",
            presence=PRESENCE_BREADBOARD,
            nominal_pose={"x": 100.0, "y": 200.0, "rotation": 45.0},
            meas_pose={"x": 100.0, "y": 200.0, "rotation": 45.0},
        )
        state = {"components": {"tag_2": entry}}
        result = refuse_if_not_on_breadboard(state, "tag_2", primitive_name="store")
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
