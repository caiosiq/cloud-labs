"""Tests for versioned alignment overlays (guides + laser lines)."""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest

from lab_model.state.control_manager import ControlManager
from lab_model.state.diff import configuration_diff
from lab_model.state.projections import (
    apply_configuration_to_components,
    extract_configuration,
    normalize_alignment_guides,
    normalize_laser_lines_doc,
)
from lab_model.state.reconcile import plan_reconcile


def _runtime_with_lines():
    return {
        "system_status": "IDLE",
        "components": {
            "tag_1": {
                "id": "tag_1",
                "type": "MIRROR",
                "statecontrol": {"tunables": {"nominal_pose": {"x": 1.0, "y": 2.0, "rotation": 0.0}}},
            }
        },
        "holding": {"tag_id": None, "nominal_pose": None},
        "alignment_guides": [
            {"id": "g1", "p1": {"x": 0.0, "y": 0.0}, "p2": {"x": 10.0, "y": 0.0}},
        ],
        "laser_lines": {
            "snap_line_id": "beam",
            "lines": [
                {"id": "beam", "name": "Beam", "color": "#f00", "enabled": True,
                 "p1": {"x": 0.0, "y": -5.0}, "p2": {"x": 0.0, "y": 5.0}},
            ],
        },
    }


class ProjectionTests(unittest.TestCase):
    def test_extract_includes_lines(self) -> None:
        cfg = extract_configuration(_runtime_with_lines())
        self.assertEqual(len(cfg["alignment_guides"]), 1)
        self.assertEqual(cfg["alignment_guides"][0]["id"], "g1")
        self.assertEqual(cfg["laser_lines"]["snap_line_id"], "beam")
        self.assertEqual(cfg["laser_lines"]["lines"][0]["id"], "beam")

    def test_extract_omits_lines_when_absent(self) -> None:
        cfg = extract_configuration({"components": {}, "holding": {}})
        self.assertNotIn("alignment_guides", cfg)
        self.assertNotIn("laser_lines", cfg)

    def test_apply_restores_lines(self) -> None:
        cfg = extract_configuration(_runtime_with_lines())
        runtime = {"components": {}, "holding": {}}
        apply_configuration_to_components(runtime, cfg)
        self.assertEqual(runtime["alignment_guides"][0]["id"], "g1")
        self.assertEqual(runtime["laser_lines"]["lines"][0]["id"], "beam")


class DiffTests(unittest.TestCase):
    def test_guide_move_is_dirty(self) -> None:
        a = extract_configuration(_runtime_with_lines())
        rt = _runtime_with_lines()
        rt["alignment_guides"][0]["p2"]["x"] = 99.0
        b = extract_configuration(rt)
        changes = configuration_diff(a, b)
        self.assertTrue(any(c["tag_id"] == "<guides>" for c in changes))

    def test_guide_delete_is_dirty(self) -> None:
        a = extract_configuration(_runtime_with_lines())
        rt = _runtime_with_lines()
        rt["alignment_guides"] = []
        b = extract_configuration(rt)
        changes = configuration_diff(a, b)
        self.assertTrue(any(c["tag_id"] == "<guides>" and c["to"] is None for c in changes))

    def test_laser_coeff_change_is_dirty(self) -> None:
        a = extract_configuration(_runtime_with_lines())
        rt = _runtime_with_lines()
        rt["laser_lines"]["lines"][0]["p2"]["x"] = 3.0
        b = extract_configuration(rt)
        changes = configuration_diff(a, b)
        self.assertTrue(any(c["tag_id"] == "<laser>" for c in changes))

    def test_legacy_commit_without_keys_is_wildcard(self) -> None:
        # Old commit (no line keys) vs seeded runtime -> no spurious line diff.
        legacy = {"components": {}, "holding": {"tag_id": None, "nominal_pose": None}}
        runtime_cfg = extract_configuration(_runtime_with_lines())
        runtime_cfg["components"] = {}
        changes = configuration_diff(legacy, runtime_cfg)
        self.assertFalse(any(c["tag_id"] in ("<guides>", "<laser>") for c in changes))

    def test_reconcile_skips_line_changes(self) -> None:
        a = extract_configuration(_runtime_with_lines())
        rt = _runtime_with_lines()
        rt["alignment_guides"][0]["p2"]["x"] = 99.0
        rt["laser_lines"]["lines"][0]["p2"]["x"] = 3.0
        b = extract_configuration(rt)
        plan = plan_reconcile(a, b)
        self.assertEqual(plan, [])


class BackfillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_backfill_injects_missing_keys(self) -> None:
        mgr = ControlManager(self.tmp, "optical-cavity")
        # Legacy commit (no line keys).
        mgr.commit_configuration(
            {"holding": {"tag_id": None, "nominal_pose": None}, "components": {}},
            message="legacy",
        )
        cid = mgr.get_head("main")
        cfg = mgr.get_configuration(cid)["configuration"]
        self.assertNotIn("alignment_guides", cfg)

        guides = normalize_alignment_guides(
            [{"id": "g1", "p1": {"x": 0.0, "y": 0.0}, "p2": {"x": 5.0, "y": 0.0}}]
        )
        laser = normalize_laser_lines_doc({"snap_line_id": "beam", "lines": []})
        updated = mgr.backfill_overlays(alignment_guides=guides, laser_lines=laser)
        self.assertEqual(updated, 1)

        cfg2 = mgr.get_configuration(cid)["configuration"]
        self.assertEqual(cfg2["alignment_guides"][0]["id"], "g1")
        self.assertEqual(cfg2["laser_lines"]["snap_line_id"], "beam")

        # Idempotent second run changes nothing.
        self.assertEqual(
            mgr.backfill_overlays(alignment_guides=guides, laser_lines=laser), 0
        )


if __name__ == "__main__":
    unittest.main()
