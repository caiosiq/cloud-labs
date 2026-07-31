"""Tests for catalog hash and checkout compatibility reports."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from lab_model.coordinator.backends.lab_view_config import bootstrap_lab_view
from lab_model.coordinator.catalog.catalog_hash import compute_active_catalog_hash
from lab_model.coordinator.state.checkout_compatibility import build_checkout_compatibility_report
from lab_model.coordinator.state.control_manager import ControlManager
from lab_model.coordinator.state.projections import extract_configuration

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_MOCK_LAB_VIEW = _PROJECT_ROOT / "mock_backend" / "lab_view"
_LAB_STATE = _MOCK_LAB_VIEW / "lab_state.json"


class CheckoutCompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ["LAB_VIEW_PATH"] = str(_MOCK_LAB_VIEW)
        bootstrap_lab_view(str(_PROJECT_ROOT))
        with open(_LAB_STATE, "r", encoding="utf-8") as handle:
            cls.fixture_runtime = json.load(handle)

    def test_catalog_hash_stable_for_same_tags(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lab_view = Path(tmp) / "lab_view"
            shutil.copytree(_MOCK_LAB_VIEW, lab_view)
            os.environ["LAB_VIEW_PATH"] = str(lab_view)
            bootstrap_lab_view(str(_PROJECT_ROOT))
            first = compute_active_catalog_hash()
            second = compute_active_catalog_hash()
            self.assertEqual(first, second)
            self.assertEqual(len(first), 64)

    def test_commit_stores_catalog_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lab_view = Path(tmp) / "lab_view"
            shutil.copytree(_MOCK_LAB_VIEW, lab_view)
            os.environ["LAB_VIEW_PATH"] = str(lab_view)
            bootstrap_lab_view(str(_PROJECT_ROOT))
            catalog_hash = compute_active_catalog_hash()
            control_root = Path(tmp) / "control"
            mgr = ControlManager(str(control_root), "test")
            commit = mgr.commit_from_runtime(
                self.fixture_runtime,
                message="with catalog",
                catalog_hash=catalog_hash,
            )
            self.assertEqual(commit.get("catalog_hash"), catalog_hash)

    def test_missing_in_runtime_reported(self) -> None:
        configuration = extract_configuration(self.fixture_runtime)
        configuration["components"]["tag_missing"] = {
            "id": "tag_missing",
            "type": "OPTICAL_MIRROR",
            "statecontrol": {"tunables": {"presence": "breadboard"}},
        }
        runtime = json.loads(json.dumps(self.fixture_runtime))
        runtime["components"].pop("tag_22", None)

        report = build_checkout_compatibility_report(
            configuration=configuration,
            runtime=runtime,
            commit_catalog_hash="aaa",
            current_catalog_hash="bbb",
            catalog_tag_ids=["tag_9", "tag_missing"],
            library_tag_ids=["tag_9", "tag_missing", "tag_22"],
        )
        self.assertFalse(report["ready"])
        self.assertIn("tag_missing", report["missing_in_runtime"])
        kinds = {issue["kind"] for issue in report["issues"]}
        self.assertIn("missing_in_runtime", kinds)
        self.assertIn("catalog_hash_mismatch", kinds)

    def test_ready_when_runtime_covers_configuration(self) -> None:
        configuration = extract_configuration(self.fixture_runtime)
        report = build_checkout_compatibility_report(
            configuration=configuration,
            runtime=self.fixture_runtime,
            commit_catalog_hash="same",
            current_catalog_hash="same",
            catalog_tag_ids=list(configuration["components"].keys()),
            library_tag_ids=list(configuration["components"].keys()),
        )
        self.assertTrue(report["ready"])


if __name__ == "__main__":
    unittest.main()
