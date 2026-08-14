"""Phase 2: per-backend LabStateStore + Twin merge policy."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from lab_model.coordinator.state.lab_state_store import LabStateStore
from lab_model.coordinator.state.merge_lab_state import merge_lab_state_for_twin
from lab_model.coordinator.state.runtime_manager import MutationKind, RuntimeManager
from lab_model.language.domain.holding import SYSTEM_STATUS_HOLDING, SYSTEM_STATUS_IDLE


class MergeLabStateTests(unittest.TestCase):
    def test_coordinator_wins_fsm_edge_wins_runtime_sync(self) -> None:
        working = {
            "system_status": SYSTEM_STATUS_HOLDING,
            "holding": {"tag_id": "tag_10", "requires_operator_confirm": False},
            "components": {
                "tag_10": {
                    "statecontrol": {
                        "tunables": {
                            "presence": "breadboard",
                            "nominal_pose": {"x": 1.0, "y": 2.0, "rotation": 0.0},
                        }
                    },
                    "telemetry": {"teleop": {"active": False}},
                }
            },
        }
        edge = {
            "system_status": SYSTEM_STATUS_IDLE,
            "holding": None,
            "runtime_sync": {"status": "ready", "errors": []},
            "components": {
                "tag_10": {
                    "statecontrol": {
                        "tunables": {
                            "presence": "breadboard",
                            "nominal_pose": {"x": 999.0, "y": 999.0, "rotation": 0.0},
                        }
                    },
                    "telemetry": {"teleop": {"active": True, "mode": "jog"}},
                },
                "tag_22": {
                    "statecontrol": {"tunables": {"presence": "breadboard"}},
                    "telemetry": {"live_feed": {"stream": {"live": True}}},
                },
            },
        }
        merged = merge_lab_state_for_twin(working, edge, backend_id="real.default")
        self.assertEqual(merged["system_status"], SYSTEM_STATUS_HOLDING)
        self.assertEqual(merged["holding"]["tag_id"], "tag_10")
        self.assertEqual(merged["runtime_sync"]["status"], "ready")
        # Commanded pose stays coordinator's.
        pose = merged["components"]["tag_10"]["statecontrol"]["tunables"]["nominal_pose"]
        self.assertEqual(pose["x"], 1.0)
        # Telemetry: coordinator owns teleop + live_feed sessions; edge-only
        # tags still appear at read time.
        self.assertFalse(
            merged["components"]["tag_10"]["telemetry"]["teleop"]["active"]
        )
        self.assertIn("tag_22", merged["components"])
        self.assertTrue(
            merged["components"]["tag_22"]["telemetry"]["live_feed"]["stream"]["live"]
        )

    def test_coordinator_keeps_live_feed_over_edge_idle(self) -> None:
        working = {
            "system_status": SYSTEM_STATUS_IDLE,
            "components": {
                "tag_22": {
                    "telemetry": {
                        "teleop": {"active": False},
                        "live_feed": {"stream": {"live": True, "connected": True}},
                    }
                }
            },
        }
        edge = {
            "runtime_sync": {"status": "ready"},
            "components": {
                "tag_22": {
                    "telemetry": {
                        "teleop": {"active": False},
                        "live_feed": {"stream": {"live": False, "connected": False}},
                    }
                }
            },
        }
        merged = merge_lab_state_for_twin(working, edge, backend_id="real.default")
        self.assertTrue(
            merged["components"]["tag_22"]["telemetry"]["live_feed"]["stream"]["live"]
        )

    def test_no_edge_returns_working_copy(self) -> None:
        working = {"system_status": SYSTEM_STATUS_IDLE, "components": {}}
        self.assertEqual(
            merge_lab_state_for_twin(working, None, backend_id="mock.default"),
            working,
        )


class LabStateStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="lab_state_store_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.path = str(self.tmp / "lab_state.json")

    def test_seed_from_edge_once_and_persist(self) -> None:
        store = LabStateStore("real.default", self.path)
        edge = {
            "system_status": SYSTEM_STATUS_IDLE,
            "holding": None,
            "runtime_sync": {"status": "ready"},
            "components": {
                "tag_10": {
                    "statecontrol": {"tunables": {"presence": "breadboard"}},
                }
            },
        }
        self.assertTrue(store.ensure_seeded_from_edge(edge))
        self.assertTrue(os.path.isfile(self.path))
        with open(self.path, "r", encoding="utf-8") as fh:
            on_disk = json.load(fh)
        self.assertIn("tag_10", on_disk["components"])
        self.assertNotIn("runtime_sync", on_disk)

        # Coordinator mutate (preview of Phase 3) must survive a second edge seed attempt.
        def _hold(state: dict) -> None:
            state["system_status"] = SYSTEM_STATUS_HOLDING
            state["holding"] = {"tag_id": "tag_10"}

        store.mutate(_hold, kind=MutationKind.PRIMITIVE_COMMIT, source="test")
        self.assertFalse(store.ensure_seeded_from_edge(edge))
        snap = store.snapshot()
        self.assertEqual(snap["system_status"], SYSTEM_STATUS_HOLDING)

        # Merge still shows HOLDING while edge claims IDLE.
        merged = merge_lab_state_for_twin(snap, edge, backend_id="real.default")
        self.assertEqual(merged["system_status"], SYSTEM_STATUS_HOLDING)
        self.assertEqual(merged["runtime_sync"]["status"], "ready")

    def test_alias_host_does_not_double_write(self) -> None:
        runtime = RuntimeManager(
            {
                "system_status": SYSTEM_STATUS_IDLE,
                "components": {"tag_1": {"id": "tag_1"}},
                "holding": None,
            }
        )

        class _Host:
            _lab_runtime = runtime

            def get_lab_state(self):
                return {
                    **runtime.snapshot_raw(),
                    "runtime_sync": {"status": "ready"},
                }

        host = _Host()
        store = LabStateStore.alias_host("mock.default", self.path, host)
        self.assertFalse(store.ensure_seeded_from_edge({"components": {"x": {}}}))
        snap = store.snapshot()
        self.assertEqual(snap["runtime_sync"]["status"], "ready")
        store.persist()
        self.assertFalse(os.path.isfile(self.path))

    def test_reconcile_prunes_tags_absent_from_edge_inventory(self) -> None:
        store = LabStateStore("real.default", self.path)
        store.replace_state(
            {
                "system_status": SYSTEM_STATUS_IDLE,
                "components": {
                    "tag_10": {
                        "id": "tag_10",
                        "statecontrol": {
                            "tunables": {
                                "nominal_pose": {"x": 1.0, "y": 2.0, "rotation": 0.0}
                            }
                        },
                    },
                    "tag_8": {
                        "id": "tag_8",
                        "statecontrol": {
                            "tunables": {
                                "nominal_pose": {"x": 3.0, "y": 4.0, "rotation": 0.0}
                            }
                        },
                    },
                },
            },
            kind=MutationKind.BOOT_HYDRATE,
            source="test_seed",
        )
        edge = {
            "components": {
                "tag_8": {
                    "id": "tag_8",
                    "statecontrol": {
                        "tunables": {
                            "nominal_pose": {"x": 30.0, "y": 40.0, "rotation": 0.0}
                        }
                    },
                },
                "tag_22": {"id": "tag_22", "statecontrol": {"tunables": {}}},
            }
        }
        self.assertTrue(store.reconcile_membership_from_edge(edge))
        snap = store.snapshot()
        self.assertNotIn("tag_10", snap["components"])
        self.assertIn("tag_8", snap["components"])
        # Existing shared tag keeps coordinator commanded pose.
        self.assertEqual(
            snap["components"]["tag_8"]["statecontrol"]["tunables"]["nominal_pose"]["x"],
            3.0,
        )
        self.assertIn("tag_22", snap["components"])
        # Second pass is a no-op.
        self.assertFalse(store.reconcile_membership_from_edge(edge))


class LaserLinesBundleSeedTests(unittest.TestCase):
    def test_empty_placeholder_upgraded_from_bundle(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="laser_seed_"))
        self.addCleanup(shutil.rmtree, tmp, True)
        state_path = tmp / "lab_state.json"
        lines_path = tmp / "laser_lines.json"
        state_path.write_text(
            json.dumps(
                {
                    "system_status": "IDLE",
                    "components": {},
                    "laser_lines": {"snap_line_id": None, "lines": []},
                }
            ),
            encoding="utf-8",
        )
        lines_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "snap_line_id": "diode",
                    "lines": [
                        {
                            "id": "diode",
                            "enabled": True,
                            "p1": {"x": 1.0, "y": -1.0},
                            "p2": {"x": 1.0, "y": 1.0},
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        store = LabStateStore.from_disk("real.default", str(state_path))
        self.assertTrue(store.ensure_laser_lines_from_bundle(str(lines_path)))
        ll = store.snapshot()["laser_lines"]
        self.assertEqual(ll["snap_line_id"], "diode")
        self.assertEqual(ll["lines"][0]["id"], "diode")
        # Second call is a no-op once lines exist.
        self.assertFalse(store.ensure_laser_lines_from_bundle(str(lines_path)))


if __name__ == "__main__":
    unittest.main()
