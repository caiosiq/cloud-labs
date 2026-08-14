"""Membership model: stored/off-table parts are inventory, not versioned table
components; add/remove is realized via storage primitives; cross-repo diffs are
anchored to a shared empty (zeroth) state; bench ownership tracks the one repo
that physically realized the global bench."""

from __future__ import annotations

import copy
import tempfile
import unittest

from lab_model.language.domain.component import (
    PRESENCE_BREADBOARD,
    PRESENCE_OFF_TABLE,
    PRESENCE_STORAGE,
)
from lab_model.language.primitives.ids import PrimitiveId
from lab_model.coordinator.state.control_manager import (
    ControlManager,
    _control_capabilities,
    read_bench_origin,
    repo_owns_bench,
    write_bench_origin,
)
from lab_model.coordinator.state.diff import configuration_diff
from lab_model.coordinator.state.projections import (
    EMPTY_CONFIGURATION,
    apply_configuration_to_components,
    extract_configuration,
    table_configuration,
)
from lab_model.coordinator.state.reconcile import plan_reconcile


def _runtime_entry(tag, presence=PRESENCE_BREADBOARD, *, x=0.0, y=0.0, in_storage=False):
    return {
        "id": tag,
        "type": "mirror",
        "statecontrol": {
            "tunables": {
                "presence": presence,
                "nominal_pose": {"x": x, "y": y, "rotation": 0.0},
                "storage": {"in_storage": in_storage, "slot": None},
            },
            "measurables": {},
        },
        "telemetry": {},
    }


def _runtime(components):
    return {"components": components, "holding": {"tag_id": None, "nominal_pose": None}}


def _cfg_component(tag, *, x=0.0, y=0.0):
    return {
        "components": {
            tag: {
                "id": tag,
                "type": "mirror",
                "statecontrol": {
                    "tunables": {
                        "presence": PRESENCE_BREADBOARD,
                        "nominal_pose": {"x": x, "y": y, "rotation": 0.0},
                    }
                },
            }
        },
        "holding": {"tag_id": None, "nominal_pose": None},
    }


class MembershipExtractTests(unittest.TestCase):
    def test_extract_includes_stored_with_slot_excludes_off_table(self) -> None:
        stored = _runtime_entry(
            "tag_stored", PRESENCE_STORAGE, x=-120.0, y=-80.0, in_storage=True
        )
        stored["statecontrol"]["tunables"]["storage"]["slot"] = {"i": 1, "j": 2}
        runtime = _runtime(
            {
                "tag_on": _runtime_entry("tag_on"),
                "tag_stored": stored,
                "tag_off": _runtime_entry("tag_off", PRESENCE_OFF_TABLE),
            }
        )
        cfg = extract_configuration(runtime)
        self.assertIn("tag_on", cfg["components"])
        self.assertIn("tag_stored", cfg["components"])
        self.assertNotIn("tag_off", cfg["components"])
        tun = cfg["components"]["tag_stored"]["statecontrol"]["tunables"]
        self.assertEqual(tun["presence"], PRESENCE_STORAGE)
        self.assertEqual(tun["storage"]["slot"], {"i": 1, "j": 2})
        self.assertNotIn("nominal_pose", tun)
        self.assertNotIn("reported_pose", tun)

    def test_in_storage_flag_is_versioned(self) -> None:
        runtime = _runtime(
            {"tag_x": _runtime_entry("tag_x", PRESENCE_BREADBOARD, in_storage=True)}
        )
        cfg = extract_configuration(runtime)
        self.assertIn("tag_x", cfg["components"])
        self.assertTrue(
            cfg["components"]["tag_x"]["statecontrol"]["tunables"]["storage"][
                "in_storage"
            ]
        )

    def test_table_configuration_strips_stored_entry(self) -> None:
        legacy = {
            "components": {
                "tag_on": {
                    "statecontrol": {"tunables": {"presence": PRESENCE_BREADBOARD}}
                },
                "tag_stored": {
                    "statecontrol": {
                        "tunables": {
                            "presence": PRESENCE_STORAGE,
                            "storage": {"in_storage": True, "slot": {"i": 0, "j": 0}},
                        }
                    }
                },
            }
        }
        normalized = table_configuration(legacy)
        self.assertEqual(set(normalized["components"].keys()), {"tag_on"})

    def test_lab_configuration_keeps_stored_entry(self) -> None:
        from lab_model.coordinator.state.projections import lab_configuration

        legacy = {
            "components": {
                "tag_on": {
                    "statecontrol": {"tunables": {"presence": PRESENCE_BREADBOARD}}
                },
                "tag_stored": {
                    "statecontrol": {
                        "tunables": {
                            "presence": PRESENCE_STORAGE,
                            "storage": {"in_storage": True, "slot": {"i": 0, "j": 1}},
                        }
                    }
                },
            }
        }
        normalized = lab_configuration(legacy)
        self.assertEqual(set(normalized["components"].keys()), {"tag_on", "tag_stored"})


