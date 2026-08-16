"""Seat-DAG batch planner (Phase 2): swap, cycles, storage peak, overlap."""

from __future__ import annotations

import unittest

from lab_model.coordinator.state.batch_plan import (
    BatchPlanError,
    parse_staging_seats,
    plan_batch,
    pose_matches_staging,
    staging_commit_issues,
    strip_staging_from_configuration,
)
from lab_model.coordinator.state.reconcile import plan_reconcile
from lab_model.coordinator.state.reconcile_executor import validate_reconcile_plan
from lab_model.language.primitives.ids import PrimitiveId


STAGING = [
    {"x": 400.0, "y": 400.0, "rotation": 0.0},
    {"x": 325.0, "y": 400.0, "rotation": 0.0},
]

# Compact footprints so 100 mm seat spacing is a valid final layout in swap tests.
_SMALL = lambda _t: (20.0, 20.0)


def _bb(tag: str, x: float, y: float, rot: float = 0.0) -> dict:
    return {
        tag: {
            "statecontrol": {
                "tunables": {
                    "presence": "breadboard",
                    "nominal_pose": {"x": x, "y": y, "rotation": rot},
                    "storage": {"in_storage": False, "slot": None},
                }
            }
        }
    }


def _stored(tag: str, i: int, j: int) -> dict:
    return {
        tag: {
            "statecontrol": {
                "tunables": {
                    "presence": "storage",
                    "storage": {"in_storage": True, "slot": {"i": i, "j": j}},
                }
            }
        }
    }


class ParseStagingSeatsTests(unittest.TestCase):
    def test_from_layout_doc(self) -> None:
        seats = parse_staging_seats({"reconcile_staging_seats": STAGING})
        self.assertEqual(len(seats), 2)
        self.assertEqual(seats[0]["x"], 400.0)

    def test_pose_match_and_strip(self) -> None:
        cfg = {"components": {**_bb("parked", 400.0, 400.0), **_bb("ok", 10.0, 20.0)}}
        self.assertEqual(pose_matches_staging({"x": 400, "y": 400}, STAGING), 0)
        issues = staging_commit_issues(cfg, STAGING)
        self.assertEqual(len(issues), 1)
        stripped = strip_staging_from_configuration(cfg, STAGING)
        self.assertNotIn("parked", stripped["components"])
        self.assertIn("ok", stripped["components"])


class SwapAndCycleTests(unittest.TestCase):
    def test_ab_swap_parks_then_moves(self) -> None:
        current = {"components": {**_bb("A", 0.0, 0.0), **_bb("B", 100.0, 0.0)}}
        target = {"components": {**_bb("A", 100.0, 0.0), **_bb("B", 0.0, 0.0)}}
        result = plan_batch(
            current, target, staging_seats=STAGING, size_fn=_SMALL, pad_mm=5.0
        )
        self.assertTrue(result.ready, result.report)
        roles = [c.get("plan_role") for c in result.commands]
        self.assertEqual(roles.count("park"), 1)
        self.assertEqual(roles.count("unpark"), 1)
        self.assertEqual(roles.count("move"), 1)
        # Park first, unpark last among spatial.
        park_i = roles.index("park")
        move_i = roles.index("move")
        unpark_i = roles.index("unpark")
        self.assertLess(park_i, move_i)
        self.assertLess(move_i, unpark_i)
        validate_reconcile_plan(result.reconcile_plan())

    def test_swap_without_staging_fails_closed(self) -> None:
        current = {"components": {**_bb("A", 0.0, 0.0), **_bb("B", 100.0, 0.0)}}
        target = {"components": {**_bb("A", 100.0, 0.0), **_bb("B", 0.0, 0.0)}}
        result = plan_batch(
            current, target, staging_seats=[], size_fn=_SMALL, pad_mm=5.0
        )
        self.assertFalse(result.ready)
        self.assertEqual(result.report["needs_staging_n"], 1)
        kinds = {i["kind"] for i in result.report["issues"]}
        self.assertIn("needs_staging", kinds)
        with self.assertRaises(BatchPlanError):
            plan_reconcile(current, target, staging_seats=[], size_fn=_SMALL, pad_mm=5.0)

    def test_cycle3_needs_one_staging(self) -> None:
        # A→B's seat, B→C's seat, C→A's seat
        current = {
            "components": {
                **_bb("A", 0.0, 0.0),
                **_bb("B", 100.0, 0.0),
                **_bb("C", 200.0, 0.0),
            }
        }
        target = {
            "components": {
                **_bb("A", 100.0, 0.0),
                **_bb("B", 200.0, 0.0),
                **_bb("C", 0.0, 0.0),
            }
        }
        result = plan_batch(
            current, target, staging_seats=STAGING[:1], size_fn=_SMALL, pad_mm=5.0
        )
        self.assertTrue(result.ready, result.report)
        self.assertEqual(result.report["needs_staging_n"], 1)
        self.assertEqual(
            sum(1 for c in result.commands if c.get("plan_role") == "park"), 1
        )


