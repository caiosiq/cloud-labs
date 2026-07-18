"""Ops hardening: edge lab_state cache, 5s stale eviction, fail-closed jobs/leases."""
from __future__ import annotations

import time
import unittest

from lab_model.execution.edge.registry import DEFAULT_STALE_AFTER_S, EdgeAgentRegistry
from lab_model.coordinator.jobs.job_manager import JobManager
from lab_model.coordinator.jobs.lease_manager import SessionLeaseManager


class EdgeStaleEvictionTests(unittest.TestCase):
    def test_default_stale_window_is_five_seconds(self) -> None:
        self.assertEqual(DEFAULT_STALE_AFTER_S, 5.0)

    def test_stale_eviction_fires_callback(self) -> None:
        seen = []

        def _on_evict(rec) -> None:
            seen.append(rec.backend_id)

        reg = EdgeAgentRegistry(stale_after_s=0.05, on_evict=_on_evict)
        rec = reg.register(backend_id="mock.default", label="t")
        self.assertTrue(reg.is_attached("mock.default"))
        time.sleep(0.08)
        self.assertIsNone(reg.get_for_backend("mock.default"))
        self.assertEqual(seen, ["mock.default"])
        disc = reg.last_disconnect("mock.default")
        assert disc is not None
        self.assertIn("stale", disc["reason"])
        self.assertEqual(disc["agent_id"], rec.agent_id)

    def test_lab_state_cache_on_heartbeat(self) -> None:
        reg = EdgeAgentRegistry(stale_after_s=60.0)
        rec = reg.register(backend_id="mock.default")
        reg.heartbeat(rec.agent_id, lab_state={"system_status": "IDLE", "components": {}})
        cached = reg.get_cached_lab_state("mock.default")
        assert cached is not None
        self.assertEqual(cached["system_status"], "IDLE")

    def test_sweep_stale(self) -> None:
        reg = EdgeAgentRegistry(stale_after_s=0.05)
        reg.register(backend_id="mock.default")
        time.sleep(0.08)
        evicted = reg.sweep_stale()
        self.assertEqual(len(evicted), 1)
        self.assertFalse(reg.is_attached("mock.default"))


class FailClosedJobsAndLeasesTests(unittest.TestCase):
    def test_fail_open_work(self) -> None:
        mgr = JobManager()
        a = mgr.submit(
            backend_id="mock.default",
            mode="closed_loop",
            holder="t",
            spec={"command": {"action": "OPTIMIZE", "target_id": "tag_20", "parameters": {"mode": "ensemble"}}},
        )
        mgr.claim_next_queued()
        b = mgr.submit(
            backend_id="mock.default",
            mode="closed_loop",
            holder="t2",
            spec={"command": {"action": "OPTIMIZE", "target_id": "tag_20", "parameters": {"mode": "ensemble"}}},
        )
        failed = mgr.fail_open_work(error="edge offline")
        self.assertEqual(len(failed), 2)
        self.assertEqual(mgr.get(a.job_id).status, "failed")
        self.assertEqual(mgr.get(b.job_id).status, "failed")
        self.assertIsNone(mgr.active_job_id())
        self.assertEqual(mgr.queued_count(), 0)

    def test_release_backend_lease(self) -> None:
        leases = SessionLeaseManager()
        rec = leases.acquire(backend_id="mock.default", holder="sdk:test")
        self.assertIsNotNone(leases.active_lease("mock.default"))
        dropped = leases.release_backend("mock.default")
        assert dropped is not None
        self.assertEqual(dropped.lease_id, rec.lease_id)
        self.assertIsNone(leases.active_lease("mock.default"))


if __name__ == "__main__":
    unittest.main()
