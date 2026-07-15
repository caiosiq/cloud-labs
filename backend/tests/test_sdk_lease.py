"""Tests for session lease manager and SDK helpers."""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from lab_model.jobs.lease_manager import (
    LeaseConflictError,
    LeaseExpiredError,
    LeaseNotFoundError,
    SessionLeaseManager,
    SessionLeaseRecord,
    active_backend_id,
)
from cloudlabs.client import (
    CloudLabsClient,
    _normalize_measurable_path,
    _read_nominal_pose,
    resolve_backend_id,
)
from cloudlabs.reconcile import build_reconcile_steps


class SessionLeaseManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mgr = SessionLeaseManager()

    def test_acquire_and_release(self) -> None:
        rec = self.mgr.acquire(
            backend_id="mock.default",
            holder="sdk:test",
            mode="imperative",
        )
        self.assertTrue(rec.lease_id.startswith("lease_"))
        active = self.mgr.active_lease("mock.default")
        assert active is not None
        self.assertEqual(active.lease_id, rec.lease_id)

        released = self.mgr.release(rec.lease_id)
        assert released is not None
        self.assertIsNone(self.mgr.active_lease("mock.default"))

    def test_conflict_second_acquire(self) -> None:
        self.mgr.acquire(backend_id="mock.default", holder="sdk:a", mode="imperative")
        with self.assertRaises(LeaseConflictError) as ctx:
            self.mgr.acquire(backend_id="mock.default", holder="sdk:b", mode="imperative")
        self.assertEqual(ctx.exception.holder, "sdk:a")

    def test_validate_command_blocks_unleased_ui(self) -> None:
        rec = self.mgr.acquire(backend_id="mock.default", holder="sdk:a", mode="imperative")
        with self.assertRaises(LeaseConflictError):
            self.mgr.validate_command_lease(
                backend_id="mock.default",
                lease_id=None,
                require_when_locked=True,
            )
        # Matching lease passes.
        validated = self.mgr.validate_command_lease(
            backend_id="mock.default",
            lease_id=rec.lease_id,
            require_when_locked=True,
        )
        self.assertEqual(validated.lease_id, rec.lease_id)

    def test_validate_command_allows_ui_when_unlocked(self) -> None:
        self.assertIsNone(
            self.mgr.validate_command_lease(
                backend_id="mock.default",
                lease_id=None,
                require_when_locked=True,
            )
        )

    def test_expired_lease_rejected(self) -> None:
        past = datetime.now(timezone.utc) - timedelta(seconds=30)
        record = SessionLeaseRecord(
            lease_id="lease_dead",
            backend_id="mock.default",
            holder="sdk:old",
            mode="imperative",
            issued_at=past,
            expires_at=past + timedelta(seconds=1),
        )
        self.mgr._by_backend["mock.default"] = record
        self.mgr._by_id[record.lease_id] = record

        with self.assertRaises((LeaseExpiredError, LeaseNotFoundError)):
            self.mgr.validate_command_lease(
                backend_id="mock.default",
                lease_id=record.lease_id,
                require_when_locked=True,
            )
        self.assertIsNone(self.mgr.active_lease("mock.default"))

    def test_release_unknown_is_none(self) -> None:
        self.assertIsNone(self.mgr.release("lease_missing"))


class ActiveBackendIdTests(unittest.TestCase):
    def test_mock_default(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(
                active_backend_id(communicator_id="mock", lab_mode="MOCK"),
                "mock.default",
            )

    def test_real_bench_env(self) -> None:
        with patch.dict("os.environ", {"CLOUDLABS_BENCH_ID": "mit_bench_1"}):
            self.assertEqual(
                active_backend_id(communicator_id="real", lab_mode="REAL"),
                "real.mit_bench_1",
            )


class SdkHelperTests(unittest.TestCase):
    def test_normalize_measurable_path(self) -> None:
        self.assertEqual(_normalize_measurable_path("camera_image"), "camera_image")
        self.assertEqual(
            _normalize_measurable_path("measurables.camera_image"),
            "camera_image",
        )

    def test_read_nominal_pose(self) -> None:
        state = {
            "components": {
                "tag_20": {
                    "statecontrol": {
                        "tunables": {
                            "nominal_pose": {"x": 1.0, "y": 2.0, "rotation": 3.0},
                        }
                    }
                }
            }
        }
        pose = _read_nominal_pose(state, "tag_20")
        self.assertEqual(pose, {"x": 1.0, "y": 2.0, "rotation": 3.0})


class CloudLabsClientContextTests(unittest.TestCase):
    def test_context_manager_acquires_and_releases(self) -> None:
        client = CloudLabsClient("mock.default", base_url="http://test")
        acquire_resp = MagicMock()
        acquire_resp.status_code = 200
        acquire_resp.content = b'{"lease_id":"lease_abc","backend_id":"mock.default","holder":"sdk:x","mode":"imperative","issued_at":"2026-01-01T00:00:00Z","expires_at":"2026-01-01T00:10:00Z"}'
        acquire_resp.json.return_value = {
            "lease_id": "lease_abc",
            "backend_id": "mock.default",
            "holder": "sdk:x",
            "mode": "imperative",
            "issued_at": "2026-01-01T00:00:00Z",
            "expires_at": "2026-01-01T00:10:00Z",
        }

        release_resp = MagicMock()
        release_resp.status_code = 200
        release_resp.content = b'{"status":"released"}'
        release_resp.json.return_value = {"status": "released"}

        with patch.object(client._session, "post", side_effect=[acquire_resp, release_resp]) as post:
            with client:
                self.assertEqual(client.lease_id, "lease_abc")
            self.assertIsNone(client.lease_id)
            self.assertEqual(post.call_count, 2)
            release_call = post.call_args_list[1]
            self.assertIn("/api/jobs/lease/release", release_call.args[0])


class ReconcileHelperTests(unittest.TestCase):
    def test_build_reconcile_steps(self) -> None:
        plan = [
            {
                "action": "MOVE_COMPONENT",
                "target_id": "tag_20",
                "parameters": {"target_x": 1.0, "target_y": 2.0, "rotation": 0.0},
            },
            {
                "action": "SET_MOTOR_SETPOINT",
                "target_id": "tag_19",
                "parameters": {"motor_id": 1, "angle_deg": 45.0},
            },
        ]
        steps = build_reconcile_steps(plan)
        self.assertEqual(len(steps), 2)
        self.assertEqual(steps[0].label, "MOVE_COMPONENT → tag_20")
        self.assertEqual(steps[1].action, "SET_MOTOR_SETPOINT")


class ResolveBackendIdTests(unittest.TestCase):
    def test_resolve_backend_id(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "backends": [
                {"backend_id": "mock.default", "availability": "ready"},
            ]
        }
        mock_resp.raise_for_status = MagicMock()
        with patch("cloudlabs.client.requests.get", return_value=mock_resp):
            self.assertEqual(resolve_backend_id("http://test"), "mock.default")


if __name__ == "__main__":
    unittest.main()
