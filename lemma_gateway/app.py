"""FastAPI application for the simulation-only Lemma gateway."""

from __future__ import annotations

import hmac
import os
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .command_language import CommandSyntaxError, ParsedLine, parse_console_line
from .service import (
    CoordinatorClient,
    GatewayError,
    JobCancelled,
    MUTATION_KINDS,
    READ_KINDS,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class JobRecord:
    job_id: str
    command: str
    request_id: str | None
    parsed: ParsedLine
    status: str = "queued"
    created_at: str = field(default_factory=_utc_now)
    started_at: str | None = None
    finished_at: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)

    def public(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "request_id": self.request_id,
            "command": self.command,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "result": self.result,
            "error": self.error,
        }


class JobManager:
    """Serialize simulation mutations and retain concise in-memory status."""

    def __init__(self, controller: CoordinatorClient) -> None:
        self.controller = controller
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="lemma-job")
        self._lock = threading.RLock()
        self._jobs: dict[str, JobRecord] = {}
        self._futures: dict[str, Future[Any]] = {}
        self._request_ids: dict[str, str] = {}

    def submit(
        self,
        *,
        command: str,
        parsed: ParsedLine,
        request_id: str | None,
    ) -> tuple[dict[str, Any], bool]:
        with self._lock:
            if request_id and request_id in self._request_ids:
                existing = self._jobs[self._request_ids[request_id]]
                if existing.command != command:
                    raise GatewayError(
                        "request_id was already used for a different command",
                        status_code=409,
                    )
                return existing.public(), False
            job_id = uuid.uuid4().hex
            record = JobRecord(
                job_id=job_id,
                command=command,
                request_id=request_id,
                parsed=parsed,
            )
            self._jobs[job_id] = record
            if request_id:
                self._request_ids[request_id] = job_id
            self._futures[job_id] = self._executor.submit(self._run, job_id)
            return record.public(), True

    def _run(self, job_id: str) -> None:
        with self._lock:
            record = self._jobs[job_id]
            if record.cancel_event.is_set():
                record.status = "cancelled"
                record.finished_at = _utc_now()
                return
            record.status = "running"
            record.started_at = _utc_now()
        try:
            result = self.controller.execute_mutation(record.parsed, record.cancel_event)
        except JobCancelled as exc:
            with self._lock:
                record.status = "cancelled"
                record.error = str(exc)
                record.finished_at = _utc_now()
        except GatewayError as exc:
            with self._lock:
                record.status = "failed"
                record.error = str(exc)
                record.finished_at = _utc_now()
        except Exception as exc:  # noqa: BLE001 - job boundary must retain failure
            with self._lock:
                record.status = "failed"
                record.error = f"unexpected gateway failure: {exc}"
                record.finished_at = _utc_now()
        else:
            with self._lock:
                record.status = "succeeded"
                record.result = result
                record.finished_at = _utc_now()

    def get(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            record = self._jobs.get(job_id)
            if record is None:
                raise GatewayError(f"job not found: {job_id}", status_code=404)
            return record.public()

    def cancel(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            record = self._jobs.get(job_id)
            if record is None:
                raise GatewayError(f"job not found: {job_id}", status_code=404)
            if record.status in {"succeeded", "failed", "cancelled"}:
                return record.public()
            record.cancel_event.set()
            future = self._futures.get(job_id)
            if future is not None and future.cancel():
                record.status = "cancelled"
                record.finished_at = _utc_now()
            elif record.status == "running":
                record.status = "cancel_requested"
            return record.public()

    def cancel_all(self) -> list[dict[str, Any]]:
        with self._lock:
            ids = [
                job_id
                for job_id, record in self._jobs.items()
                if record.status not in {"succeeded", "failed", "cancelled"}
            ]
        return [self.cancel(job_id) for job_id in ids]

    def close(self) -> None:
        self.cancel_all()
        self._executor.shutdown(wait=False, cancel_futures=True)
        self.controller.close()


class ConsoleRequest(BaseModel):
    command: str = Field(min_length=1, max_length=65536)
    request_id: str | None = Field(default=None, min_length=1, max_length=128)


class PresetWriteRequest(BaseModel):
    document: dict[str, Any]
    overwrite: bool = False


class GatewayRuntime:
    def __init__(self, controller: CoordinatorClient) -> None:
        self.controller = controller
        self.jobs = JobManager(controller)

    def close(self) -> None:
        self.jobs.close()


def create_app(controller: CoordinatorClient | None = None) -> FastAPI:
    configured_controller = controller

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        active_controller = configured_controller or CoordinatorClient(
            base_url=os.getenv(
                "CLOUDLABS_LEMMA_COORDINATOR_URL", "http://127.0.0.1:8000"
            ),
            timeout_s=float(os.getenv("CLOUDLABS_LEMMA_HTTP_TIMEOUT_S", "15")),
            operation_timeout_s=float(
                os.getenv("CLOUDLABS_LEMMA_OPERATION_TIMEOUT_S", "900")
            ),
        )
        app.state.runtime = GatewayRuntime(active_controller)
        try:
            yield
        finally:
            app.state.runtime.close()

    application = FastAPI(
        title="Cloud Labs Lemma Gateway",
        version="1.0.0",
        description=(
            "Localhost-only, simulation-only gateway for the Cloud Labs Command Console. "
            "Mutating commands return a job_id and execute serially."
        ),
        lifespan=lifespan,
    )

    async def authorize(request: Request) -> None:
        expected = os.getenv("CLOUDLABS_LEMMA_API_TOKEN", "").strip()
        if not expected:
            return
        supplied = request.headers.get("Authorization", "")
        prefix = "Bearer "
        candidate = supplied[len(prefix) :] if supplied.startswith(prefix) else ""
        if not candidate or not hmac.compare_digest(candidate, expected):
            raise HTTPException(status_code=401, detail="invalid gateway bearer token")

    def runtime(request: Request) -> GatewayRuntime:
        return request.app.state.runtime

    @application.exception_handler(GatewayError)
    async def gateway_error_handler(_request: Request, exc: GatewayError):
        return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})

    @application.get("/v1/health", dependencies=[Depends(authorize)])
    async def health(rt: GatewayRuntime = Depends(runtime)):
        return rt.controller.health()

    @application.get("/v1/capabilities", dependencies=[Depends(authorize)])
    async def capabilities(
        detail: bool = Query(default=False),
        rt: GatewayRuntime = Depends(runtime),
    ):
        return rt.controller.capabilities(detailed=detail)

    @application.get("/v1/state", dependencies=[Depends(authorize)])
    async def state_view(
        component: str | None = Query(default=None),
        raw: bool = Query(default=False),
        rt: GatewayRuntime = Depends(runtime),
    ):
        if raw:
            return rt.controller.raw_state()
        tag_id = rt.controller.resolve_component(component) if component else None
        return rt.controller.compact_state(tag_id=tag_id)

    @application.get("/v1/presets/{preset_name}", dependencies=[Depends(authorize)])
    async def show_preset(
        preset_name: str,
        rt: GatewayRuntime = Depends(runtime),
    ):
        return rt.controller.show_preset(preset_name)

    @application.put("/v1/presets/{preset_name}", dependencies=[Depends(authorize)])
    async def write_preset(
        preset_name: str,
        body: PresetWriteRequest,
        rt: GatewayRuntime = Depends(runtime),
    ):
        return rt.controller.write_preset(
            preset_name,
            body.document,
            overwrite=body.overwrite,
        )

    @application.post("/v1/console", dependencies=[Depends(authorize)])
    async def console(
        body: ConsoleRequest,
        rt: GatewayRuntime = Depends(runtime),
    ):
        try:
            parsed = parse_console_line(body.command)
        except CommandSyntaxError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if parsed.kind in READ_KINDS:
            return {
                "mode": "immediate",
                "command": body.command,
                "result": rt.controller.execute_read(parsed),
            }
        if parsed.kind not in MUTATION_KINDS:
            raise HTTPException(
                status_code=400,
                detail=f"unsupported parsed command kind: {parsed.kind}",
            )
        job, created = rt.jobs.submit(
            command=body.command,
            parsed=parsed,
            request_id=body.request_id,
        )
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED if created else status.HTTP_200_OK,
            content={"mode": "job", "created": created, "job": job},
        )

    @application.get("/v1/jobs/{job_id}", dependencies=[Depends(authorize)])
    async def job_status(job_id: str, rt: GatewayRuntime = Depends(runtime)):
        return rt.jobs.get(job_id)

    @application.post("/v1/jobs/{job_id}/cancel", dependencies=[Depends(authorize)])
    async def cancel_job(job_id: str, rt: GatewayRuntime = Depends(runtime)):
        return {
            "job": rt.jobs.cancel(job_id),
            "interrupt_supported": False,
            "note": (
                "Queued work is cancelled. MuJoCo may finish an action that the coordinator "
                "already accepted."
            ),
        }

    @application.post("/v1/emergency-stop", dependencies=[Depends(authorize)])
    async def emergency_stop(rt: GatewayRuntime = Depends(runtime)):
        jobs = rt.jobs.cancel_all()
        lease = rt.controller.release_lease()
        return {
            "status": "stop_requested",
            "jobs": jobs,
            "lease": lease,
            "interrupt_supported": False,
            "note": (
                "The gateway stopped queued work and released control. The current simulation "
                "motion API has no hard-interrupt primitive, so an accepted MuJoCo action may "
                "finish."
            ),
        }

    return application


app = create_app()


__all__ = [
    "ConsoleRequest",
    "GatewayRuntime",
    "JobManager",
    "PresetWriteRequest",
    "create_app",
    "app",
]
