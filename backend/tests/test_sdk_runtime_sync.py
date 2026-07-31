"""SDK helpers for RECORD_TUNABLES / SYNC_RUNTIME / lab_initialization."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from cloudlabs.client import CloudLabsClient, _derive_lab_initialization
from cloudlabs.components import ComponentProxy
from cloudlabs.exceptions import CloudLabsCommandError


class DeriveLabInitTests(unittest.TestCase):
    def test_ready(self) -> None:
        init = _derive_lab_initialization({"runtime_sync": {"status": "ready"}})
        self.assertTrue(init["ready"])
        self.assertEqual(init["phase"], "ready")

    def test_missing_fail_closed(self) -> None:
        init = _derive_lab_initialization({})
        self.assertFalse(init["ready"])
        self.assertEqual(init["phase"], "missing_runtime_sync")

    def test_running(self) -> None:
        init = _derive_lab_initialization({"runtime_sync": {"status": "running"}})
        self.assertEqual(init["phase"], "measuring_inventory")


class LabInitializationClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = CloudLabsClient.__new__(CloudLabsClient)
        self.client.backend_id = "mock.default"
        self.client._lease = MagicMock(lease_id="lease-1")

    def test_prefers_server_field(self) -> None:
        self.client.get_lab_state = MagicMock(  # type: ignore[method-assign]
            return_value={
                "lab_initialization": {
                    "ready": True,
                    "phase": "ready",
                    "runtime_sync": "ready",
                    "errors": [],
                }
            }
        )
        init = self.client.lab_initialization()
        self.assertTrue(init["ready"])
        self.assertEqual(init["phase"], "ready")

    def test_wait_until_lab_ready(self) -> None:
        states = [
            {"runtime_sync": {"status": "running"}},
            {"runtime_sync": {"status": "ready"}},
        ]
        self.client.get_lab_state = MagicMock(side_effect=states)  # type: ignore[method-assign]
        with patch("cloudlabs.client.time.sleep", return_value=None):
            out = self.client.wait_until_lab_ready(timeout_s=5.0, poll_interval_s=0.01)
        self.assertEqual(out["runtime_sync"]["status"], "ready")

    def test_wait_until_lab_ready_failed(self) -> None:
        self.client.get_lab_state = MagicMock(  # type: ignore[method-assign]
            return_value={
                "runtime_sync": {"status": "failed", "errors": ["scan boom"]},
            }
        )
        with self.assertRaises(CloudLabsCommandError) as ctx:
            self.client.wait_until_lab_ready(timeout_s=1.0, poll_interval_s=0.01)
        self.assertIn("failed", str(ctx.exception).lower())


class RecordSyncCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = CloudLabsClient.__new__(CloudLabsClient)
        self.client.backend_id = "mock.default"
        self.client._lease = MagicMock(lease_id="lease-1")
        self.client._vlog = MagicMock()  # type: ignore[method-assign]
        self.client._require_lease = MagicMock(  # type: ignore[method-assign]
            return_value=self.client._lease
        )
        self.client._post_command = MagicMock(  # type: ignore[method-assign]
            return_value={"status": "accepted"}
        )
        self.client.wait_until_idle = MagicMock(  # type: ignore[method-assign]
            return_value={}
        )
        self.client.wait_until_lab_ready = MagicMock(  # type: ignore[method-assign]
            return_value={}
        )

    def test_record_tunables_payload(self) -> None:
        self.client.record_tunables(["tag_20"], ["nominal_pose"], wait=True)
        self.client._post_command.assert_called_once_with(
            {
                "action": "RECORD_TUNABLES",
                "parameters": {
                    "tag_ids": ["tag_20"],
                    "tunable_paths": ["nominal_pose"],
                    "force_rescan": True,
                },
            }
        )
        self.client.wait_until_idle.assert_called_once()

    def test_record_nominal_poses(self) -> None:
        self.client.record_nominal_poses(["tag_a", "tag_b"], wait=False)
        kwargs = self.client._post_command.call_args[0][0]
        self.assertEqual(kwargs["parameters"]["tunable_paths"], ["nominal_pose"])
        self.client.wait_until_idle.assert_not_called()

    def test_sync_runtime_payload(self) -> None:
        self.client.sync_runtime(wait=True)
        self.client._post_command.assert_called_once_with(
            {
                "action": "SYNC_RUNTIME",
                "parameters": {"force_rescan": True},
            }
        )
        self.client.wait_until_lab_ready.assert_called_once()

    def test_record_tunables_requires_tags(self) -> None:
        with self.assertRaises(ValueError):
            self.client.record_tunables([], ["nominal_pose"])


class ComponentRecordProxyTests(unittest.TestCase):
    def test_record_pose_delegates(self) -> None:
        client = MagicMock()
        proxy = ComponentProxy(client, "tag_22")
        out = proxy.record_pose(wait=False)
        client.record_nominal_poses.assert_called_once_with(
            ["tag_22"],
            force_rescan=True,
            wait=False,
        )
        self.assertIs(out, proxy)

    def test_record_tunables_string_path(self) -> None:
        client = MagicMock()
        proxy = ComponentProxy(client, "tag_22")
        proxy.record_tunables("exposure_time_ms", wait=True)
        client.record_tunables.assert_called_once_with(
            ["tag_22"],
            ["exposure_time_ms"],
            force_rescan=True,
            wait=True,
        )


if __name__ == "__main__":
    unittest.main()