class MembershipDiffReconcileTests(unittest.TestCase):
    def test_added_component_diff_and_place_from_storage(self) -> None:
        current = EMPTY_CONFIGURATION
        target = _cfg_component("tag_a", x=100.0, y=50.0)
        changes = configuration_diff(current, target)
        comp_changes = [c for c in changes if c["path"] == "component"]
        self.assertEqual(len(comp_changes), 1)
        self.assertEqual(comp_changes[0]["to"], "added")

        plan = plan_reconcile(current, target)
        actions = [c["action"] for c in plan]
        self.assertIn(PrimitiveId.PLACE_FROM_STORAGE, actions)
        place = next(c for c in plan if c["action"] == PrimitiveId.PLACE_FROM_STORAGE)
        self.assertEqual(place["target_id"], "tag_a")
        self.assertEqual(place["parameters"]["target_x"], 100.0)
        self.assertEqual(place["parameters"]["target_y"], 50.0)

    def test_removed_component_stores(self) -> None:
        current = _cfg_component("tag_a")
        target = EMPTY_CONFIGURATION
        plan = plan_reconcile(current, target)
        self.assertEqual(len(plan), 1)
        self.assertEqual(plan[0]["action"], PrimitiveId.STORE_COMPONENT)
        self.assertEqual(plan[0]["target_id"], "tag_a")

    def test_place_extras_restore_exposure(self) -> None:
        target = {
            "components": {
                "cam": {
                    "statecontrol": {
                        "tunables": {
                            "presence": PRESENCE_BREADBOARD,
                            "nominal_pose": {"x": 1.0, "y": 2.0, "rotation": 0.0},
                            "exposure_time_ms": 12.5,
                        }
                    }
                }
            },
            "holding": {"tag_id": None, "nominal_pose": None},
        }
        plan = plan_reconcile(EMPTY_CONFIGURATION, target)
        actions = [c["action"] for c in plan]
        self.assertIn(PrimitiveId.PLACE_FROM_STORAGE, actions)
        self.assertIn(PrimitiveId.SET_EXPOSURE, actions)

    def test_store_to_explicit_slot(self) -> None:
        current = _cfg_component("tag_a", x=10.0, y=20.0)
        target = {
            "components": {
                "tag_a": {
                    "id": "tag_a",
                    "type": "mirror",
                    "statecontrol": {
                        "tunables": {
                            "presence": PRESENCE_STORAGE,
                            "nominal_pose": {"x": -120.0, "y": -80.0, "rotation": 0.0},
                            "storage": {
                                "in_storage": True,
                                "slot": {"i": 2, "j": 1},
                            },
                        }
                    },
                }
            },
            "holding": {"tag_id": None, "nominal_pose": None},
        }
        plan = plan_reconcile(current, target)
        store = next(c for c in plan if c["action"] == PrimitiveId.STORE_COMPONENT)
        self.assertEqual(store["parameters"]["slot_i"], 2)
        self.assertEqual(store["parameters"]["slot_j"], 1)

    def test_storage_slot_change_plans_store(self) -> None:
        current = {
            "components": {
                "tag_a": {
                    "statecontrol": {
                        "tunables": {
                            "presence": PRESENCE_STORAGE,
                            "storage": {
                                "in_storage": True,
                                "slot": {"i": 0, "j": 0},
                            },
                        }
                    }
                }
            },
            "holding": {"tag_id": None, "nominal_pose": None},
        }
        target = {
            "components": {
                "tag_a": {
                    "statecontrol": {
                        "tunables": {
                            "presence": PRESENCE_STORAGE,
                            "storage": {
                                "in_storage": True,
                                "slot": {"i": 1, "j": 2},
                            },
                        }
                    }
                }
            },
            "holding": {"tag_id": None, "nominal_pose": None},
        }
        plan = plan_reconcile(current, target)
        store = next(c for c in plan if c["action"] == PrimitiveId.STORE_COMPONENT)
        self.assertEqual(store["parameters"]["slot_i"], 1)
        self.assertEqual(store["parameters"]["slot_j"], 2)

    def test_stored_pose_in_legacy_config_does_not_plan_motion(self) -> None:
        """Slot-only VC: embedded XY on a stored entry is ignored for reconcile."""
        current = {
            "components": {
                "tag_a": {
                    "statecontrol": {
                        "tunables": {
                            "presence": PRESENCE_STORAGE,
                            "nominal_pose": {"x": -115.0, "y": -78.0, "rotation": 5.0},
                            "storage": {
                                "in_storage": True,
                                "slot": {"i": 1, "j": 2},
                            },
                        }
                    }
                }
            },
            "holding": {"tag_id": None, "nominal_pose": None},
        }
        target = {
            "components": {
                "tag_a": {
                    "statecontrol": {
                        "tunables": {
                            "presence": PRESENCE_STORAGE,
                            "nominal_pose": {"x": -120.0, "y": -80.0, "rotation": 0.0},
                            "storage": {
                                "in_storage": True,
                                "slot": {"i": 1, "j": 2},
                            },
                        }
                    }
                }
            },
            "holding": {"tag_id": None, "nominal_pose": None},
        }
        plan = plan_reconcile(current, target)
        self.assertEqual(plan, [])

    def test_dropping_stored_from_target_is_noop(self) -> None:
        current = {
            "components": {
                "tag_a": {
                    "statecontrol": {
                        "tunables": {
                            "presence": PRESENCE_STORAGE,
                            "storage": {"in_storage": True, "slot": {"i": 0, "j": 0}},
                        }
                    }
                }
            },
            "holding": {"tag_id": None, "nominal_pose": None},
        }
        plan = plan_reconcile(current, EMPTY_CONFIGURATION)
        self.assertEqual(plan, [])


