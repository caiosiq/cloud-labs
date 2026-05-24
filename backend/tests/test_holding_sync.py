"""Holding reconcile + robot session alignment."""
from __future__ import annotations

import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch

from lab_communicator.real import gripper


class TestHoldingSync(unittest.TestCase):
    def test_open_gripper_clears_confirmed_cloud_holding(self) -> None:
        exp = SimpleNamespace(is_physically_holding=True, _holding_tag_id=9)
        state = {
            "system_status": "HOLDING",
            "holding": {
                "tag_id": "tag_9",
                "nominal_pose": {"x": 0, "y": 0, "z": 40, "rotation": 0},
                "requires_operator_confirm": False,
            },
        }
        comm = SimpleNamespace(
            experiment=exp,
            current_state=state,
            _state_lock=nullcontext(),
            _persist_state=lambda: None,
        )
        with patch.object(
            gripper,
            "get_gripper_status",
            return_value={"closed": False, "source": "experiment.is_gripper_closed"},
        ):
            gripper.sync_experiment_holding_from_cloud_state(comm)
        self.assertFalse(exp.is_physically_holding)
        self.assertIsNone(exp._holding_tag_id)
        self.assertEqual(state["system_status"], "IDLE")

    def test_closed_gripper_confirmed_tag_sets_robot_session(self) -> None:
        exp = SimpleNamespace(is_physically_holding=False, _holding_tag_id=None)
        state = {
            "system_status": "HOLDING",
            "holding": {
                "tag_id": "tag_8",
                "nominal_pose": {"x": 0, "y": 0, "z": 40, "rotation": 0},
                "requires_operator_confirm": False,
            },
        }
        comm = SimpleNamespace(
            experiment=exp,
            current_state=state,
            _state_lock=nullcontext(),
            _persist_state=lambda: None,
        )
        with patch.object(
            gripper,
            "get_gripper_status",
            return_value={"closed": True, "source": "experiment.get_gripper_status"},
        ), patch.object(gripper, "_marker_id_from_cloud_tag", return_value=8):
            gripper.sync_experiment_holding_from_cloud_state(comm)
        self.assertTrue(exp.is_physically_holding)
        self.assertEqual(exp._holding_tag_id, 8)


if __name__ == "__main__":
    unittest.main()
