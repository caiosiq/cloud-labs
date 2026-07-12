"""Per-backend job managers and runners."""
from __future__ import annotations

import threading
from typing import Dict, Optional

from lab_model.jobs.job_manager import JobManager, JobNotFoundError


class JobManagerHub:
    """One job queue + runner slot per backend_id."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._by_backend: Dict[str, JobManager] = {}

    def for_backend(self, backend_id: str) -> JobManager:
        key = backend_id.strip()
        with self._lock:
            mgr = self._by_backend.get(key)
            if mgr is None:
                mgr = JobManager()
                self._by_backend[key] = mgr
            return mgr

    def active_job_id(self, backend_id: str) -> Optional[str]:
        return self.for_backend(backend_id).active_job_id()

    def find_job(self, job_id: str) -> Optional[tuple[str, JobManager]]:
        with self._lock:
            for backend_id, mgr in self._by_backend.items():
                try:
                    mgr.get(job_id)
                except JobNotFoundError:
                    continue
                return backend_id, mgr
        return None

    def list_all_jobs(self, *, limit: int = 50, status: Optional[str] = None):
        rows = []
        with self._lock:
            for mgr in self._by_backend.values():
                rows.extend(mgr.list_jobs(limit=limit, status=status))  # type: ignore[arg-type]
        rows.sort(key=lambda r: r.submitted_at, reverse=True)
        return rows[: max(1, limit)]

    def queued_count(self, backend_id: str) -> int:
        return self.for_backend(backend_id).queued_count()


__all__ = ["JobManagerHub"]
