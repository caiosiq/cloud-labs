"""Tests for scoped pose refresh selection and mock apply."""

from __future__ import annotations

import copy
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from lab_model.coordinator.backends.lab_view_config import bootstrap_lab_view
from mock_backend.shared.mock_scan_preview import build_mock_scan_proposed_poses
from lab_model.coordinator.state.pose_refresh_selection import resolve_pose_refresh_plan

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_MOCK_LAB_VIEW = _PROJECT_ROOT / "mock_backend" / "lab_view"


class PoseRefreshSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ["LAB_VIEW_PATH"] = str(_MOCK_LAB_VIEW)
        bootstrap_lab_view(str(_PROJECT_ROOT))

    def test_apply_tag_ids_scopes_scan(self) -> None:
        components = {
            "tag_a": {"statecontrol": {"tunables": {"presence": "breadboard"}}},
            "tag_b": {"statecontrol": {"tunables": {"presence": "breadboard"}}},
            "tag_c": {"statecontrol": {"tunables": {"presence": "off_table"}}},
        }
        plan = resolve_pose_refresh_plan(
            components,
            apply_tag_ids=["tag_a"],
        )
        self.assertEqual(plan.scan_tag_ids, ["tag_a"])
        self.assertIn("tag_b", plan.preserve_tag_ids)

    def test_tag_ids_scope_limits_participants(self) -> None:
        components = {
            "tag_a": {"statecontrol": {"tunables": {"presence": "breadboard"}}},
            "tag_b": {"statecontrol": {"tunables": {"presence": "breadboard"}}},
        }
        plan = resolve_pose_refresh_plan(components, tag_ids=["tag_b"])
        self.assertEqual(plan.scan_tag_ids, ["tag_b"])
        self.assertIn("tag_a", plan.preserve_tag_ids)

    def test_mock_scoped_refresh_updates_single_tag(self) -> None:
        from mock_backend.host.communicator import MockLabCommunicator
        from lab_model.coordinator.backends.lab_view_config import get_lab_view_paths
        from lab_model.language.domain import motor_rotation_store as motor_rot
        from lab_model.language.domain.component import get_measurables

        with tempfile.TemporaryDirectory() as tmp:
            lab_view = Path(tmp) / "lab_view"
            shutil.copytree(_MOCK_LAB_VIEW, lab_view)
            os.environ["LAB_VIEW_PATH"] = str(lab_view)
            bootstrap_lab_view(str(_PROJECT_ROOT))
            motor_rot.configure(get_lab_view_paths().motor_rotations_json)

            lab = MockLabCommunicator()
            state = lab.get_lab_state()
            comps = state["components"]
            on_table = [
                tid
                for tid, comp in comps.items()
                if comp.get("statecontrol", {}).get("tunables", {}).get("presence")
                in ("breadboard", "storage")
            ]
            self.assertGreaterEqual(len(on_table), 2)
            target = on_table[0]
            other = on_table[1]

            before_target = copy.deepcopy(get_measurables(comps[target]).get("pose"))
            before_other = copy.deepcopy(get_measurables(comps[other]).get("pose"))

            lab.refresh_pose_from_camera(apply_tag_ids=[target])

            after = lab.get_lab_state()["components"]
            after_target = get_measurables(after[target]).get("pose")
            after_other = get_measurables(after[other]).get("pose")

            proposed = build_mock_scan_proposed_poses(comps, tag_ids=[target])
            self.assertEqual(after_target, proposed[target])
            self.assertEqual(after_other, before_other)

    def test_build_mock_scan_respects_tag_ids_filter(self) -> None:
        components = {
            "tag_a": {
                "statecontrol": {
                    "tunables": {
                        "presence": "breadboard",
                        "nominal_pose": {"x": 1.0, "y": 2.0, "rotation": 0.0},
                    },
                    "measurables": {"pose": {"x": 1.0, "y": 2.0, "rotation": 0.0}},
                }
            },
            "tag_b": {
                "statecontrol": {
                    "tunables": {
                        "presence": "breadboard",
                        "nominal_pose": {"x": 3.0, "y": 4.0, "rotation": 0.0},
                    },
                    "measurables": {"pose": {"x": 3.0, "y": 4.0, "rotation": 0.0}},
                }
            },
        }
        proposed = build_mock_scan_proposed_poses(components, tag_ids=["tag_a"])
        self.assertEqual(set(proposed.keys()), {"tag_a"})


if __name__ == "__main__":
    unittest.main()
