"""Tests for Step B / B.1 edge agent registry, job claim, and command queue."""
from __future__ import annotations

import threading
import unittest

from lab_model.edge.commands import EdgeCommandQueue
from lab_model.edge.registry import EdgeAgentRegistry
from lab_model.jobs.job_manager import JobManager


class EdgeAgentRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.reg = EdgeAgentRegistry(stale_after_s=60.0)

    def test_register_and_attach(self) -> None:
        rec = self.reg.register(backend_id="mock.default", label="agent-a")
        self.assertTrue(self.reg.is_attached("mock.default"))
        self.assertEqual(self.reg.get_for_backend("mock.default").agent_id, rec.agent_id)
        self.reg.heartbeat(rec.agent_id)
        self.assertTrue(self.reg.unregister(rec.agent_id))
        self.assertFalse(self.reg.is_attached("mock.default"))

    def test_replace_same_backend(self) -> None:
        a = self.reg.register(backend_id="mock.default", agent_id="edge_a")
        b = self.reg.register(backend_id="mock.default", agent_id="edge_b")
        self.assertEqual(self.reg.get_for_backend("mock.default").agent_id, "edge_b")
        self.assertIsNone(self.reg._by_agent.get(a.agent_id))
        self.assertEqual(b.agent_id, "edge_b")


class JobClaimForEdgeTests(unittest.TestCase):
    def test_claim_marks_running(self) -> None:
        mgr = JobManager()
        rec = mgr.submit(
            backend_id="mock.default",
            mode="closed_loop",
            holder="job:test",
            spec={"command": {"action": "OPTIMIZE", "target_id": "tag_20", "parameters": {}}},
        )
        claimed = mgr.claim_next_queued()
        assert claimed is not None
        self.assertEqual(claimed.job_id, rec.job_id)
        self.assertEqual(claimed.status, "running")
        self.assertIsNone(mgr.claim_next_queued())
        mgr.complete(rec.job_id, status="succeeded", result={})
        self.assertTrue(mgr.runner_should_start() or mgr.queued_count() == 0)


class EdgeCommandQueueTests(unittest.TestCase):
    def test_submit_poll_complete(self) -> None:
        q = EdgeCommandQueue()
        cmd = q.submit(
            backend_id="mock.default",
            kind="primitive",
            payload={"command": {"action": "RECORD_MEASURABLES", "target_id": "tag_20"}},
        )
        polled = q.poll("mock.default")
        assert polled is not None
        self.assertEqual(polled.command_id, cmd.command_id)
        self.assertIsNone(q.poll("mock.default"))
        q.complete(cmd.command_id, result={"status": "ok"})
        self.assertTrue(cmd.done.is_set())
        self.assertEqual(cmd.result, {"status": "ok"})

    def test_submit_and_wait_from_worker(self) -> None:
        q = EdgeCommandQueue()

        def _worker() -> None:
            import time

            for _ in range(50):
                polled = q.poll("mock.default")
                if polled is not None:
                    q.complete(
                        polled.command_id,
                        result={"kind": "scalar", "scalar": 1.0},
                    )
                    return
                time.sleep(0.01)

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        done = q.submit_and_wait(
            backend_id="mock.default",
            kind="kernel_eval",
            payload={"kernel_id": "demo.x", "tag_id": "tag_20"},
            timeout_s=5.0,
        )
        t.join(timeout=2.0)
        self.assertIsNone(done.error)
        self.assertEqual(done.result.get("scalar"), 1.0)


if __name__ == "__main__":
    unittest.main()
