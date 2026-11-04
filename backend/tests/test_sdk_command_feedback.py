import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from cloudlabs import CloudLabsClient, CloudLabsCommandError, CloudLabsTimeoutError


class SdkCommandFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.client = CloudLabsClient("sim.default", heartbeat_s=0, verbose=False)
        self.addCleanup(self.client._session.close)
        self.client._lease = SimpleNamespace(lease_id="test-lease")
        self.client.get_lab_state = Mock(return_value={"system_status": "IDLE"})

    def submit(self, ids, outcome):
        def post(path, body, **kwargs):
            if path == "/api/command":
                return {"status": "queued", "command_id": ids.pop(0)}
            self.assertEqual(path, "/api/command-queue/status")
            return {"enabled": True, "statuses": {cid: outcome[cid] for cid in body["command_ids"]}}
        self.client._post_json = Mock(side_effect=post)
        for _ in range(len(ids)):
            self.client._post_command({"action": "MOVE_COMPONENT", "target_id": "tag_22"})

    def test_settle_rejects_failed_command_despite_idle_lab(self):
        self.submit(["cmd_a"], {"cmd_a": {"status": "failed", "error": "frame boundary"}})
        with self.assertRaisesRegex(CloudLabsCommandError, "frame boundary"):
            self.client.wait_for_primitive_settled()

    def test_fluent_idle_wait_also_rejects_failed_command(self):
        self.submit(["cmd_a"], {"cmd_a": {"status": "failed", "error": "frame boundary"}})
        with self.assertRaisesRegex(CloudLabsCommandError, "frame boundary"):
            self.client.wait_until_idle()

    def test_holding_after_completed_pick_is_valid(self):
        self.submit(["cmd_a"], {"cmd_a": {"status": "done"}})
        self.client.get_lab_state.return_value = {"system_status": "HOLDING"}
        self.assertEqual(self.client.wait_for_primitive_settled()["system_status"], "HOLDING")
        self.assertEqual(self.client._pending_primitive_command_ids, [])

    def test_all_submitted_commands_are_checked(self):
        self.submit(["cmd_a", "cmd_b"], {
            "cmd_a": {"status": "failed", "error": "first command failed"},
            "cmd_b": {"status": "done"},
        })
        with self.assertRaisesRegex(CloudLabsCommandError, "first command failed"):
            self.client.wait_for_primitive_settled()
        self.assertEqual(self.client._post_json.call_args.args[1]["command_ids"], ["cmd_a", "cmd_b"])

    def test_unknown_or_invalid_status_never_counts_as_done(self):
        for status in ("unknown", "unexpected", None):
            with self.subTest(status=status):
                self.client._post_json = Mock(return_value={"enabled": True, "statuses": {"cmd_a": {"status": status}}})
                with self.assertRaisesRegex(CloudLabsCommandError, "Cannot confirm outcome"):
                    self.client.wait_for_commands(["cmd_a"])

    def test_cancelled_command_is_an_error(self):
        self.submit(["cmd_a"], {"cmd_a": {"status": "cancelled", "error": "lease released"}})
        with self.assertRaisesRegex(CloudLabsCommandError, "lease released"):
            self.client.wait_for_primitive_settled()

    def test_legacy_status_wait_is_preserved_without_tracked_commands(self):
        self.assertEqual(self.client.wait_until_idle()["system_status"], "IDLE")

    def test_queued_command_without_id_is_rejected(self):
        self.client._post_json = Mock(return_value={"status": "queued"})
        with self.assertRaisesRegex(CloudLabsCommandError, "no tracking ID"):
            self.client._post_command({"action": "MOVE_COMPONENT"})

    def test_timeout_keeps_tracking_for_later_confirmation(self):
        outcome = {"cmd_a": {"status": "running"}}
        self.submit(["cmd_a"], outcome)
        with self.assertRaises(CloudLabsTimeoutError):
            self.client.wait_for_primitive_settled(timeout_s=.003, poll_interval_s=.001)
        self.assertEqual(self.client._pending_primitive_command_ids, ["cmd_a"])
        outcome["cmd_a"] = {"status": "done"}
        self.client.wait_for_primitive_settled()
        self.assertEqual(self.client._pending_primitive_command_ids, [])
