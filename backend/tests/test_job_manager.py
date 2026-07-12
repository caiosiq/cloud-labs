"""Tests for Job Manager (Phase C)."""
from __future__ import annotations

import unittest

from lab_model.jobs.job_manager import JobManager, validate_submit_spec
from lab_model.jobs.models import JobRecord


class JobManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mgr = JobManager()

    def test_submit_and_list(self) -> None:
        record = self.mgr.submit(
            backend_id="mock.default",
            mode="closed_loop",
            holder="job:test",
            spec={"command": {"action": "OPTIMIZE", "target_id": "tag_20", "parameters": {"mode": "ensemble"}}},
        )
        self.assertTrue(record.job_id.startswith("job_"))
        self.assertEqual(record.status, "queued")
        listed = self.mgr.list_jobs()
        self.assertEqual(listed[0].job_id, record.job_id)

    def test_lifecycle(self) -> None:
        record = self.mgr.submit(
            backend_id="mock.default",
            mode="compiled_dag",
            holder="job:runner",
            spec={"steps": [{"action": "MOVE_COMPONENT", "target_id": "tag_20", "parameters": {}}]},
        )
        nxt = self.mgr.pop_next_queued()
        assert nxt is not None
        self.mgr.mark_running(nxt.job_id, lease_id="lease_abc")
        self.assertEqual(self.mgr.active_job_id(), nxt.job_id)
        self.mgr.update_progress(nxt.job_id, {"eval": 3, "best_loss": 0.12})
        done = self.mgr.complete(nxt.job_id, status="succeeded", result={"ok": True})
        self.assertEqual(done.status, "succeeded")
        self.assertIsNone(self.mgr.active_job_id())

    def test_cancel_queued(self) -> None:
        record = self.mgr.submit(
            backend_id="mock.default",
            mode="closed_loop",
            holder="job:q",
            spec={},
        )
        cancelled = self.mgr.request_cancel(record.job_id)
        self.assertEqual(cancelled.status, "cancelled")

    def test_validate_closed_loop_spec(self) -> None:
        spec = validate_submit_spec(
            "closed_loop",
            {
                "command": {
                    "action": "OPTIMIZE",
                    "target_id": "tag_20",
                    "parameters": {"mode": "ensemble", "variables": []},
                }
            },
        )
        self.assertIn("command", spec)

    def test_validate_compiled_dag_spec(self) -> None:
        spec = validate_submit_spec(
            "compiled_dag",
            {"steps": [{"action": "MOVE_COMPONENT", "target_id": "tag_20", "parameters": {}}]},
        )
        self.assertEqual(len(spec["steps"]), 1)

    def test_validate_compiled_dag_finalize_only(self) -> None:
        spec = validate_submit_spec(
            "compiled_dag",
            {
                "steps": [],
                "finalize_checkout": {
                    "repo_id": "laser-cavity",
                    "configuration_id": "abc123",
                    "branch": "main",
                },
            },
        )
        self.assertEqual(spec["steps"], [])
        self.assertEqual(spec["finalize_checkout"]["repo_id"], "laser-cavity")

    def test_validate_compiled_dag_snapshot_only(self) -> None:
        spec = validate_submit_spec(
            "compiled_dag",
            {
                "steps": [],
                "snapshot": {
                    "repo_id": "laser-cavity",
                    "branch": "main",
                    "commit": "abc123",
                },
            },
        )
        self.assertEqual(spec["steps"], [])
        self.assertEqual(spec["initialization_policy"], "force_reconcile")
        self.assertEqual(spec["snapshot"]["repo_id"], "laser-cavity")

    def test_validate_finalize_checkout_requires_fields(self) -> None:
        with self.assertRaises(ValueError):
            validate_submit_spec(
                "compiled_dag",
                {
                    "steps": [{"action": "MOVE_COMPONENT", "target_id": "tag_20", "parameters": {}}],
                    "finalize_checkout": {"repo_id": "laser-cavity"},
                },
            )


if __name__ == "__main__":
    unittest.main()
