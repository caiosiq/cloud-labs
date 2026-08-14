"""Job record types for the Job Manager (Phase C)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

ExecutionMode = Literal["imperative", "compiled_dag", "closed_loop"]
JobStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class JobRecord:
    """Orchestration envelope for compiled and long-running work."""

    job_id: str
    mode: ExecutionMode
    backend_id: str
    status: JobStatus = "queued"
    holder: str = ""
    lease_id: Optional[str] = None
    submitted_at: datetime = field(default_factory=_utcnow)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    snapshot_ref: Optional[str] = None
    spec: Dict[str, Any] = field(default_factory=dict)
    progress: Dict[str, Any] = field(default_factory=dict)
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    cancel_requested: bool = False
    accept_requested: bool = False

    def to_api_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "mode": self.mode,
            "backend_id": self.backend_id,
            "status": self.status,
            "holder": self.holder,
            "lease_id": self.lease_id,
            "submitted_at": _iso(self.submitted_at),
            "started_at": _iso(self.started_at),
            "finished_at": _iso(self.finished_at),
            "snapshot_ref": self.snapshot_ref,
            "spec": self.spec,
            "progress": dict(self.progress),
            "result": self.result,
            "error": self.error,
            "cancel_requested": self.cancel_requested,
            "accept_requested": self.accept_requested,
        }


def job_record_from_submit(
    *,
    job_id: str,
    backend_id: str,
    mode: ExecutionMode,
    holder: str,
    snapshot_ref: Optional[str],
    spec: Dict[str, Any],
) -> JobRecord:
    return JobRecord(
        job_id=job_id,
        mode=mode,
        backend_id=backend_id,
        holder=holder,
        snapshot_ref=snapshot_ref,
        spec=spec,
    )
