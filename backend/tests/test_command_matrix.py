"""Unit tests for Command Matrix (Phase 0–3 + tag-scoped motors)."""
from __future__ import annotations

import os
import unittest
from unittest import mock

from lab_model.coordinator.jobs.command_matrix import (
    CommandMatrix,
    CommandMatrixRefuse,
    command_matrix_enabled,
    make_motor_thread_id,
    parse_execution_threads,
    parse_motor_thread_id,
    route_primitive,
)


class ParseExecutionThreadsTests(unittest.TestCase):
    def test_fallback_arm_and_sense_only(self) -> None:
        specs = parse_execution_threads(None)
        ids = [s.id for s in specs]
        self.assertEqual(ids, ["arm.0", "sense.0"])
        self.assertEqual(specs[0].kind, "arm")
        self.assertEqual(specs[1].kind, "sense")

    def test_fallback_ignores_motor_ids(self) -> None:
        specs = parse_execution_threads({}, motor_ids=[7, 9])
        self.assertEqual([s.id for s in specs], ["arm.0", "sense.0"])

    def test_explicit_execution_threads(self) -> None:
        caps = {
            "execution_threads": [
                {"id": "arm.0", "kind": "arm"},
                {"id": "sense.0", "kind": "sense"},
            ]
        }
        specs = parse_execution_threads(caps, motor_ids=[99])
        self.assertEqual([s.id for s in specs], ["arm.0", "sense.0"])

    def test_parse_tag_scoped_motor_from_id(self) -> None:
        caps = {
            "execution_threads": [
                {"id": "arm.0", "kind": "arm"},
                {
                    "id": "motor.tag_20.1",
                    "kind": "motor",
                    "motor_id": 1,
                    "tag_id": "tag_20",
                },
            ]
        }
        specs = parse_execution_threads(caps)
        self.assertEqual(specs[1].id, "motor.tag_20.1")
        self.assertEqual(specs[1].tag_id, "tag_20")
        self.assertEqual(specs[1].motor_id, 1)


class MotorThreadIdTests(unittest.TestCase):
    def test_make_and_parse(self) -> None:
        tid = make_motor_thread_id("tag_20", 1)
        self.assertEqual(tid, "motor.tag_20.1")
        self.assertEqual(parse_motor_thread_id(tid), ("tag_20", 1))

    def test_parse_rejects_legacy_global(self) -> None:
        self.assertIsNone(parse_motor_thread_id("motor.1"))


class RoutePrimitiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.matrix = CommandMatrix.from_capabilities(
            "mock.default",
            {
                "execution_threads": [
                    {"id": "arm.0", "kind": "arm"},
                    {"id": "sense.0", "kind": "sense"},
                ]
            },
        )

    def test_route_arm(self) -> None:
        resources, kind = route_primitive("STORE_COMPONENT", {}, self.matrix.threads)
        self.assertEqual(resources, ["arm.0"])
        self.assertEqual(kind, "normal")

    def test_route_motor_tag_scoped(self) -> None:
        resources, kind = route_primitive(
            "SET_MOTOR_SETPOINT",
            {"motor_id": 2},
            self.matrix.threads,
            target_id="tag_20",
        )
        self.assertEqual(resources, ["motor.tag_20.2"])
        self.assertEqual(kind, "normal")

    def test_route_motor_missing_tag_falls_back_to_arm(self) -> None:
        resources, kind = route_primitive(
            "MOVE_MOTOR", {"motor_id": 1}, self.matrix.threads
        )
        self.assertEqual(resources, ["arm.0"])
        self.assertEqual(kind, "normal")

    def test_route_motor_legacy_global_column(self) -> None:
        m = CommandMatrix.from_capabilities(
            "mock.legacy",
            {
                "execution_threads": [
                    {"id": "arm.0", "kind": "arm"},
                    {"id": "motor.2", "kind": "motor", "motor_id": 2},
                ]
            },
        )
        resources, kind = route_primitive(
            "SET_MOTOR_SETPOINT", {"motor_id": 2}, m.threads
        )
        self.assertEqual(resources, ["motor.2"])
        self.assertEqual(kind, "normal")

    def test_route_optimize_barrier(self) -> None:
        resources, kind = route_primitive("OPTIMIZE", {}, self.matrix.threads)
        self.assertEqual(kind, "barrier")
        self.assertEqual(set(resources), set(self.matrix.threads.keys()))

    def test_route_teleop_session_lock(self) -> None:
        resources, kind = route_primitive("START_TELEOP", {}, self.matrix.threads)
        self.assertEqual(resources, ["arm.0"])
        self.assertEqual(kind, "session_lock")

    def test_route_live_feed_sense(self) -> None:
        resources, kind = route_primitive("START_LIVE_FEED", {}, self.matrix.threads)
        self.assertEqual(resources, ["sense.0"])
        self.assertEqual(kind, "normal")


class EnqueueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.matrix = CommandMatrix.from_capabilities(
            "mock.default",
            {
                "execution_threads": [
                    {"id": "arm.0", "kind": "arm"},
                    {"id": "sense.0", "kind": "sense"},
                ]
            },
        )

    def test_enqueue_two_store_on_arm(self) -> None:
        a = self.matrix.enqueue(
            {"action": "STORE_COMPONENT", "target_id": "tag_a"},
            lease_id="lease_1",
        )
        b = self.matrix.enqueue(
            {"action": "STORE_COMPONENT", "target_id": "tag_b"},
            lease_id="lease_1",
        )
        self.assertEqual(a["status"], "queued")
        self.assertEqual(b["status"], "queued")
        arm_q = self.matrix.threads["arm.0"].queue
        self.assertEqual(arm_q, [a["command_id"], b["command_id"]])

    def test_holding_refuses_store_allows_place_from_hover(self) -> None:
        lab = {"system_status": "HOLDING", "holding": {"tag_id": "tag_held"}}
        with self.assertRaises(CommandMatrixRefuse) as ctx:
            self.matrix.enqueue(
                {"action": "STORE_COMPONENT", "target_id": "tag_other"},
                lease_id="lease_1",
                lab_state=lab,
            )
        self.assertIn("HOLDING", ctx.exception.reason)

        ok = self.matrix.enqueue(
            {"action": "PLACE_FROM_HOVER", "target_id": "tag_held"},
            lease_id="lease_1",
            lab_state=lab,
        )
        self.assertEqual(ok["status"], "queued")
        self.assertEqual(ok["resources"], ["arm.0"])

    def test_motor_enqueue_creates_tag_scoped_thread(self) -> None:
        store = self.matrix.enqueue(
            {"action": "STORE_COMPONENT", "target_id": "tag_a"},
            lease_id="lease_1",
        )
        motor = self.matrix.enqueue(
            {
                "action": "SET_MOTOR_SETPOINT",
                "target_id": "tag_m",
                "parameters": {"motor_id": 1},
            },
            lease_id="lease_1",
        )
        self.assertEqual(motor["resources"], ["motor.tag_m.1"])
        self.assertEqual(self.matrix.threads["arm.0"].queue, [store["command_id"]])
        self.assertEqual(
            self.matrix.threads["motor.tag_m.1"].queue, [motor["command_id"]]
        )
        ts = self.matrix.threads["motor.tag_m.1"]
        self.assertEqual(ts.tag_id, "tag_m")
        self.assertEqual(ts.motor_id, 1)
        runnable = self.matrix.pop_runnable("motor.tag_m.1")
        self.assertIsNotNone(runnable)
        assert runnable is not None
        self.assertEqual(runnable.command_id, motor["command_id"])

    def test_same_motor_id_different_tags_are_independent(self) -> None:
        a = self.matrix.enqueue(
            {
                "action": "SET_MOTOR_SETPOINT",
                "target_id": "tag_20",
                "parameters": {"motor_id": 1},
            },
            lease_id="lease_1",
        )
        b = self.matrix.enqueue(
            {
                "action": "SET_MOTOR_SETPOINT",
                "target_id": "tag_11",
                "parameters": {"motor_id": 1},
            },
            lease_id="lease_1",
        )
        self.assertEqual(a["resources"], ["motor.tag_20.1"])
        self.assertEqual(b["resources"], ["motor.tag_11.1"])
        self.assertIn("motor.tag_20.1", self.matrix.threads)
        self.assertIn("motor.tag_11.1", self.matrix.threads)
        self.assertIsNotNone(self.matrix.pop_runnable("motor.tag_20.1"))
        self.assertIsNotNone(self.matrix.pop_runnable("motor.tag_11.1"))

    def test_optimize_barrier_resources_all_threads(self) -> None:
        ack = self.matrix.enqueue(
            {"action": "OPTIMIZE", "target_id": "tag_20", "parameters": {"mode": "ensemble"}},
            lease_id="lease_1",
        )
        self.assertEqual(ack["kind"], "barrier")
        self.assertEqual(set(ack["resources"]), set(self.matrix.threads.keys()))
        for tid in self.matrix.threads:
            self.assertIn(ack["command_id"], self.matrix.threads[tid].queue)

    def test_new_motor_thread_fenced_by_queued_barrier(self) -> None:
        opt = self.matrix.enqueue(
            {"action": "OPTIMIZE", "target_id": "tag_20"},
            lease_id="lease_1",
        )
        motor = self.matrix.enqueue(
            {
                "action": "MOVE_MOTOR",
                "target_id": "tag_20",
                "parameters": {"motor_id": 1, "distance": 1.0},
            },
            lease_id="lease_1",
        )
        mtid = "motor.tag_20.1"
        self.assertIn(mtid, self.matrix.threads)
        # Barrier was extended onto the new column; motor sits behind it.
        self.assertEqual(
            self.matrix.threads[mtid].queue,
            [opt["command_id"], motor["command_id"]],
        )
        self.assertIn(mtid, self.matrix.items[opt["command_id"]].resources)
        # Head is the barrier (motor work cannot claim ahead of it).
        head = self.matrix.pop_runnable(mtid)
        self.assertIsNotNone(head)
        assert head is not None
        self.assertEqual(head.command_id, opt["command_id"])
        self.assertEqual(head.kind, "barrier")
        # Only primary resource thread may claim the barrier.
        self.assertIsNone(self.matrix.claim_runnable(mtid))
        claimed = self.matrix.claim_runnable(sorted(head.resources)[0])
        self.assertIsNotNone(claimed)
        assert claimed is not None
        self.assertEqual(claimed.command_id, opt["command_id"])

    def test_cancel_queued(self) -> None:
        ack = self.matrix.enqueue(
            {"action": "STORE_COMPONENT", "target_id": "tag_a"},
            lease_id="lease_1",
        )
        self.assertTrue(self.matrix.cancel_queued(ack["command_id"]))
        self.assertEqual(self.matrix.items[ack["command_id"]].status, "cancelled")
        self.assertNotIn(ack["command_id"], self.matrix.threads["arm.0"].queue)
        self.assertFalse(self.matrix.cancel_queued(ack["command_id"]))

    def test_optimizing_refuses_enqueue(self) -> None:
        lab = {"system_status": "OPTIMIZING"}
        with self.assertRaises(CommandMatrixRefuse) as ctx:
            self.matrix.enqueue(
                {"action": "STORE_COMPONENT", "target_id": "tag_a"},
                lease_id="lease_1",
                lab_state=lab,
            )
        self.assertIn("OPTIMIZING", ctx.exception.reason)

    def test_barrier_waits_until_all_heads(self) -> None:
        store = self.matrix.enqueue(
            {"action": "STORE_COMPONENT", "target_id": "tag_a"},
            lease_id="lease_1",
        )
        # Create a motor column ahead of OPTIMIZE so barrier spans it too.
        motor = self.matrix.enqueue(
            {
                "action": "SET_MOTOR_SETPOINT",
                "target_id": "tag_20",
                "parameters": {"motor_id": 1},
            },
            lease_id="lease_1",
        )
        opt = self.matrix.enqueue(
            {"action": "OPTIMIZE", "target_id": "tag_20"},
            lease_id="lease_1",
        )
        mtid = "motor.tag_20.1"
        # Motor head is the setpoint (runnable); barrier waits behind it
        m_peek = self.matrix.pop_runnable(mtid)
        self.assertIsNotNone(m_peek)
        assert m_peek is not None
        self.assertEqual(m_peek.command_id, motor["command_id"])
        # Arm head is the store
        head = self.matrix.pop_runnable("arm.0")
        self.assertIsNotNone(head)
        assert head is not None
        self.assertEqual(head.command_id, store["command_id"])
        self.matrix.mark_running(store["command_id"])
        self.matrix.mark_done(store["command_id"])
        # Clear motor ahead of barrier
        m_head = self.matrix.claim_runnable(mtid)
        self.assertIsNotNone(m_head)
        assert m_head is not None
        self.assertEqual(m_head.command_id, motor["command_id"])
        self.matrix.mark_done(motor["command_id"])
        # Now barrier is head on all threads
        barrier = self.matrix.pop_runnable("arm.0")
        self.assertIsNotNone(barrier)
        assert barrier is not None
        self.assertEqual(barrier.command_id, opt["command_id"])


