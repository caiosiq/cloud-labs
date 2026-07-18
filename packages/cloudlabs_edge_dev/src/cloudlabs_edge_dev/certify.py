"""Pre-register certification: doctor + live conformance → machine-readable report."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from cloudlabs_edge_dev import CONTRACT_VERSION, __version__ as KIT_VERSION
from cloudlabs_edge_dev.conformance import Report as ConformanceReport, run_conformance
from cloudlabs_edge_dev.doctor import DoctorReport, run_doctor


@dataclass
class CertifyReport:
    """Combined readiness gate before adding an edge to the coordinator."""

    edge_url: str
    profile: str
    kit_version: str
    contract_version: str
    doctor: DoctorReport
    conformance: ConformanceReport
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    edge_root: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.doctor.ok and self.conformance.ok

    def to_dict(self) -> dict[str, Any]:
        conf_checks = [
            {"name": r.name, "ok": r.ok, "detail": r.detail}
            for r in self.conformance.results
        ]
        return {
            "ok": self.ok,
            "generated_at": self.generated_at,
            "kit_version": self.kit_version,
            "contract_version": self.contract_version,
            "edge_url": self.edge_url,
            "profile": self.profile,
            "edge_root": self.edge_root,
            "doctor": self.doctor.to_dict(),
            "conformance": {
                "ok": self.conformance.ok,
                "base_url": self.conformance.base_url,
                "profile": self.conformance.profile,
                "checks": conf_checks,
            },
            "summary": {
                "doctor_ok": self.doctor.ok,
                "conformance_ok": self.conformance.ok,
                "doctor_warnings": len(self.doctor.warnings),
                "conformance_failures": sum(
                    1 for r in self.conformance.results if not r.ok
                ),
            },
        }

    def print(self, file=sys.stdout) -> None:
        status = "PASS" if self.ok else "FAIL"
        print(
            f"[{status}] edge certify profile={self.profile} url={self.edge_url}",
            file=file,
        )
        print("--- doctor ---", file=file)
        self.doctor.print(file=file)
        print("--- conformance ---", file=file)
        self.conformance.print(file=file)
        if self.ok:
            print(
                "Ready to register this edge with the coordinator "
                "(capabilities + contract probes passed).",
                file=file,
            )
        else:
            print(
                "Not ready: fix FAIL items above before adding edge.base_url "
                "to cloud-labs.",
                file=file,
            )


def run_certify(
    base_url: str,
    *,
    profile: str = "stub",
    edge_path: Optional[Path] = None,
    skip_doctor: bool = False,
) -> CertifyReport:
    if skip_doctor:
        doctor = DoctorReport(
            kit_version=KIT_VERSION,
            contract_version_expected=CONTRACT_VERSION,
        )
        doctor.add(
            "doctor skipped",
            True,
            "--skip-doctor",
            level="info",
        )
    else:
        doctor = run_doctor(edge_path)

    conformance = run_conformance(base_url, profile=profile)
    return CertifyReport(
        edge_url=base_url.rstrip("/"),
        profile=profile,
        kit_version=KIT_VERSION,
        contract_version=CONTRACT_VERSION,
        doctor=doctor,
        conformance=conformance,
        edge_root=doctor.edge_root,
    )


def main_certify(
    base_url: str,
    *,
    profile: str = "stub",
    edge_path: Optional[Path] = None,
    out: Optional[Path] = None,
    skip_doctor: bool = False,
    as_json: bool = False,
) -> int:
    report = run_certify(
        base_url,
        profile=profile,
        edge_path=edge_path,
        skip_doctor=skip_doctor,
    )
    payload = report.to_dict()
    if out is not None:
        out = out.expanduser().resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote certify report to {out}", file=sys.stderr)

    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        report.print()
    return 0 if report.ok else 1
