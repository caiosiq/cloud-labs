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
        # Telemetry overlays from edge.
        self.assertTrue(merged["components"]["tag_10"]["telemetry"]["teleop"]["active"])
        # Edge-only tag visible read-time.
        self.assertIn("tag_22", merged["components"])

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


if __name__ == "__main__":
    unittest.main()