class CommandMatrixEnabledTests(unittest.TestCase):
    def test_env_on(self) -> None:
        with mock.patch.dict(os.environ, {"CLOUDLABS_COMMAND_MATRIX": "1"}, clear=False):
            self.assertTrue(command_matrix_enabled("real.deathray"))
        with mock.patch.dict(os.environ, {"CLOUDLABS_COMMAND_MATRIX": "true"}, clear=False):
            self.assertTrue(command_matrix_enabled("real.x"))

    def test_env_off(self) -> None:
        with mock.patch.dict(os.environ, {"CLOUDLABS_COMMAND_MATRIX": "0"}, clear=False):
            self.assertFalse(command_matrix_enabled("mock.default"))
        with mock.patch.dict(os.environ, {"CLOUDLABS_COMMAND_MATRIX": "off"}, clear=False):
            self.assertFalse(command_matrix_enabled("sim.default"))

    def test_default_mock_sim_real(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "CLOUDLABS_COMMAND_MATRIX"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertTrue(command_matrix_enabled("mock.default"))
            self.assertTrue(command_matrix_enabled("sim.default"))
            self.assertFalse(command_matrix_enabled("real.deathray"))
            self.assertTrue(command_matrix_enabled(None))


class MatrixRuntimeHelperTests(unittest.TestCase):
    def test_get_or_create(self) -> None:
        from lab_model.coordinator.jobs.matrix_runtime import get_or_create_matrix

        class FakeRuntime:
            backend_id = "mock.default"
            command_matrix = None

        rt = FakeRuntime()
        m1 = get_or_create_matrix(rt)
        m2 = get_or_create_matrix(rt)
        self.assertIs(m1, m2)
        self.assertIn("arm.0", m1.threads)
        self.assertNotIn("motor.1", m1.threads)


class Phase2SessionAndSenseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.matrix = CommandMatrix.from_capabilities(
            "mock.default",
            {
                "execution_threads": [
                    {"id": "arm.0", "kind": "arm"},
                    {"id": "sense.0", "kind": "sense"},
                ]
            },
        )

    def test_live_feed_on_sense_parallel_to_arm(self) -> None:
        store = self.matrix.enqueue(
            {"action": "STORE_COMPONENT", "target_id": "tag_a"},
            lease_id="L",
        )
        feed = self.matrix.enqueue(
            {"action": "START_LIVE_FEED", "target_id": "tag_cam"},
            lease_id="L",
        )
        self.assertEqual(store["resources"], ["arm.0"])
        self.assertEqual(feed["resources"], ["sense.0"])
        self.assertEqual(self.matrix.threads["sense.0"].queue, [feed["command_id"]])
        sense_head = self.matrix.claim_runnable("sense.0")
        self.assertIsNotNone(sense_head)
        assert sense_head is not None
        self.assertEqual(sense_head.action, "START_LIVE_FEED")

    def test_teleop_session_lock_blocks_arm_until_end(self) -> None:
        start = self.matrix.enqueue(
            {"action": "START_TELEOP", "target_id": "tag_t"},
            lease_id="L",
        )
        claimed = self.matrix.claim_runnable("arm.0")
        self.assertIsNotNone(claimed)
        assert claimed is not None
        self.assertEqual(claimed.command_id, start["command_id"])
        self.assertIn("arm.0", self.matrix.session_locks)
        self.matrix.mark_done(start["command_id"])

        with self.assertRaises(CommandMatrixRefuse):
            self.matrix.enqueue(
                {"action": "STORE_COMPONENT", "target_id": "tag_a"},
                lease_id="L",
            )

        end = self.matrix.enqueue(
            {"action": "END_TELEOP", "target_id": "tag_t"},
            lease_id="L",
        )
        end_item = self.matrix.claim_runnable("arm.0")
        self.assertIsNotNone(end_item)
        assert end_item is not None
        self.assertEqual(end_item.command_id, end["command_id"])
        self.matrix.mark_done(end["command_id"])
        self.assertNotIn("arm.0", self.matrix.session_locks)
        self.assertTrue(self.matrix.is_idle())

    def test_optimize_refused_while_teleop_lock(self) -> None:
        start = self.matrix.enqueue(
            {"action": "START_TELEOP", "target_id": "tag_t"},
            lease_id="L",
        )
        self.matrix.claim_runnable("arm.0")
        self.matrix.mark_done(start["command_id"])
        with self.assertRaises(CommandMatrixRefuse) as ctx:
            self.matrix.enqueue(
                {"action": "OPTIMIZE", "target_id": "tag_20"},
                lease_id="L",
            )
        self.assertIn("session locks", ctx.exception.reason)

    def test_holding_allows_sense_live_feed(self) -> None:
        lab = {"system_status": "HOLDING", "holding": {"tag_id": "tag_held"}}
        ack = self.matrix.enqueue(
            {"action": "START_LIVE_FEED", "target_id": "tag_cam"},
            lease_id="L",
            lab_state=lab,
        )
        self.assertEqual(ack["resources"], ["sense.0"])

    def test_holding_allows_motor_on_other_tag(self) -> None:
        lab = {"system_status": "HOLDING", "holding": {"tag_id": "tag_held"}}
        ack = self.matrix.enqueue(
            {
                "action": "SET_MOTOR_SETPOINT",
                "target_id": "tag_20",
                "parameters": {"motor_id": 1},
            },
            lease_id="L",
            lab_state=lab,
        )
        self.assertEqual(ack["resources"], ["motor.tag_20.1"])


class Phase3CancelAndLeaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.matrix = CommandMatrix.from_capabilities(
            "mock.default",
            {
                "execution_threads": [
                    {"id": "arm.0", "kind": "arm"},
                    {"id": "sense.0", "kind": "sense"},
                ]
            },
        )

    def test_cancel_all_queued(self) -> None:
        a = self.matrix.enqueue(
            {"action": "STORE_COMPONENT", "target_id": "a"}, lease_id="L"
        )
        b = self.matrix.enqueue(
            {"action": "STORE_COMPONENT", "target_id": "b"}, lease_id="L"
        )
        cancelled = self.matrix.cancel_all_queued()
        self.assertEqual(set(cancelled), {a["command_id"], b["command_id"]})
        self.assertEqual(self.matrix.threads["arm.0"].queue, [])
        self.assertTrue(self.matrix.is_idle())

    def test_statuses_lookup(self) -> None:
        a = self.matrix.enqueue(
            {"action": "STORE_COMPONENT", "target_id": "a"}, lease_id="L"
        )
        st = self.matrix.statuses([a["command_id"], "missing"])
        self.assertEqual(st[a["command_id"]]["status"], "queued")
        self.assertEqual(st["missing"]["status"], "unknown")

    def test_lease_release_cancels_queued_and_orphans_running(self) -> None:
        a = self.matrix.enqueue(
            {"action": "STORE_COMPONENT", "target_id": "a"}, lease_id="L"
        )
        b = self.matrix.enqueue(
            {"action": "STORE_COMPONENT", "target_id": "b"}, lease_id="L"
        )
        claimed = self.matrix.claim_runnable("arm.0")
        self.assertIsNotNone(claimed)
        assert claimed is not None
        self.assertEqual(claimed.command_id, a["command_id"])
        summary = self.matrix.clear_on_lease_release()
        self.assertIn(b["command_id"], summary["cancelled_queued"])
        self.assertIn(a["command_id"], summary["orphaned_running"])
        self.assertEqual(self.matrix.items[a["command_id"]].status, "failed")
        self.assertEqual(self.matrix.items[a["command_id"]].error, "lease_released")
        self.assertEqual(self.matrix.items[b["command_id"]].status, "cancelled")
        self.assertTrue(self.matrix.is_idle())

    def test_lease_release_clears_teleop_session_lock(self) -> None:
        start = self.matrix.enqueue(
            {"action": "START_TELEOP", "target_id": "tag_t"}, lease_id="L"
        )
        self.matrix.claim_runnable("arm.0")
        self.matrix.mark_done(start["command_id"])
        self.assertIn("arm.0", self.matrix.session_locks)
        summary = self.matrix.clear_on_lease_release()
        self.assertIn("arm.0", summary["session_locks_cleared"])
        self.assertNotIn("arm.0", self.matrix.session_locks)


class PredecessorGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.matrix = CommandMatrix.from_capabilities(
            "mock.default",
            {
                "execution_threads": [
                    {"id": "arm.0", "kind": "arm"},
                    {"id": "sense.0", "kind": "sense"},
                ]
            },
        )

    def test_unknown_predecessor_refused(self) -> None:
        with self.assertRaises(CommandMatrixRefuse) as ctx:
            self.matrix.enqueue(
                {
                    "action": "STORE_COMPONENT",
                    "target_id": "b",
                    "predecessors": ["missing_cmd"],
                },
                lease_id="L",
            )
        self.assertIn("unknown predecessor", ctx.exception.reason)

    def test_cross_thread_head_blocked_until_pred_done(self) -> None:
        # Motor on its own column, but must wait for arm STORE to finish.
        store = self.matrix.enqueue(
            {"action": "STORE_COMPONENT", "target_id": "tag_a"},
            lease_id="L",
        )
        motor = self.matrix.enqueue(
            {
                "action": "SET_MOTOR_SETPOINT",
                "target_id": "tag_20",
                "parameters": {"motor_id": 1},
                "predecessors": [store["command_id"]],
            },
            lease_id="L",
        )
        self.assertEqual(motor["predecessors"], [store["command_id"]])
        mtid = "motor.tag_20.1"
        # Motor is head of its column but blocked on the arm store.
        self.assertIsNone(self.matrix.pop_runnable(mtid))
        snap = self.matrix.snapshot()
        motor_col = next(t for t in snap["threads"] if t["id"] == mtid)
        self.assertEqual(motor_col["queue"][0]["blocked_on"], [store["command_id"]])

        # Unrelated sense work still runs.
        feed = self.matrix.enqueue(
            {"action": "START_LIVE_FEED", "target_id": "tag_cam"},
            lease_id="L",
        )
        sense_head = self.matrix.claim_runnable("sense.0")
        self.assertIsNotNone(sense_head)
        assert sense_head is not None
        self.assertEqual(sense_head.command_id, feed["command_id"])
        self.matrix.mark_done(feed["command_id"])

        # Finish store → motor becomes runnable.
        arm_head = self.matrix.claim_runnable("arm.0")
        self.assertIsNotNone(arm_head)
        assert arm_head is not None
        self.assertEqual(arm_head.command_id, store["command_id"])
        self.matrix.mark_done(store["command_id"])
        motor_head = self.matrix.claim_runnable(mtid)
        self.assertIsNotNone(motor_head)
        assert motor_head is not None
        self.assertEqual(motor_head.command_id, motor["command_id"])

    def test_failed_predecessor_fails_dependent(self) -> None:
        a = self.matrix.enqueue(
            {"action": "STORE_COMPONENT", "target_id": "a"}, lease_id="L"
        )
        b = self.matrix.enqueue(
            {
                "action": "STORE_COMPONENT",
                "target_id": "b",
                "predecessors": [a["command_id"]],
            },
            lease_id="L",
        )
        claimed = self.matrix.claim_runnable("arm.0")
        self.assertIsNotNone(claimed)
        self.matrix.mark_done(a["command_id"], error="boom")
        # B is next on arm but predecessor failed → B fails on peek.
        self.assertIsNone(self.matrix.pop_runnable("arm.0"))
        self.assertEqual(self.matrix.items[b["command_id"]].status, "failed")
        self.assertIn("predecessor", self.matrix.items[b["command_id"]].error or "")


class EnqueueBatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.matrix = CommandMatrix.from_capabilities(
            "mock.default",
            {
                "execution_threads": [
                    {"id": "arm.0", "kind": "arm"},
                    {"id": "sense.0", "kind": "sense"},
                ]
            },
        )

    def test_batch_remaps_plan_step_predecessors(self) -> None:
        result = self.matrix.enqueue_batch(
            [
                {
                    "action": "STORE_COMPONENT",
                    "target_id": "a",
                    "plan_step_id": "s0",
                    "predecessors": [],
                },
                {
                    "action": "SET_MOTOR_SETPOINT",
                    "target_id": "tag_20",
                    "parameters": {"motor_id": 1, "angle_deg": 10.0},
                    "plan_step_id": "s1",
                    "predecessors": ["s0"],
                },
            ],
            lease_id="L",
        )
        self.assertEqual(result["steps"], 2)
        ids = result["command_ids"]
        self.assertEqual(len(ids), 2)
        motor = self.matrix.items[ids[1]]
        self.assertEqual(motor.predecessors, [ids[0]])
        # Motor blocked until store done.
        self.assertIsNone(self.matrix.pop_runnable("motor.tag_20.1"))
        arm = self.matrix.claim_runnable("arm.0")
        self.assertIsNotNone(arm)
        assert arm is not None
        self.matrix.mark_done(arm.command_id)
        self.assertIsNotNone(self.matrix.claim_runnable("motor.tag_20.1"))


class MultiArmPredecessorTests(unittest.TestCase):
    """Phase 5: same pred model works across arm.0 ‖ arm.1 (not mock-specific)."""

    def setUp(self) -> None:
        self.matrix = CommandMatrix.from_capabilities(
            "any.edge",
            {
                "execution_threads": [
                    {"id": "arm.0", "kind": "arm"},
                    {"id": "arm.1", "kind": "arm"},
                    {"id": "sense.0", "kind": "sense"},
                ]
            },
        )

    def test_arm1_head_blocked_until_arm0_pred_done(self) -> None:
        a = self.matrix.enqueue(
            {"action": "MOVE_COMPONENT", "target_id": "A", "parameters": {
                "target_x": 0.0, "target_y": 0.0, "rotation": 0.0,
            }},
            lease_id="L",
        )
        # Force second command onto arm.1 by using a payload that still routes
        # to arm — matrix routes MOVE to all arms as resources; pick by claim.
        # Enqueue B with predecessor A; both may share arm columns depending on
        # route table — assert B cannot run until A is done when B lists A.
        b = self.matrix.enqueue(
            {
                "action": "MOVE_COMPONENT",
                "target_id": "B",
                "parameters": {"target_x": 1.0, "target_y": 1.0, "rotation": 0.0},
                "predecessors": [a["command_id"]],
            },
            lease_id="L",
        )
        # With a single arm.0 in resources for MOVE, B is queued behind A on the
        # same column OR blocked by pred. Either way B is not runnable first.
        first = self.matrix.claim_runnable("arm.0")
        self.assertIsNotNone(first)
        assert first is not None
        self.assertEqual(first.command_id, a["command_id"])
        self.assertIsNone(self.matrix.pop_runnable("arm.0"))
        self.assertIsNone(self.matrix.pop_runnable("arm.1"))
        self.matrix.mark_done(a["command_id"])
        # After A done, B becomes runnable on some arm column.
        nxt = self.matrix.claim_runnable("arm.0") or self.matrix.claim_runnable("arm.1")
        self.assertIsNotNone(nxt)
        assert nxt is not None
        self.assertEqual(nxt.command_id, b["command_id"])


if __name__ == "__main__":
    unittest.main()
