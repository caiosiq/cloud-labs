"""In-process Job Manager — queue, status, telemetry (Phase C)."""
from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from .initialization_policy import normalize_initialization_policy
from .post_job_commit import _normalize_on_success
from .models import ExecutionMode, JobRecord, JobStatus, job_record_from_submit
from lab_model.execution.optimization.kernels import validate_kernel_ids

_TERMINAL: frozenset[str] = frozenset({"succeeded", "failed", "cancelled"})


class JobNotFoundError(KeyError):
    """Unknown job id."""


class JobConflictError(Exception):
    """Job cannot be submitted or started in the current state."""


class JobManager:
    """Thread-safe job table with a single active runner per backend."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._by_id: Dict[str, JobRecord] = {}
        self._queue: List[str] = []
        self._active_job_id: Optional[str] = None
        self._runner_scheduled = False

    def active_job_id(self) -> Optional[str]:
        with self._lock:
            return self._active_job_id

    def get(self, job_id: str) -> JobRecord:
        with self._lock:
            record = self._by_id.get(job_id)
            if record is None:
                raise JobNotFoundError(job_id)
            return record

    def list_jobs(
        self,
        *,
        limit: int = 50,
        status: Optional[JobStatus] = None,
    ) -> List[JobRecord]:
        with self._lock:
            rows = list(self._by_id.values())
        rows.sort(key=lambda r: r.submitted_at, reverse=True)
        if status is not None:
            rows = [r for r in rows if r.status == status]
        return rows[: max(1, limit)]

    def submit(
        self,
        *,
        backend_id: str,
        mode: ExecutionMode,
        holder: str,
        spec: Dict[str, Any],
        snapshot_ref: Optional[str] = None,
    ) -> JobRecord:
        holder = (holder or "").strip()
        if not holder:
            holder = f"job:{uuid.uuid4().hex[:10]}"

        job_id = f"job_{uuid.uuid4().hex}"
        if not holder.startswith("job:"):
            holder = f"job:{holder}"

        record = job_record_from_submit(
            job_id=job_id,
            backend_id=backend_id,
            mode=mode,
            holder=holder,
            snapshot_ref=snapshot_ref,
            spec=spec,
        )

        with self._lock:
            self._by_id[job_id] = record
            self._queue.append(job_id)
        return record

    def pop_next_queued(self) -> Optional[JobRecord]:
        with self._lock:
            if self._active_job_id is not None:
                return None
            while self._queue:
                job_id = self._queue.pop(0)
                record = self._by_id.get(job_id)
                if record is None:
                    continue
                if record.status != "queued":
                    continue
                return record
            return None

    def claim_next_queued(self) -> Optional[JobRecord]:
        """Pop the next queued job and mark it running (for an external edge agent)."""
        with self._lock:
            if self._active_job_id is not None:
                return None
            while self._queue:
                job_id = self._queue.pop(0)
                record = self._by_id.get(job_id)
                if record is None or record.status != "queued":
                    continue
                record.status = "running"
                record.started_at = datetime.now(timezone.utc)
                self._active_job_id = job_id
                self._runner_scheduled = True
                return record
            return None

    def mark_running(self, job_id: str, *, lease_id: Optional[str] = None) -> JobRecord:
        with self._lock:
            record = self._require(job_id)
            if record.status != "queued":
                raise JobConflictError(f"job {job_id} is not queued (status={record.status})")
            record.status = "running"
            record.started_at = datetime.now(timezone.utc)
            record.lease_id = lease_id
            self._active_job_id = job_id
            return record

    def update_progress(self, job_id: str, patch: Dict[str, Any]) -> JobRecord:
        with self._lock:
            record = self._require(job_id)
            if record.status != "running":
                return record
            record.progress.update(patch)
            return record

    def complete(
        self,
        job_id: str,
        *,
        status: JobStatus,
        result: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> JobRecord:
        if status not in _TERMINAL:
            raise ValueError(f"complete status must be terminal, got {status!r}")
        with self._lock:
            record = self._require(job_id)
            record.status = status
            record.finished_at = datetime.now(timezone.utc)
            record.result = result
            record.error = error
            if self._active_job_id == job_id:
                self._active_job_id = None
            self._runner_scheduled = False
            return record

    def request_cancel(self, job_id: str) -> JobRecord:
        with self._lock:
            record = self._require(job_id)
            if record.status in _TERMINAL:
                return record
            if record.status == "queued":
                record.status = "cancelled"
                record.finished_at = datetime.now(timezone.utc)
                record.error = "Cancelled while queued"
                if job_id in self._queue:
                    self._queue = [jid for jid in self._queue if jid != job_id]
                return record
            record.cancel_requested = True
            return record

    def is_cancel_requested(self, job_id: str) -> bool:
        with self._lock:
            record = self._by_id.get(job_id)
            return bool(record and record.cancel_requested)

    def note_runner_scheduled(self, scheduled: bool) -> None:
        with self._lock:
            self._runner_scheduled = scheduled

    def runner_should_start(self) -> bool:
        with self._lock:
            return not self._runner_scheduled and self._active_job_id is None and bool(self._queue)

    def queued_count(self) -> int:
        with self._lock:
            return sum(
                1
                for jid in self._queue
                if (rec := self._by_id.get(jid)) and rec.status == "queued"
            )

    def fail_open_work(self, *, error: str) -> List[JobRecord]:
        """Fail every queued/running job (edge disconnect fail-closed)."""
        error = (error or "edge disconnected").strip() or "edge disconnected"
        failed: List[JobRecord] = []
        with self._lock:
            targets = [
                rec
                for rec in self._by_id.values()
                if rec.status in ("queued", "running")
            ]
            now = datetime.now(timezone.utc)
            for record in targets:
                record.status = "failed"
                record.finished_at = now
                record.error = error
                record.cancel_requested = True
                failed.append(record)
            self._queue = []
            self._active_job_id = None
            self._runner_scheduled = False
        return failed

    def _require(self, job_id: str) -> JobRecord:
        record = self._by_id.get(job_id)
        if record is None:
            raise JobNotFoundError(job_id)
        return record


def parse_snapshot_ref(snapshot: Any) -> Optional[str]:
    if not isinstance(snapshot, dict):
        return None
    repo = str(snapshot.get("repo_id") or snapshot.get("repo") or "").strip()
    branch = str(snapshot.get("branch") or "main").strip()
    commit = str(snapshot.get("commit") or snapshot.get("configuration_id") or "").strip()
    if repo and commit:
        return f"{repo}@{branch}:{commit}"
    if repo:
        return f"{repo}@{branch}"
    return None


def validate_submit_spec(
    mode: ExecutionMode,
    body: Dict[str, Any],
    *,
    known_session_ids: Optional[set] = None,
) -> Dict[str, Any]:
    """Normalize and validate a job submit envelope."""
    init_policy = normalize_initialization_policy(body.get("initialization_policy"))
    snapshot = body.get("snapshot") if isinstance(body.get("snapshot"), dict) else None
    on_success = body.get("on_success") if isinstance(body.get("on_success"), dict) else None

    # Collect session kernel ids from kernels[] + objective terms for allowlisting.
    session_allow = set(known_session_ids or [])
    raw_kernels = body.get("kernels") if isinstance(body.get("kernels"), list) else []
    for kid in raw_kernels:
        s = str(kid).strip()
        if s.startswith("session."):
            session_allow.add(s)
    command_preview = body.get("command") if isinstance(body.get("command"), dict) else {}
    params_preview = command_preview.get("parameters") if isinstance(command_preview, dict) else {}
    if isinstance(params_preview, dict):
        for kid in params_preview.get("kernels") or []:
            s = str(kid).strip()
            if s.startswith("session."):
                session_allow.add(s)
        obj = params_preview.get("objective")
        if isinstance(obj, dict):
            for term in obj.get("terms") or []:
                if not isinstance(term, dict):
                    continue
                src = term.get("source") or {}
                if isinstance(src, dict):
                    kid = str(src.get("kernel_id") or "").strip()
                    if kid.startswith("session."):
                        session_allow.add(kid)

    kernels = validate_kernel_ids(
        body.get("kernels"),
        known_session_ids=session_allow,
        allow_unknown_session=bool(session_allow),
    )
    if on_success is not None:
        _normalize_on_success({"on_success": on_success})

    if mode == "closed_loop":
        command = body.get("command")
        if not isinstance(command, dict):
            raise ValueError("closed_loop jobs require a command object")
        action = str(command.get("action") or "").strip().upper()
        if action != "OPTIMIZE":
            raise ValueError("closed_loop Phase C supports command.action=OPTIMIZE only")
        if not str(command.get("target_id") or "").strip():
            raise ValueError("command.target_id is required")
        params = command.get("parameters")
        if not isinstance(params, dict):
            raise ValueError("command.parameters is required")
        if params.get("mode") != "ensemble":
            raise ValueError(
                "closed_loop OPTIMIZE requires parameters.mode=ensemble "
                "(legacy_strategy / NEWTON|COBYLA via POST /api/command is deprecated; "
                "use SDK run_optimize/run_cobyla or Twin Alignment session)"
            )
        # Also validate kernels nested in parameters
        if params.get("kernels") is not None:
            params = dict(params)
            params["kernels"] = validate_kernel_ids(
                params.get("kernels"),
                known_session_ids=session_allow,
                allow_unknown_session=bool(session_allow),
            )
            command = dict(command)
            command["parameters"] = params
        spec: Dict[str, Any] = {
            "command": command,
            "initialization_policy": init_policy,
        }
        if snapshot:
            spec["snapshot"] = snapshot
        if on_success:
            spec["on_success"] = on_success
        if kernels:
            spec["kernels"] = kernels
        if session_allow:
            spec["session_kernel_ids"] = sorted(session_allow)
        return spec

    if mode == "compiled_dag":
        steps = body.get("steps")
        finalize = body.get("finalize_checkout")
        if not isinstance(steps, list):
            raise ValueError("compiled_dag jobs require a steps list")
        if not steps and finalize is None and snapshot is None:
            raise ValueError("compiled_dag jobs require steps, finalize_checkout, or snapshot")
        spec = {
            "steps": steps,
            "initialization_policy": init_policy,
        }
        if snapshot:
            spec["snapshot"] = snapshot
        if on_success:
            spec["on_success"] = on_success
        if finalize is not None:
            if not isinstance(finalize, dict):
                raise ValueError("finalize_checkout must be an object")
            repo = str(finalize.get("repo_id") or "").strip()
            commit = str(finalize.get("configuration_id") or "").strip()
            if not repo or not commit:
                raise ValueError("finalize_checkout requires repo_id and configuration_id")
            spec["finalize_checkout"] = {
                "repo_id": repo,
                "configuration_id": commit,
                "branch": str(finalize.get("branch") or "main").strip() or "main",
            }
        if kernels:
            spec["kernels"] = kernels
        return spec

    raise ValueError(f"unsupported job mode: {mode!r}")
