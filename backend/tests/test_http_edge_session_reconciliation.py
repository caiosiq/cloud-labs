"""HTTP-edge session reconciliation uses Twin store + coordinator checkpoint."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from lab_model.coordinator.backends.lab_view_config import LabViewManifest, LabViewPaths
from lab_model.coordinator.state.lab_state_store import LabStateStore
from lab_model.coordinator.state.session_reconciliation import (
    apply_session_reconciliation_tags_for_runtime,
    offers_dict_for_runtime,
    save_session_checkpoint_for_runtime,
    session_checkpoint_enabled,
)
from lab_model.language.domain.component import new_component_entry
from mock_backend.shared.session_checkpoint import build_checkpoint_document


def _entry(tag_id: str, x: float, y: float, rot: float, *, motor: float | None = None):
    entry = new_component_entry(
        tag_id,
        "OPTICAL_MIRROR",
        presence="breadboard",
        nominal_pose={"x": x, "y": y, "rotation": rot},
        meas_pose={"x": x, "y": y, "rotation": rot},
    )
    if motor is not None:
        entry["statecontrol"]["tunables"]["nominal_motor_positions"] = {"m1": motor}
    return entry


def _paths(root: str) -> LabViewPaths:
    return LabViewPaths(
        root_dir=root,
        layout_json="",
        laser_lines_json=os.path.join(root, "laser_lines.json"),
        component_library_json="",
        active_catalog_json="",
        motor_rotations_json="",
        lab_state_json=os.path.join(root, "lab_state.json"),
        stored_intent_json=os.path.join(root, "stored_intent.json"),
        session_checkpoint_json=os.path.join(root, "session_last_lab_state.json"),
        recipes_dir=os.path.join(root, "recipes"),
        states_dir=os.path.join(root, "states"),
        control_dir=os.path.join(root, "control"),
        camera_captures_dir=os.path.join(root, "camera_captures"),
        table_cam_preview_json=os.path.join(root, "table_cam_preview.json"),
        lab_manifest_json=os.path.join(root, "lab_manifest.json"),
    )


class HttpEdgeSessionReconciliationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = self._tmpdir.name
        self.paths = _paths(self.root)
        os.makedirs(self.root, exist_ok=True)
        self.store = LabStateStore.from_disk("real.test", self.paths.lab_state_json)
        self.manifest = LabViewManifest(
            communicator="real",
            lab_mode="REAL",
            session_checkpoint=True,
            reconciliation_position_mm=8.0,
            reconciliation_yaw_deg=10.0,
        )
        self.rt = SimpleNamespace(
            backend_id="real.test",
            paths=self.paths,
            manifest=self.manifest,
            lab_mode="REAL",
            lab=None,
            lab_state_store=self.store,
        )

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_session_checkpoint_enabled_for_http_manifest(self) -> None:
        self.assertTrue(session_checkpoint_enabled(self.rt))

    def test_save_and_offers_close_poses_differing_tunables(self) -> None:
        # Checkpoint: clean nominal / motor.
        ck_comp = _entry("tag_1", 100.0, 200.0, 45.0, motor=12.5)
        ck_state = {
            "system_status": "IDLE",
            "components": {"tag_1": ck_comp},
        }
        doc = build_checkpoint_document("REAL", ck_state)
        with open(self.paths.session_checkpoint_json, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)

        # Current: noisy measured pose still within 8 mm / 10°, different motor.
        cur_comp = _entry("tag_1", 103.0, 198.0, 47.0, motor=0.0)
        self.store.replace_state(
            {"system_status": "IDLE", "components": {"tag_1": cur_comp}},
            persist=True,
        )

        offers = offers_dict_for_runtime(self.rt)
        self.assertTrue(offers["enabled"])
        self.assertIsNone(offers["skipped_reason"])
        self.assertEqual([o["tag_id"] for o in offers["offers"]], ["tag_1"])

        applied = apply_session_reconciliation_tags_for_runtime(self.rt, ["tag_1"])
        self.assertEqual(applied, ["tag_1"])
        restored = self.store.snapshot()["components"]["tag_1"]
        motors = restored["statecontrol"]["tunables"]["nominal_motor_positions"]
        self.assertEqual(motors.get("m1"), 12.5)

    def test_save_checkpoint_writes_coordinator_path(self) -> None:
        self.store.replace_state(
            {
                "system_status": "IDLE",
                "components": {"tag_1": _entry("tag_1", 1.0, 2.0, 3.0)},
            },
            persist=True,
        )
        path = save_session_checkpoint_for_runtime(self.rt)
        self.assertEqual(path, self.paths.session_checkpoint_json)
        self.assertTrue(os.path.isfile(path))
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        self.assertEqual(doc["lab_mode"], "REAL")
        self.assertIn("tag_1", doc["lab_state"]["components"])

    def test_offers_skip_when_poses_far(self) -> None:
        ck_state = {
            "system_status": "IDLE",
            "components": {"tag_1": _entry("tag_1", 0.0, 0.0, 0.0, motor=1.0)},
        }
        with open(self.paths.session_checkpoint_json, "w", encoding="utf-8") as fh:
            json.dump(build_checkpoint_document("REAL", ck_state), fh)
        self.store.replace_state(
            {
                "system_status": "IDLE",
                "components": {
                    "tag_1": _entry("tag_1", 50.0, 0.0, 0.0, motor=0.0),
                },
            },
            persist=True,
        )
        offers = offers_dict_for_runtime(self.rt)
        self.assertEqual(offers["offers"], [])
        self.assertEqual(offers["debug"]["pose_mismatch_tags"][0]["tag_id"], "tag_1")


class MainHttpEdgeOffersRoutingTests(unittest.TestCase):
    def test_session_reconciliation_no_longer_skips_external_edge(self) -> None:
        import main
        from lab_model.execution.edge.client import EdgeTransport

        fake_rt = SimpleNamespace(
            backend_id="real.default",
            paths=_paths(tempfile.mkdtemp()),
            manifest=LabViewManifest(
                communicator="real",
                lab_mode="REAL",
                session_checkpoint=True,
            ),
            lab_mode="REAL",
            lab=None,
            lab_state_store=None,
        )
        with patch.object(main, "_edge_client_for", return_value=SimpleNamespace(transport=EdgeTransport.HTTP)):
            with patch.object(main, "_runtime_for_active", return_value=fake_rt):
                result = main._session_reconciliation_offers_dict()

        self.assertNotEqual(result.get("skipped_reason"), "external_edge")
        # No store / empty state → lab_unavailable or feature path, not external skip.
        self.assertIn(
            result.get("skipped_reason"),
            {"lab_unavailable", "no_checkpoint", "feature_disabled", None},
        )


if __name__ == "__main__":
    unittest.main()
