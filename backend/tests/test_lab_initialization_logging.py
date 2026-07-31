"""lab_initialization status-change logging + hard refuse helpers."""
from __future__ import annotations

import logging
import unittest

from lab_model.coordinator import lab_initialization as li


class LabInitializationTests(unittest.TestCase):
    def setUp(self) -> None:
        li._last_runtime_sync.clear()
        li._last_lab_init_key.clear()

    def test_missing_runtime_sync_not_ready(self) -> None:
        init = li.lab_init_phase({"system_status": "IDLE"})
        self.assertFalse(init["ready"])
        self.assertEqual(init["phase"], "missing_runtime_sync")

    def test_ready_when_runtime_sync_ready(self) -> None:
        init = li.lab_init_phase({"runtime_sync": {"status": "ready"}})
        self.assertTrue(init["ready"])
        self.assertEqual(init["phase"], "ready")

    def test_in_process_ready_ignores_detached_edge_flag(self) -> None:
        init = li.lab_init_phase(
            {"runtime_sync": {"status": "ready"}},
            edge_attached=False,
        )
        self.assertTrue(init["ready"])
        self.assertEqual(init["phase"], "ready")

    def test_note_lab_state_logs_only_on_change(self) -> None:
        with self.assertLogs("lab_init", level=logging.INFO) as cm:
            li.note_lab_state(
                "mock.default",
                {"runtime_sync": {"status": "running"}},
                source="test",
            )
            li.note_lab_state(
                "mock.default",
                {"runtime_sync": {"status": "running"}},
                source="test",
            )
            li.note_lab_state(
                "mock.default",
                {"runtime_sync": {"status": "ready"}},
                source="test",
            )
        joined = "\n".join(cm.output)
        self.assertIn("(none) → running", joined)
        self.assertIn("running → ready", joined)
        # No third transition from the duplicate "running" poll
        self.assertEqual(joined.count("→"), 2)

    def test_ensure_action_allowed_refuses_when_not_ready(self) -> None:
        with self.assertLogs("lab_init", level=logging.WARNING) as cm:
            with self.assertRaises(li.LabNotInitializedError) as ctx:
                li.ensure_action_allowed(
                    "mock.default",
                    "MOVE_COMPONENT",
                    {"runtime_sync": {"status": "pending"}},
                )
        self.assertTrue(any("REFUSED" in line for line in cm.output))
        self.assertEqual(ctx.exception.init.get("phase"), "starting")

    def test_ensure_action_allowed_ok_when_ready(self) -> None:
        init = li.ensure_action_allowed(
            "mock.default",
            "MOVE_COMPONENT",
            {"runtime_sync": {"status": "ready"}},
        )
        self.assertTrue(init["ready"])

    def test_sync_runtime_not_gated(self) -> None:
        init = li.ensure_action_allowed(
            "mock.default",
            "SYNC_RUNTIME",
            {"runtime_sync": {"status": "failed"}},
        )
        self.assertFalse(init["ready"])


if __name__ == "__main__":
    unittest.main()
