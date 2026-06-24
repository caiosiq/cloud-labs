from __future__ import annotations

import unittest

from lab_model.state.saved_workspace import (
    WORKSPACE_STATE_KIND,
    build_workspace_state,
    unpack_workspace_state,
)


class SavedWorkspaceTests(unittest.TestCase):
    def test_round_trip_includes_empty_guides(self):
        lab_state = {"system_status": "IDLE", "components": {"tag_1": {}}}
        document = build_workspace_state(
            lab_state,
            {"alignment_guides": []},
        )
        restored_lab, restored_ui, has_ui = unpack_workspace_state(document)
        self.assertEqual(document["kind"], WORKSPACE_STATE_KIND)
        self.assertEqual(restored_lab, lab_state)
        self.assertEqual(restored_ui, {"alignment_guides": []})
        self.assertTrue(has_ui)

    def test_legacy_lab_state_remains_loadable(self):
        legacy = {"system_status": "IDLE", "components": {"tag_1": {}}}
        restored_lab, restored_ui, has_ui = unpack_workspace_state(legacy)
        self.assertEqual(restored_lab, legacy)
        self.assertEqual(restored_ui, {})
        self.assertFalse(has_ui)


if __name__ == "__main__":
    unittest.main()

