"""Pose refresh resolves to RECORD_TUNABLES (nominal_pose)."""
from __future__ import annotations

import unittest

from lab_model.coordinator.state.pose_refresh_selection import resolve_pose_refresh_plan
from lab_model.language.domain.component import PRESENCE_BREADBOARD
from lab_model.language.primitives import parse_command_payload
from lab_model.language.primitives.schemas import RecordTunablesBody


class PoseRefreshRecordPathTests(unittest.TestCase):
    def test_plan_apply_list(self) -> None:
        comps = {
            "tag_a": {
                "statecontrol": {"tunables": {"presence": PRESENCE_BREADBOARD}}
            },
            "tag_b": {
                "statecontrol": {"tunables": {"presence": PRESENCE_BREADBOARD}}
            },
        }
        plan = resolve_pose_refresh_plan(
            comps,
            apply_tag_ids=["tag_a"],
        )
        self.assertEqual(plan.scan_tag_ids, ["tag_a"])

    def test_record_command_shape(self) -> None:
        cmd = parse_command_payload(
            {
                "action": "RECORD_TUNABLES",
                "parameters": {
                    "tag_ids": ["tag_a", "tag_b"],
                    "tunable_paths": ["nominal_pose"],
                    "force_rescan": True,
                },
            }
        )
        self.assertIsInstance(cmd, RecordTunablesBody)
        self.assertEqual(cmd.parameters.tag_ids, ["tag_a", "tag_b"])
        self.assertEqual(cmd.parameters.tunable_paths, ["nominal_pose"])


if __name__ == "__main__":
    unittest.main()