class EmptyBaseDirtyTests(unittest.TestCase):
    def test_dirty_against_empty_when_no_applied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mgr = ControlManager(tmp, "fresh")
            runtime = _runtime({"tag_a": _runtime_entry("tag_a")})
            # No applied node yet: a non-empty bench is uncommitted work on empty.
            self.assertTrue(mgr.runtime_is_dirty(runtime))

    def test_clean_empty_bench_when_no_applied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mgr = ControlManager(tmp, "fresh")
            self.assertFalse(mgr.runtime_is_dirty(_runtime({})))

    def test_storage_only_does_not_dirty_empty_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mgr = ControlManager(tmp, "fresh")
            runtime = _runtime(
                {
                    "tag_s": _runtime_entry(
                        "tag_s", PRESENCE_STORAGE, in_storage=True
                    )
                }
            )
            self.assertFalse(mgr.runtime_is_dirty(runtime))

    def test_dirty_when_applied_versions_storage_slot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mgr = ControlManager(tmp, "fresh")
            stored = _runtime_entry(
                "tag_s", PRESENCE_STORAGE, x=-100.0, y=-100.0, in_storage=True
            )
            stored["statecontrol"]["tunables"]["storage"]["slot"] = {"i": 0, "j": 0}
            mgr.commit_from_runtime(_runtime({"tag_s": stored}), message="inv")
            moved = copy.deepcopy(stored)
            moved["statecontrol"]["tunables"]["storage"]["slot"] = {"i": 1, "j": 0}
            self.assertTrue(mgr.runtime_is_dirty(_runtime({"tag_s": moved})))

    def test_foreign_bench_is_dirty_when_not_owning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mgr = ControlManager(tmp, "repo_b")
            commit = mgr.commit_from_runtime(
                _runtime({"tag_a": _runtime_entry("tag_a")}), message="b base"
            )
            # Bench matches applied -> not dirty while owning.
            runtime = _runtime({"tag_a": _runtime_entry("tag_a")})
            self.assertFalse(mgr.runtime_is_dirty(runtime, owns_bench=True))
            # The raw diff-vs-empty is still dirty (used for stash planning)...
            self.assertTrue(mgr.runtime_is_dirty(runtime, owns_bench=False))
            # ...but working_state surfaces this as "unadopted" (no current node
            # yet), not as dirty work, so the UI prompts to Set-as-node instead.
            ws = mgr.working_state(runtime, owns_bench=False)
            self.assertFalse(ws["dirty"])
            self.assertTrue(ws["unadopted"])
            self.assertTrue(ws["on_head"])
            self.assertIsNone(ws["applied"]["configuration_id"])
            self.assertEqual(commit["id"], mgr.get_head("main"))
            # A non-owning repo with history can Set-as-node, but not stash/fork.
            caps = ws["capabilities"]
            self.assertTrue(caps["can_set_node"])
            self.assertFalse(caps["can_stash"])
            self.assertFalse(caps["can_fork"])


