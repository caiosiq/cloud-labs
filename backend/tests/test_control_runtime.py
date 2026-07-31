"""Tests for RuntimeManager, ControlManager, projections, diff, reconcile."""

from __future__ import annotations

import asyncio
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path

from lab_model.coordinator.backends.lab_view_config import bootstrap_lab_view
from lab_model.language.primitives.ids import PrimitiveId
from lab_model.coordinator.state.control_manager import ControlManager
from lab_model.coordinator.state.diff import configuration_diff
from lab_model.coordinator.state.projections import (
    extract_configuration,
    extract_configuration_metadata,
    extract_observations,
)
from lab_model.coordinator.state.reconcile import plan_reconcile
from lab_model.coordinator.state.reconcile_executor import validate_reconcile_plan
from lab_model.coordinator.state.runtime_manager import MutationKind, RuntimeManager

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_MOCK_LAB_VIEW = _PROJECT_ROOT / "mock_backend" / "lab_view"
_LAB_STATE = _MOCK_LAB_VIEW / "lab_state.json"


class ControlRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ["LAB_VIEW_PATH"] = str(_MOCK_LAB_VIEW)
        bootstrap_lab_view(str(_PROJECT_ROOT))
        from lab_model.coordinator.backends.lab_view_config import get_lab_view_paths
        from lab_model.language.domain import motor_rotation_store as motor_rot

        motor_rot.configure(get_lab_view_paths().motor_rotations_json)
        with open(_LAB_STATE, "r", encoding="utf-8") as handle:
            cls.fixture_runtime = json.load(handle)

    def test_extract_configuration_excludes_measurables(self) -> None:
        configuration = extract_configuration(self.fixture_runtime)
        tag = next(iter(configuration["components"].keys()))
        sc = configuration["components"][tag]["statecontrol"]
        self.assertIn("tunables", sc)
        self.assertNotIn("measurables", sc)

    def test_extract_observations_excludes_tunables(self) -> None:
        observations = extract_observations(self.fixture_runtime)
        tag = next(iter(observations["components"].keys()))
        sc = observations["components"][tag]["statecontrol"]
        self.assertIn("measurables", sc)
        self.assertNotIn("tunables", sc)

    def test_apply_configuration_projection_updates_tunables_only(self) -> None:
        runtime_mgr = RuntimeManager(self.fixture_runtime)
        before_obs = runtime_mgr.extract_observations()
        configuration = runtime_mgr.extract_configuration()
        first_tag = next(iter(configuration["components"].keys()))
        tun = configuration["components"][first_tag]["statecontrol"]["tunables"]
        tun["nominal_pose"] = {"x": 123.0, "y": 456.0, "rotation": 7.0}

        runtime_mgr.apply_configuration_projection(configuration, source="test")
        after_obs = runtime_mgr.extract_observations()
        self.assertEqual(before_obs, after_obs)
        updated = runtime_mgr.extract_configuration()
        pose = updated["components"][first_tag]["statecontrol"]["tunables"]["nominal_pose"]
        self.assertEqual(pose["x"], 123.0)
        self.assertEqual(pose["y"], 456.0)

    def test_runtime_manager_records_mutations(self) -> None:
        runtime_mgr = RuntimeManager({"components": {}, "holding": {"tag_id": None}})
        runtime_mgr.replace_state(
            {"components": {"tag_1": {}}, "holding": {"tag_id": None}},
            kind=MutationKind.ADMINISTRATIVE_LOAD,
            source="test",
        )
        log = runtime_mgr.mutation_log()
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0].kind, MutationKind.ADMINISTRATIVE_LOAD)

    def test_control_manager_commit_and_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mgr = ControlManager(tmp, "test")
            commit = mgr.commit_from_runtime(
                self.fixture_runtime,
                message="initial layout",
                branch="main",
            )
            self.assertEqual(mgr.get_head("main"), commit["id"])
            history = mgr.list_history("main")
            self.assertEqual(len(history), 1)
            loaded = mgr.get_configuration(commit["id"])
            self.assertEqual(loaded["message"], "initial layout")

    def test_configuration_diff_and_reconcile_plan(self) -> None:
        current = extract_configuration(self.fixture_runtime)
        target = json.loads(json.dumps(current))
        tag = next(iter(target["components"].keys()))
        tun = target["components"][tag]["statecontrol"]["tunables"]
        tun["nominal_pose"] = {"x": 1.0, "y": 2.0, "rotation": 3.0}
        tun["exposure_time_ms"] = 250.0

        changes = configuration_diff(current, target)
        self.assertTrue(any(item["path"] == "tunables.nominal_pose" for item in changes))

        plan = plan_reconcile(current, target)
        actions = {step["action"] for step in plan}
        self.assertIn(PrimitiveId.MOVE_COMPONENT, actions)

    def test_reconcile_place_from_storage_carries_target_pose(self) -> None:
        # A part moving STORAGE -> BREADBOARD must produce a PLACE_FROM_STORAGE
        # carrying the destination pose (target_x/target_y), and that envelope
        # must pass command-schema validation (regression: empty parameters {}).
        current = {
            "components": {
                "tag_x": {
                    "statecontrol": {
                        "tunables": {
                            "presence": "storage",
                            "nominal_pose": {"x": -112.5, "y": -332.5, "rotation": 0.0},
                            "storage": {"in_storage": True, "slot": {"i": 1, "j": 0}},
                        }
                    }
                }
            }
        }
        target = {
            "components": {
                "tag_x": {
                    "statecontrol": {
                        "tunables": {
                            "presence": "breadboard",
                            "nominal_pose": {"x": 150.0, "y": 60.0, "rotation": 45.0},
                            "storage": {"in_storage": False, "slot": None},
                        }
                    }
                }
            }
        }

        plan = plan_reconcile(current, target)
        place_steps = [
            s for s in plan if s["action"] == PrimitiveId.PLACE_FROM_STORAGE
        ]
        self.assertEqual(len(place_steps), 1)
        params = place_steps[0]["parameters"]
        self.assertEqual(params["target_x"], 150.0)
        self.assertEqual(params["target_y"], 60.0)
        self.assertEqual(params["rotation"], 45.0)

        # No standalone MOVE_COMPONENT for a part that is being placed from storage.
        self.assertFalse(
            any(s["action"] == PrimitiveId.MOVE_COMPONENT for s in plan)
        )

        # The full plan must validate against the command schema.
        validate_reconcile_plan(plan)

    def test_reconcile_store_skips_redundant_move(self) -> None:
        # A part moving BREADBOARD -> STORAGE should STORE (not MOVE), and the
        # plan must validate.
        current = {
            "components": {
                "tag_y": {
                    "statecontrol": {
                        "tunables": {
                            "presence": "breadboard",
                            "nominal_pose": {"x": 150.0, "y": 60.0, "rotation": 0.0},
                            "storage": {"in_storage": False, "slot": None},
                        }
                    }
                }
            }
        }
        target = {
            "components": {
                "tag_y": {
                    "statecontrol": {
                        "tunables": {
                            "presence": "storage",
                            "nominal_pose": {"x": -112.5, "y": -332.5, "rotation": 0.0},
                            "storage": {"in_storage": True, "slot": {"i": 1, "j": 0}},
                        }
                    }
                }
            }
        }

        plan = plan_reconcile(current, target)
        actions = [s["action"] for s in plan]
        self.assertIn(PrimitiveId.STORE_COMPONENT, actions)
        self.assertNotIn(PrimitiveId.MOVE_COMPONENT, actions)
        # Exactly one STORE (not double-emitted by presence + storage branches).
        self.assertEqual(actions.count(PrimitiveId.STORE_COMPONENT), 1)
        validate_reconcile_plan(plan)

    def test_plan_checkout_from_runtime_uses_actual_bench(self) -> None:
        # Regression: hard checkout must plan from the ACTUAL bench, not the
        # branch HEAD. Two commits (A=HEAD, B); bench currently sits at B's
        # layout. Planning B->A must produce real steps; planning from HEAD
        # would wrongly yield "no motion required".
        import copy

        with tempfile.TemporaryDirectory() as tmp:
            mgr = ControlManager(tmp, "test")

            runtime_a = copy.deepcopy(self.fixture_runtime)
            tag = next(
                t
                for t, c in runtime_a["components"].items()
                if (c.get("statecontrol", {}).get("tunables", {}) or {}).get("presence")
                == "breadboard"
            )
            # Commit A (becomes HEAD).
            commit_a = mgr.commit_from_runtime(runtime_a, message="A", branch="main")

            # Bench now sits at a different layout B (move `tag`).
            runtime_b = copy.deepcopy(runtime_a)
            pose_b = runtime_b["components"][tag]["statecontrol"]["tunables"][
                "nominal_pose"
            ]
            pose_b["x"] = float(pose_b.get("x", 0.0)) + 25.0

            # Planning from the actual bench (B) toward HEAD (A) yields motion.
            plan = mgr.plan_checkout_from_runtime(runtime_b, commit_a["id"])
            self.assertTrue(plan, "expected motion when bench differs from target")
            self.assertTrue(
                any(
                    s["action"] == PrimitiveId.MOVE_COMPONENT and s["target_id"] == tag
                    for s in plan
                )
            )

            # Bench already at target -> no motion (correctly).
            plan_noop = mgr.plan_checkout_from_runtime(runtime_a, commit_a["id"])
            self.assertEqual(plan_noop, [])

    def test_list_and_create_control_repos(self) -> None:
        from lab_model.coordinator.state.control_manager import (
            create_control_repo,
            list_control_repos,
            validate_repo_id,
        )

        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(list_control_repos(tmp), [])

            with self.assertRaises(ValueError):
                validate_repo_id("Bad Name!")

            repo = create_control_repo(tmp, "experiment-a", display_name="Experiment A")
            self.assertEqual(repo["repo_id"], "experiment-a")
            self.assertEqual(repo["display_name"], "Experiment A")
            self.assertEqual(repo["configuration_count"], 0)

            listed = list_control_repos(tmp)
            self.assertEqual(len(listed), 1)
            self.assertEqual(listed[0]["repo_id"], "experiment-a")
            self.assertEqual(listed[0]["heads"].get("main"), None)

            with self.assertRaises(FileExistsError):
                create_control_repo(tmp, "experiment-a")

    def test_control_manager_applied_ref(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mgr = ControlManager(tmp, "test")
            commit = mgr.commit_from_runtime(
                self.fixture_runtime,
                message="initial layout",
                branch="main",
            )
            applied = mgr.get_applied()
            self.assertEqual(applied["configuration_id"], commit["id"])
            self.assertEqual(applied["branch"], "main")
            status = mgr.status()
            self.assertEqual(status["applied"]["configuration_id"], commit["id"])

            mgr.set_applied(None, branch=None)
            self.assertIsNone(mgr.get_applied()["configuration_id"])

    def test_control_manager_fork_branch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mgr = ControlManager(tmp, "test")
            commit = mgr.commit_from_runtime(self.fixture_runtime, message="base")
            mgr.fork_branch(branch="experiment-a", parent_id=str(commit["id"]))
            self.assertEqual(mgr.get_head("experiment-a"), commit["id"])
            history = mgr.list_history("experiment-a")
            self.assertEqual(len(history), 1)
            self.assertEqual(history[0]["id"], commit["id"])

    def test_working_state_clean_on_head_after_commit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mgr = ControlManager(tmp, "test")
            mgr.commit_from_runtime(self.fixture_runtime, message="base")
            ws = mgr.working_state(self.fixture_runtime)
            self.assertTrue(ws["on_head"])
            self.assertFalse(ws["detached"])
            self.assertFalse(ws["dirty"])
            self.assertIsNone(ws["viewing"])
            self.assertIsNone(ws["stash"])

    def test_runtime_is_dirty_detects_edit_and_ignores_preview(self) -> None:
        import copy

        with tempfile.TemporaryDirectory() as tmp:
            mgr = ControlManager(tmp, "test")
            mgr.commit_from_runtime(self.fixture_runtime, message="base")

            self.assertFalse(mgr.runtime_is_dirty(self.fixture_runtime))

            edited = copy.deepcopy(self.fixture_runtime)
            # Pick an active *table* component: stored / off-table parts are
            # inventory and excluded from the versioned configuration, so editing
            # them is intentionally NOT a dirty-making change (membership model).
            tag = next(iter(extract_configuration(self.fixture_runtime)["components"].keys()))
            tun = edited["components"][tag]["statecontrol"]["tunables"]
            tun["nominal_pose"] = {"x": 999.0, "y": 888.0, "rotation": 12.0}
            self.assertTrue(mgr.runtime_is_dirty(edited))

            # Preview is now a read-only frontend overlay: it never mutates the
            # live runtime, so the `viewing` pointer no longer affects dirty â€”
            # dirty is purely bench-vs-applied.
            mgr.set_viewing("some-other-commit")
            self.assertTrue(mgr.runtime_is_dirty(edited))
            mgr.set_viewing(None)
            self.assertTrue(mgr.runtime_is_dirty(edited))

    def test_working_state_detached_when_applied_not_head(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mgr = ControlManager(tmp, "test")
            commit_a = mgr.commit_from_runtime(self.fixture_runtime, message="A")
            commit_b = mgr.commit_from_runtime(self.fixture_runtime, message="B")
            self.assertEqual(mgr.get_head("main"), commit_b["id"])

            # Revert applied to A (older commit) -> detached.
            mgr.set_applied(commit_a["id"], branch="main")
            ws = mgr.working_state(self.fixture_runtime)
            self.assertFalse(ws["on_head"])
            self.assertTrue(ws["detached"])

    def test_fork_lands_on_head_and_clears_preview(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mgr = ControlManager(tmp, "test")
            commit_a = mgr.commit_from_runtime(self.fixture_runtime, message="A")
            mgr.commit_from_runtime(self.fixture_runtime, message="B")
            # Detach onto A, then fork a branch from A.
            mgr.set_applied(commit_a["id"], branch="main")
            mgr.set_viewing(commit_a["id"])
            mgr.fork_branch(branch="experiment", parent_id=commit_a["id"])

            applied = mgr.get_applied()
            self.assertEqual(applied["configuration_id"], commit_a["id"])
            self.assertEqual(applied["branch"], "experiment")
            ws = mgr.working_state(self.fixture_runtime)
            self.assertTrue(ws["on_head"])
            self.assertFalse(ws["detached"])
            self.assertIsNone(ws["viewing"])

    def test_stash_store_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mgr = ControlManager(tmp, "test")
            commit = mgr.commit_from_runtime(self.fixture_runtime, message="base")
            self.assertIsNone(mgr.get_stash())

            snapshot = extract_configuration(self.fixture_runtime)
            stash = mgr.save_stash(
                snapshot,
                base_configuration_id=commit["id"],
                base_branch="main",
                message="wip",
            )
            self.assertEqual(stash["base_configuration_id"], commit["id"])

            loaded = mgr.get_stash()
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded["configuration"], snapshot)

            summary = mgr.stash_summary()
            self.assertIsNotNone(summary)
            self.assertNotIn("configuration", summary)
            self.assertEqual(summary["message"], "wip")

            mgr.clear_stash()
            self.assertIsNone(mgr.get_stash())
            self.assertIsNone(mgr.stash_summary())

    def test_plan_runtime_to_configuration_targets_snapshot(self) -> None:
        import copy

        with tempfile.TemporaryDirectory() as tmp:
            mgr = ControlManager(tmp, "test")
            base = copy.deepcopy(self.fixture_runtime)
            tag = next(
                t
                for t, c in base["components"].items()
                if (c.get("statecontrol", {}).get("tunables", {}) or {}).get("presence")
                == "breadboard"
            )
            snapshot = extract_configuration(base)
            snap_tun = snapshot["components"][tag]["statecontrol"]["tunables"]
            snap_tun["nominal_pose"] = {
                "x": float(snap_tun.get("nominal_pose", {}).get("x", 0.0)) + 40.0,
                "y": 200.0,
                "rotation": 10.0,
            }
            plan = mgr.plan_runtime_to_configuration(base, snapshot)
            self.assertTrue(
                any(
                    s["action"] == PrimitiveId.MOVE_COMPONENT and s["target_id"] == tag
                    for s in plan
                )
            )
            validate_reconcile_plan(plan)

    def test_apply_hard_checkout_projection_clears_observations(self) -> None:
        runtime_mgr = RuntimeManager(self.fixture_runtime)
        before_obs = runtime_mgr.extract_observations()
        tag = next(iter(before_obs["components"].keys()))
        self.assertIn("measurables", before_obs["components"][tag]["statecontrol"])

        configuration = runtime_mgr.extract_configuration()
        runtime_mgr.apply_hard_checkout_projection(configuration, source="test")
        after_obs = runtime_mgr.extract_observations()
        pose = after_obs["components"][tag]["statecontrol"]["measurables"]["pose"]
        self.assertEqual(pose, {"x": 0.0, "y": 0.0, "rotation": 0.0})

    def test_execute_reconcile_plan_moves_mock_component(self) -> None:
        import copy

        from mock_backend.host.communicator import MockLabCommunicator
        from lab_model.coordinator.state.reconcile_executor import execute_reconcile_plan

        lab = MockLabCommunicator()
        state = copy.deepcopy(self.fixture_runtime)
        lab.set_lab_state(state)

        current = extract_configuration(state)
        target = json.loads(json.dumps(current))
        # Pick a component that is on the breadboard (movable) â€” a STORED part
        # would be (correctly) refused by MOVE_COMPONENT, so blindly taking the
        # first component is fragile as the lab_state fixture evolves.
        tag = next(
            t
            for t, c in target["components"].items()
            if (c.get("statecontrol", {}).get("tunables", {}) or {}).get("presence")
            == "breadboard"
        )
        tun = target["components"][tag]["statecontrol"]["tunables"]
        base_x = float(tun.get("nominal_pose", {}).get("x", 0.0))
        tun["nominal_pose"] = {
            "x": base_x + 73.0,
            "y": 222.0,
            "rotation": 33.0,
        }
        plan = plan_reconcile(current, target)
        self.assertTrue(plan, "expected at least one reconcile step")
        validate_reconcile_plan(plan)

        async def _run() -> None:
            result = await execute_reconcile_plan(lab, plan, source="test")
            self.assertEqual(result["steps_executed"], len(plan))

        asyncio.run(_run())
        updated = extract_configuration(lab.get_lab_state())
        pose = updated["components"][tag]["statecontrol"]["tunables"]["nominal_pose"]
        self.assertAlmostEqual(pose["x"], base_x + 73.0)
        self.assertEqual(pose["y"], 222.0)

    def test_extract_configuration_strips_placement_and_metadata_roundtrip(self) -> None:
        runtime = json.loads(json.dumps(self.fixture_runtime))
        tag = next(
            t
            for t, c in runtime["components"].items()
            if (c.get("statecontrol", {}).get("tunables", {}) or {}).get("presence")
            == "breadboard"
        )
        sc = runtime["components"][tag]["statecontrol"]
        sc["tunables"]["placement"] = {"mode": "NEWTON"}
        sc["measurables"]["last_optimization_score"] = 0.87
        sc["measurables"]["last_optimized_pose"] = {
            "x": 1.0,
            "y": 2.0,
            "rotation": 3.0,
        }

        configuration = extract_configuration(runtime)
        tun = configuration["components"][tag]["statecontrol"]["tunables"]
        self.assertNotIn("placement", tun)

        metadata = extract_configuration_metadata(runtime)
        self.assertEqual(
            metadata["optimization"][tag]["placement_mode"],
            "NEWTON",
        )
        self.assertAlmostEqual(
            metadata["optimization"][tag]["last_optimization_score"],
            0.87,
        )

        with tempfile.TemporaryDirectory() as tmp:
            mgr = ControlManager(tmp, "test")
            commit = mgr.commit_from_runtime(runtime, message="optimized", branch="main")
            loaded = mgr.get_configuration(commit["id"])
            self.assertIn("metadata", loaded)
            self.assertEqual(
                loaded["metadata"]["optimization"][tag]["placement_mode"],
                "NEWTON",
            )

    def test_placement_mode_does_not_trigger_configuration_diff(self) -> None:
        current = extract_configuration(self.fixture_runtime)
        target = json.loads(json.dumps(current))
        tag = next(iter(target["components"].keys()))
        tun = target["components"][tag]["statecontrol"]["tunables"]
        tun["placement"] = {"mode": "NEWTON"}

        changes = configuration_diff(current, target)
        self.assertFalse(
            any(item.get("path") == "tunables.placement" for item in changes),
            changes,
        )
        plan = plan_reconcile(current, target)
        self.assertEqual(plan, [])

    def test_hard_checkout_restores_optimization_metadata(self) -> None:
        runtime = json.loads(json.dumps(self.fixture_runtime))
        tag = next(
            t
            for t, c in runtime["components"].items()
            if (c.get("statecontrol", {}).get("tunables", {}) or {}).get("presence")
            == "breadboard"
        )
        sc = runtime["components"][tag]["statecontrol"]
        sc["tunables"]["placement"] = {"mode": "COBYLA"}
        sc["measurables"]["last_optimization_score"] = 0.75

        configuration = extract_configuration(runtime)
        metadata = extract_configuration_metadata(runtime)
        runtime_mgr = RuntimeManager(runtime)
        runtime_mgr.apply_hard_checkout_projection(
            configuration,
            source="test",
            metadata=metadata,
        )
        obs = runtime_mgr.extract_observations()
        meas = obs["components"][tag]["statecontrol"]["measurables"]
        self.assertAlmostEqual(meas["last_optimization_score"], 0.75)
        updated = runtime_mgr.extract_configuration()
        tun = updated["components"][tag]["statecontrol"]["tunables"]
        self.assertNotIn("placement", tun)

    def test_commit_optimization_complete_syncs_nominal_pose(self) -> None:
        from lab_model.coordinator.state.commits import commit_optimization_complete

        runtime = json.loads(json.dumps(self.fixture_runtime))
        tag = next(
            t
            for t, c in runtime["components"].items()
            if (c.get("statecontrol", {}).get("tunables", {}) or {}).get("presence")
            == "breadboard"
        )
        commit_optimization_complete(
            runtime,
            tag,
            strategy_name="NEWTON",
            score=0.91,
            final_pose={"x": 10.0, "y": 20.0, "rotation": 5.0},
        )
        sc = runtime["components"][tag]["statecontrol"]
        self.assertEqual(
            sc["tunables"]["nominal_pose"],
            {"x": 10.0, "y": 20.0, "rotation": 5.0},
        )
        self.assertAlmostEqual(
            sc["measurables"]["last_optimization_score"],
            0.91,
        )

    def test_backfill_optimization_metadata_from_legacy_commit(self) -> None:
        from lab_model.coordinator.state.control_documents import configuration_document
        from lab_model.coordinator.state.projections import infer_configuration_metadata_from_document

        legacy_cfg = {
            "holding": {"tag_id": None, "nominal_pose": None},
            "components": {
                "tag_a": {
                    "statecontrol": {
                        "tunables": {
                            "presence": "breadboard",
                            "nominal_pose": {"x": 1.0, "y": 2.0, "rotation": 0.0},
                            "placement": {"mode": "NEWTON"},
                        }
                    }
                }
            },
        }
        observations = {
            "components": {
                "tag_a": {
                    "statecontrol": {
                        "measurables": {
                            "last_optimization_score": 0.88,
                            "last_optimized_pose": {"x": 1.0, "y": 2.0, "rotation": 0.0},
                        }
                    }
                }
            }
        }
        doc = configuration_document(
            commit_id="abc",
            repo_id="test",
            branch="main",
            parent_id=None,
            message="legacy",
            configuration=copy.deepcopy(legacy_cfg),
            created_at="2026-01-01T00:00:00+00:00",
        )
        inferred = infer_configuration_metadata_from_document(
            doc,
            observations=observations,
        )
        self.assertAlmostEqual(
            inferred["optimization"]["tag_a"]["last_optimization_score"],
            0.88,
        )
        self.assertEqual(inferred["optimization"]["tag_a"]["placement_mode"], "NEWTON")

        with tempfile.TemporaryDirectory() as tmp:
            mgr = ControlManager(tmp, "test")
            mgr.save_configuration_document(doc)
            obs_dir = mgr.observations_dir
            os.makedirs(obs_dir, exist_ok=True)
            pin_path = os.path.join(obs_dir, "pin1.json")
            with open(pin_path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "configuration_id": "abc",
                        "created_at": "2026-01-02T00:00:00+00:00",
                        "observations": observations,
                    },
                    handle,
                )
            updated = mgr.backfill_optimization_metadata()
            self.assertEqual(updated, 1)
            loaded = mgr.get_configuration("abc")
            self.assertIn("metadata", loaded)
            self.assertNotIn(
                "placement",
                loaded["configuration"]["components"]["tag_a"]["statecontrol"]["tunables"],
            )
            self.assertEqual(mgr.backfill_optimization_metadata(), 0)


if __name__ == "__main__":
    unittest.main()
