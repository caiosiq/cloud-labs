"""Tests for cloudlabs-edge doctor / certify (no hardware)."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cloudlabs_edge_dev.certify import run_certify
from cloudlabs_edge_dev.conformance import Report as ConformanceReport
from cloudlabs_edge_dev.doctor import run_doctor
from cloudlabs_edge_dev.scaffold import init_edge


class DoctorTests(unittest.TestCase):
    def test_doctor_passes_on_scaffold(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = init_edge(Path(tmp) / "cloudlabs_edge", backend_id="test.lab")
            report = run_doctor(root)
            self.assertTrue(report.ok, report.to_dict())
            self.assertEqual(report.edge_root, str(root.resolve()))
            # NotImplementedError skeleton modules are warnings, not failures.
            warns = [r for r in report.results if r.level == "warn" and not r.ok]
            self.assertTrue(any("adapters implemented" in r.name for r in warns))
            self.assertTrue((root / "dispatch.py").is_file())
            self.assertTrue((root / "adapters" / "motion.py").is_file())
            self.assertTrue((root / "SKELETON.md").is_file())

    def test_doctor_fails_missing_contract_pin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = init_edge(Path(tmp) / "cloudlabs_edge")
            (root / "contract_version.txt").write_text("0.0.0\n", encoding="utf-8")
            report = run_doctor(root)
            self.assertFalse(report.ok)
            pin = next(r for r in report.results if r.name == "contract_version.txt pin")
            self.assertFalse(pin.ok)


class CertifyTests(unittest.TestCase):
    def test_certify_combines_doctor_and_conformance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = init_edge(Path(tmp) / "cloudlabs_edge")
            fake = ConformanceReport(base_url="http://127.0.0.1:8100", profile="stub")
            fake.add("GET /capabilities reachable", True)
            with patch(
                "cloudlabs_edge_dev.certify.run_conformance",
                return_value=fake,
            ):
                report = run_certify(
                    "http://127.0.0.1:8100",
                    profile="stub",
                    edge_path=root,
                )
            self.assertTrue(report.ok)
            payload = report.to_dict()
            self.assertTrue(payload["doctor"]["ok"])
            self.assertTrue(payload["conformance"]["ok"])
            self.assertIn("summary", payload)
            # JSON-serializable
            json.dumps(payload)


if __name__ == "__main__":
    unittest.main()