class BenchOriginTests(unittest.TestCase):
    def test_origin_roundtrip_and_ownership(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            # Unknown origin: every repo is treated as owner (legacy behavior).
            self.assertTrue(repo_owns_bench(tmp, "repo_a"))
            self.assertTrue(repo_owns_bench(tmp, "repo_b"))

            write_bench_origin(tmp, "repo_a", "commit123")
            self.assertEqual(read_bench_origin(tmp)["repo_id"], "repo_a")
            self.assertEqual(read_bench_origin(tmp)["configuration_id"], "commit123")
            self.assertTrue(repo_owns_bench(tmp, "repo_a"))
            self.assertFalse(repo_owns_bench(tmp, "repo_b"))


class ApplyMembershipProjectionTests(unittest.TestCase):
    """Applying a config must take absent table parts off the table (to storage),
    so a soft-checkout preview + return faithfully restores stored parts."""

    def _presence(self, entry):
        return entry["statecontrol"]["tunables"].get("presence")

    def test_absent_breadboard_part_is_stored_on_apply(self) -> None:
        runtime = _runtime(
            {
                "tag_keep": _runtime_entry("tag_keep"),
                "tag_drop": _runtime_entry("tag_drop"),
            }
        )
        # Config only mentions tag_keep -> tag_drop must go to storage.
        apply_configuration_to_components(runtime, _cfg_component("tag_keep"))
        self.assertEqual(self._presence(runtime["components"]["tag_keep"]), PRESENCE_BREADBOARD)
        self.assertEqual(self._presence(runtime["components"]["tag_drop"]), PRESENCE_STORAGE)
        self.assertTrue(
            runtime["components"]["tag_drop"]["statecontrol"]["tunables"]["storage"][
                "in_storage"
            ]
        )

    def test_preview_then_return_restores_stored(self) -> None:
        # Applied node: tag_a stored (absent), tag_b on table.
        runtime = _runtime(
            {
                "tag_a": _runtime_entry("tag_a", PRESENCE_STORAGE, in_storage=True),
                "tag_b": _runtime_entry("tag_b"),
            }
        )
        applied_cfg = _cfg_component("tag_b")

        # Preview a node where tag_a is placed on the breadboard.
        preview_cfg = {
            "components": {
                "tag_a": {
                    "statecontrol": {
                        "tunables": {
                            "presence": PRESENCE_BREADBOARD,
                            "nominal_pose": {"x": 5.0, "y": 5.0, "rotation": 0.0},
                            "storage": {"in_storage": False, "slot": None},
                        }
                    }
                },
                "tag_b": {
                    "statecontrol": {
                        "tunables": {"presence": PRESENCE_BREADBOARD}
                    }
                },
            },
            "holding": {"tag_id": None, "nominal_pose": None},
        }
        apply_configuration_to_components(runtime, preview_cfg)
        self.assertEqual(self._presence(runtime["components"]["tag_a"]), PRESENCE_BREADBOARD)

        # Return to the applied node: tag_a is absent -> back to storage.
        apply_configuration_to_components(runtime, applied_cfg)
        self.assertEqual(self._presence(runtime["components"]["tag_a"]), PRESENCE_STORAGE)


class CrossRepoFlowTests(unittest.TestCase):
    """End-to-end (manager-level) cross-repo bench preservation flow."""

    def test_switch_then_stash_then_checkout_uses_storage_primitives(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            # Repo B has a node with component Z on the table.
            mgr_b = ControlManager(tmp, "repo_b")
            node_b = mgr_b.commit_from_runtime(
                _runtime({"tag_z": _runtime_entry("tag_z", x=10.0, y=20.0)}),
                message="B base (Z)",
            )

            # The physical bench actually holds A's components (X, Y) — repo B did
            # not move it (switching repos never moves the bench). So B does not
            # own the bench: it reads as uncommitted work on top of empty.
            bench = _runtime(
                {
                    "tag_x": _runtime_entry("tag_x", x=1.0, y=1.0),
                    "tag_y": _runtime_entry("tag_y", x=2.0, y=2.0),
                }
            )
            self.assertTrue(mgr_b.runtime_is_dirty(bench, owns_bench=False))

            # Stashing a foreign bench reconciles it back to EMPTY: every part is
            # stored. (This is what the /stash endpoint plans when not owning.)
            stash_plan = mgr_b.plan_runtime_to_configuration(bench, EMPTY_CONFIGURATION)
            self.assertEqual(
                sorted(c["target_id"] for c in stash_plan), ["tag_x", "tag_y"]
            )
            self.assertTrue(
                all(c["action"] == PrimitiveId.STORE_COMPONENT for c in stash_plan)
            )

            # After stash the bench is empty; checking out B's node places Z from
            # storage.
            empty_bench = _runtime({})
            checkout_plan = mgr_b.plan_checkout_from_runtime(empty_bench, node_b["id"])
            place = [
                c for c in checkout_plan if c["action"] == PrimitiveId.PLACE_FROM_STORAGE
            ]
            self.assertEqual([c["target_id"] for c in place], ["tag_z"])


class ControlCapabilitiesTests(unittest.TestCase):
    """The single rule set behind every Git-like toolbar affordance."""

    def test_clean_on_head_with_history(self) -> None:
        caps = _control_capabilities(
            dirty=False, detached=False, has_stash=False, has_commits=True
        )
        self.assertTrue(caps["can_commit"])
        self.assertFalse(caps["commit_recommended"])
        self.assertFalse(caps["can_stash"])
        self.assertFalse(caps["can_pop_stash"])
        self.assertFalse(caps["can_drop_stash"])
        self.assertTrue(caps["can_fork"])
        self.assertTrue(caps["can_checkout_other"])
        self.assertTrue(caps["can_set_node"])

    def test_dirty_on_head(self) -> None:
        caps = _control_capabilities(
            dirty=True, detached=False, has_stash=False, has_commits=True
        )
        # Soft preview / Set as reference stay available while dirty; only hard
        # apply-on-bench remains gated in the checkout endpoint / UI.
        self.assertTrue(caps["can_commit"])
        self.assertTrue(caps["commit_recommended"])
        self.assertTrue(caps["can_stash"])
        self.assertTrue(caps["can_checkout_other"])
        self.assertTrue(caps["can_set_node"])
        self.assertFalse(caps["can_pop_stash"])

    def test_detached_blocks_commit_allows_fork(self) -> None:
        caps = _control_capabilities(
            dirty=False, detached=True, has_stash=False, has_commits=True
        )
        self.assertFalse(caps["can_commit"])
        self.assertFalse(caps["commit_recommended"])
        self.assertTrue(caps["can_fork"])

    def test_stash_present_blocks_new_stash_and_gates_pop(self) -> None:
        # Clean HEAD with a stash: can pop/drop, cannot create another stash.
        caps = _control_capabilities(
            dirty=False, detached=False, has_stash=True, has_commits=True
        )
        self.assertFalse(caps["can_stash"])
        self.assertTrue(caps["can_pop_stash"])
        self.assertTrue(caps["can_drop_stash"])
        # Dirty HEAD with a stash: pop is blocked (must land on a clean HEAD),
        # drop is still allowed, no second stash.
        caps_dirty = _control_capabilities(
            dirty=True, detached=False, has_stash=True, has_commits=True
        )
        self.assertFalse(caps_dirty["can_stash"])
        self.assertFalse(caps_dirty["can_pop_stash"])
        self.assertTrue(caps_dirty["can_drop_stash"])

    def test_fresh_repo_no_commits(self) -> None:
        # Empty bench, no history: committing the first node is recommended,
        # and there is nothing to fork from yet.
        caps = _control_capabilities(
            dirty=False, detached=False, has_stash=False, has_commits=False
        )
        self.assertTrue(caps["can_commit"])
        self.assertTrue(caps["commit_recommended"])
        self.assertFalse(caps["can_fork"])

    def test_unadopted_non_empty_repo_can_set_node_only(self) -> None:
        caps = _control_capabilities(
            dirty=False, detached=False, has_stash=False, has_commits=True,
            unadopted=True,
        )
        self.assertTrue(caps["can_set_node"])
        self.assertTrue(caps["can_checkout_other"])
        self.assertFalse(caps["can_commit"])
        self.assertFalse(caps["can_stash"])
        self.assertFalse(caps["can_fork"])

    def test_unadopted_fresh_repo_commits_root(self) -> None:
        caps = _control_capabilities(
            dirty=False, detached=False, has_stash=False, has_commits=False,
            unadopted=True,
        )
        # No history yet: the only move is to commit the current bench as root.
        self.assertFalse(caps["can_set_node"])
        self.assertTrue(caps["can_commit"])
        self.assertTrue(caps["commit_recommended"])

    def test_working_state_exposes_capabilities(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mgr = ControlManager(tmp, "caps_repo")
            runtime = _runtime({"tag_a": _runtime_entry("tag_a", x=5.0, y=5.0)})
            mgr.commit_from_runtime(runtime, message="base")
            ws = mgr.working_state(runtime, owns_bench=True)
            self.assertIn("capabilities", ws)
            caps = ws["capabilities"]
            self.assertFalse(caps["can_stash"])
            self.assertTrue(caps["can_commit"])
            self.assertTrue(caps["can_fork"])


if __name__ == "__main__":
    unittest.main()
