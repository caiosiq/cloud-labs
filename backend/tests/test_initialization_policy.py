"""Tests for initialization policy and catalog pins (Phase G)."""
from __future__ import annotations

import os
import tempfile
import unittest

from lab_model.coordinator.catalog.pins_store import CatalogPinsStore
from lab_model.coordinator.jobs.initialization_policy import normalize_initialization_policy
from lab_model.coordinator.jobs.job_manager import validate_submit_spec


class InitializationPolicyTests(unittest.TestCase):
    def test_default_is_force_reconcile(self) -> None:
        self.assertEqual(normalize_initialization_policy(None), "force_reconcile")
        self.assertEqual(normalize_initialization_policy(""), "force_reconcile")
        self.assertEqual(normalize_initialization_policy("bogus"), "force_reconcile")

    def test_accepts_named_policies(self) -> None:
        self.assertEqual(normalize_initialization_policy("strict"), "strict")
        self.assertEqual(normalize_initialization_policy("stash_and_start"), "stash_and_start")

    def test_submit_spec_carries_policy_and_snapshot(self) -> None:
        spec = validate_submit_spec(
            "compiled_dag",
            {
                "steps": [],
                "initialization_policy": "force_reconcile",
                "snapshot": {
                    "repo_id": "laser-cavity",
                    "branch": "main",
                    "commit": "abc123",
                },
                "finalize_checkout": {
                    "repo_id": "laser-cavity",
                    "configuration_id": "abc123",
                },
            },
        )
        self.assertEqual(spec["initialization_policy"], "force_reconcile")
        self.assertEqual(spec["snapshot"]["repo_id"], "laser-cavity")


class CatalogPinsStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = CatalogPinsStore(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_auto_approve_creates_pin(self) -> None:
        record = self.store.submit_publish_request(
            repo_id="laser-cavity",
            configuration_id="deadbeef01",
            branch="main",
            message="golden cavity",
            requested_by="ui:test",
            auto_approve=True,
            approved_by="mock:auto",
        )
        self.assertEqual(record["status"], "approved")
        pin_id = record["catalog_pin_id"]
        pin = self.store.get_pin(pin_id)
        assert pin is not None
        self.assertEqual(pin["repo_id"], "laser-cavity")
        self.assertEqual(pin["configuration_id"], "deadbeef01")

    def test_manual_approve_flow(self) -> None:
        pending = self.store.submit_publish_request(
            repo_id="laser-cavity",
            configuration_id="cafebabe99",
            branch="main",
            message="pending pin",
            requested_by="sdk:test",
            auto_approve=False,
        )
        self.assertEqual(pending["status"], "pending")
        approved = self.store.approve_publish_request(
            pending["request_id"],
            approved_by="owner:lab",
            pin_id="golden-cavity",
        )
        self.assertEqual(approved["status"], "approved")
        pin = self.store.get_pin("golden-cavity")
        assert pin is not None
        self.assertEqual(pin["approved_by"], "owner:lab")

    def test_reject_publish_request(self) -> None:
        pending = self.store.submit_publish_request(
            repo_id="laser-cavity",
            configuration_id="abcd1234",
            branch="main",
            message="reject me",
            requested_by="ui:test",
            auto_approve=False,
        )
        rejected = self.store.reject_publish_request(
            pending["request_id"],
            rejected_by="owner:lab",
            reason="not ready",
        )
        self.assertEqual(rejected["status"], "rejected")
        self.assertEqual(rejected["reject_reason"], "not ready")
        with self.assertRaises(ValueError):
            self.store.approve_publish_request(pending["request_id"], approved_by="owner")


if __name__ == "__main__":
    unittest.main()