class FootprintDagTests(unittest.TestCase):
    def test_near_miss_triggers_dep_and_park(self) -> None:
        # 10×10 mm parts; B wants a center only 7 mm from A's current center.
        # Twin circle+pad must see a collision → A before B (or park on cycle).
        size = lambda _t: (10.0, 10.0)
        current = {
            "components": {
                **_bb("A", 0.0, 0.0),
                **_bb("B", 50.0, 0.0),
            }
        }
        target = {
            "components": {
                **_bb("A", 50.0, 0.0),
                **_bb("B", 7.0, 0.0),
            }
        }
        # Without staging this near-miss swap-like pattern may need park.
        result = plan_batch(
            current,
            target,
            staging_seats=STAGING,
            size_fn=size,
            pad_mm=5.0,
        )
        self.assertTrue(result.ready, result.report)
        # Footprints collide: either park/unpark or ordered moves with preds.
        roles = [c.get("plan_role") for c in result.commands]
        self.assertTrue(
            roles.count("park") >= 1 or any(c.get("predecessors") for c in result.commands),
            roles,
        )

    def test_far_apart_no_spurious_dep(self) -> None:
        size = lambda _t: (10.0, 10.0)
        current = {"components": {**_bb("A", 0.0, 0.0), **_bb("B", 200.0, 0.0)}}
        target = {"components": {**_bb("A", 0.0, 0.0), **_bb("B", 210.0, 0.0)}}
        result = plan_batch(
            current, target, staging_seats=STAGING, size_fn=size, pad_mm=5.0
        )
        self.assertTrue(result.ready, result.report)
        self.assertEqual(result.report.get("needs_staging_n"), 0)
        moves = [c for c in result.commands if c.get("plan_role") == "move"]
        self.assertEqual(len(moves), 1)
        self.assertEqual(moves[0]["target_id"], "B")
        self.assertEqual(moves[0].get("predecessors") or [], [])

    def test_footprints_collide_helper(self) -> None:
        from lab_model.coordinator.state.batch_plan import footprints_collide

        # 10×10, centers 7 mm apart, pad 5 → collide
        self.assertTrue(
            footprints_collide(0, 0, 10, 10, 7, 0, 10, 10, pad_mm=5.0)
        )
        # Far apart → clear
        self.assertFalse(
            footprints_collide(0, 0, 10, 10, 200, 0, 10, 10, pad_mm=5.0)
        )


class StorageAndOverlapTests(unittest.TestCase):
    def test_remove_then_place_same_seat(self) -> None:
        current = {
            "components": {
                **_bb("B", 50.0, 50.0),
                **_stored("A", 0, 0),
            }
        }
        target = {
            "components": {
                **_stored("B", 1, 0),
                **_bb("A", 50.0, 50.0),
            }
        }
        result = plan_batch(current, target, staging_seats=STAGING, free_storage_slots=4)
        self.assertTrue(result.ready, result.report)
        actions = [c["action"] for c in result.commands]
        self.assertIn(PrimitiveId.STORE_COMPONENT, actions)
        self.assertIn(PrimitiveId.PLACE_FROM_STORAGE, actions)
        store_i = actions.index(PrimitiveId.STORE_COMPONENT)
        place_i = actions.index(PrimitiveId.PLACE_FROM_STORAGE)
        self.assertLess(store_i, place_i)
        place = next(
            c for c in result.commands if c["action"] == PrimitiveId.PLACE_FROM_STORAGE
        )
        self.assertIn(place["predecessors"][0], {c["plan_step_id"] for c in result.commands})

    def test_storage_peak_fail(self) -> None:
        current = {
            "components": {
                **_bb("A", 0.0, 0.0),
                **_bb("B", 100.0, 0.0),
            }
        }
        target = {
            "components": {
                **_stored("A", 0, 0),
                **_stored("B", 1, 0),
            }
        }
        result = plan_batch(current, target, free_storage_slots=1)
        self.assertFalse(result.ready)
        kinds = {i["kind"] for i in result.report["issues"]}
        self.assertIn("storage_peak", kinds)

    def test_target_overlap_fail(self) -> None:
        current = {
            "components": {
                **_bb("A", 0.0, 0.0),
                **_bb("B", 100.0, 0.0),
            }
        }
        target = {
            "components": {
                **_bb("A", 50.0, 50.0),
                **_bb("B", 50.0, 50.0),
            }
        }
        result = plan_batch(current, target, staging_seats=STAGING)
        self.assertFalse(result.ready)
        kinds = {i["kind"] for i in result.report["issues"]}
        self.assertIn("target_overlap", kinds)


class ReconcileCompatTests(unittest.TestCase):
    def test_simple_move_still_plans(self) -> None:
        current = {"components": {**_bb("A", 0.0, 0.0)}}
        target = {"components": {**_bb("A", 10.0, 20.0, 45.0)}}
        plan = plan_reconcile(current, target, staging_seats=STAGING)
        self.assertEqual(len(plan), 1)
        self.assertEqual(plan[0]["action"], PrimitiveId.MOVE_COMPONENT)
        validate_reconcile_plan(plan)

    def test_place_from_storage(self) -> None:
        current = {"components": {**_stored("X", 1, 0)}}
        target = {"components": {**_bb("X", 150.0, 60.0, 45.0)}}
        plan = plan_reconcile(current, target)
        actions = [s["action"] for s in plan]
        self.assertIn(PrimitiveId.PLACE_FROM_STORAGE, actions)
        self.assertNotIn(PrimitiveId.MOVE_COMPONENT, actions)
        validate_reconcile_plan(plan)


if __name__ == "__main__":
    unittest.main()
