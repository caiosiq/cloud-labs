"""Exclusive backend session leases for imperative SDK and batch jobs.

See ``docs/EXECUTION_MODES.md`` §4 for the normative contract.
"""
from __future__ import annotations

import os
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Literal, Optional

ExecutionMode = Literal["imperative", "compiled_dag", "closed_loop"]

_DEFAULT_TTL_BY_MODE: Dict[str, int] = {
    "imperative": 600,
    "compiled_dag": 3600,
    "closed_loop": 3600,
}


class LeaseError(Exception):
    """Base lease failure."""


class LeaseConflictError(LeaseError):
    """Another holder already owns the backend."""

    def __init__(self, backend_id: str, holder: str) -> None:
        self.backend_id = backend_id
        self.holder = holder
        super().__init__(f"backend {backend_id!r} is locked by {holder!r}")


class LeaseNotFoundError(LeaseError):
    """Unknown or already-released lease id."""

    def __init__(self, lease_id: str) -> None:
        self.lease_id = lease_id
        super().__init__(f"lease {lease_id!r} not found or already released")


class LeaseExpiredError(LeaseError):
    """Lease TTL elapsed without heartbeat."""

    def __init__(self, lease_id: str) -> None:
        self.lease_id = lease_id
        super().__init__(f"lease {lease_id!r} has expired")


@dataclass
class SessionLeaseRecord:
    lease_id: str
    backend_id: str
    holder: str
    mode: ExecutionMode
    issued_at: datetime
    expires_at: datetime
    snapshot_ref: Optional[str] = None

    def is_expired(self, *, now: Optional[datetime] = None) -> bool:
        ref = now or _utcnow()
        return ref >= self.expires_at

    def to_api_dict(self) -> Dict[str, Any]:
        return lease_record_to_api_dict(self)


def lease_record_to_api_dict(record: SessionLeaseRecord) -> Dict[str, Any]:
    return {
        "lease_id": record.lease_id,
        "backend_id": record.backend_id,
        "holder": record.holder,
        "mode": record.mode,
        "issued_at": _iso(record.issued_at),
        "expires_at": _iso(record.expires_at),
        "snapshot_ref": record.snapshot_ref,
    }


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def active_backend_id(*, communicator_id: str, lab_mode: str) -> str:
    """Stable ``BackendId`` for the running FastAPI process."""
    bench = (os.environ.get("CLOUDLABS_BENCH_ID") or "default").strip() or "default"
    mode = (lab_mode or "").strip().upper()
    if mode == "MOCK" or (communicator_id or "").strip().lower() == "mock":
        prefix = "mock"
    elif mode == "REAL" or (communicator_id or "").strip().lower() == "real":
        prefix = "real"
    else:
        prefix = (communicator_id or "lab").strip().lower() or "lab"
    return f"{prefix}.{bench}"


class SessionLeaseManager:
    """Thread-safe in-process lease table (one active lease per backend)."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._by_backend: Dict[str, SessionLeaseRecord] = {}
        self._by_id: Dict[str, SessionLeaseRecord] = {}

    def active_lease(self, backend_id: str) -> Optional[SessionLeaseRecord]:
        with self._lock:
            self._sweep_expired_locked()
            return self._by_backend.get(backend_id)

    def get(self, lease_id: str) -> Optional[SessionLeaseRecord]:
        with self._lock:
            self._sweep_expired_locked()
            return self._by_id.get(lease_id)

    def acquire(
        self,
        *,
        backend_id: str,
        holder: str,
        mode: ExecutionMode = "imperative",
        snapshot_ref: Optional[str] = None,
        ttl_seconds: Optional[int] = None,
    ) -> SessionLeaseRecord:
        holder = (holder or "").strip()
        if not holder:
            raise ValueError("holder is required")

        ttl = int(ttl_seconds or _DEFAULT_TTL_BY_MODE.get(mode, 600))
        if ttl <= 0:
            raise ValueError("ttl_seconds must be positive")

        now = _utcnow()
        record = SessionLeaseRecord(
            lease_id=f"lease_{uuid.uuid4().hex}",
            backend_id=backend_id,
            holder=holder,
            mode=mode,
            issued_at=now,
            expires_at=now + timedelta(seconds=ttl),
            snapshot_ref=snapshot_ref,
        )

        with self._lock:
            self._sweep_expired_locked()
            existing = self._by_backend.get(backend_id)
            if existing is not None and not existing.is_expired(now=now):
                raise LeaseConflictError(backend_id, existing.holder)
            self._by_backend[backend_id] = record
            self._by_id[record.lease_id] = record
            return record

    def heartbeat(self, lease_id: str, *, extend_seconds: Optional[int] = None) -> SessionLeaseRecord:
        with self._lock:
            self._sweep_expired_locked()
            record = self._by_id.get(lease_id)
            if record is None:
                raise LeaseNotFoundError(lease_id)
            if record.is_expired():
                self._drop_locked(record)
                raise LeaseExpiredError(lease_id)

            extend = int(extend_seconds or _DEFAULT_TTL_BY_MODE.get(record.mode, 600))
            record.expires_at = _utcnow() + timedelta(seconds=extend)
            return record

    def release(self, lease_id: str) -> Optional[SessionLeaseRecord]:
        with self._lock:
            record = self._by_id.get(lease_id)
            if record is None:
                return None
            self._drop_locked(record)
            return record

    def validate_command_lease(
        self,
        *,
        backend_id: str,
        lease_id: Optional[str],
        require_when_locked: bool = True,
    ) -> Optional[SessionLeaseRecord]:
        """Return the active lease when the command may proceed.

        Raises ``LeaseConflictError`` when another holder owns the backend and
        the command does not present a matching ``lease_id``.
        """
        with self._lock:
            self._sweep_expired_locked()
            active = self._by_backend.get(backend_id)
            if active is None:
                if lease_id:
                    known = self._by_id.get(lease_id)
                    if known is None:
                        raise LeaseNotFoundError(lease_id)
                    raise LeaseExpiredError(lease_id)
                return None

            if active.is_expired():
                self._drop_locked(active)
                if lease_id and lease_id == active.lease_id:
                    raise LeaseExpiredError(lease_id)
                return None

            if lease_id is None:
                if require_when_locked:
                    raise LeaseConflictError(backend_id, active.holder)
                return active

            if lease_id != active.lease_id:
                raise LeaseConflictError(backend_id, active.holder)

            return active

    def _drop_locked(self, record: SessionLeaseRecord) -> None:
        self._by_id.pop(record.lease_id, None)
        current = self._by_backend.get(record.backend_id)
        if current is not None and current.lease_id == record.lease_id:
            self._by_backend.pop(record.backend_id, None)

    def _sweep_expired_locked(self) -> None:
        now = _utcnow()
        expired = [
            rec
            for rec in list(self._by_id.values())
            if rec.is_expired(now=now)
        ]
        for rec in expired:
            self._drop_locked(rec)
