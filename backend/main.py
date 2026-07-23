from fastapi import FastAPI, HTTPException, BackgroundTasks, Request, Query, Body, WebSocket
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse, RedirectResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware
from pydantic import BaseModel, Field, ValidationError
import json
import os
import re
import math
import uuid
import asyncio
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple
import io
import logging
import functools
import inspect

import httpx

logger = logging.getLogger(__name__)


def _install_windows_connection_reset_handler() -> None:
    """
    Windows Proactor asyncio logs ERROR when a client aborts TCP/WS (WinError 10054).

    Harmless on disconnect; suppress so real failures stay visible.
    """
    if os.name != "nt":
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    previous = loop.get_exception_handler()

    def _handler(loop: asyncio.AbstractEventLoop, context: Dict[str, Any]) -> None:
        exc = context.get("exception")
        if isinstance(exc, ConnectionResetError):
            return
        if previous is not None:
            previous(loop, context)
        else:
            loop.default_exception_handler(context)

    loop.set_exception_handler(_handler)


# Load .env from project root (parent of backend/) â€” only LAB_VIEW_PATH is required there.
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_env_path = os.path.join(_project_root, ".env")
if os.path.exists(_env_path):
    from dotenv import load_dotenv
    load_dotenv(_env_path)
    print(f"[CONFIG] Loaded .env from {_env_path}")
else:
    print(f"[CONFIG] No .env at {_env_path}")

from lab_model.coordinator.backends.lab_view_config import (
    get_lab_manifest,
    get_lab_view_paths,
    laser_line_coeffs_from_doc,
    line_id_pattern,
    load_layout_document,
    read_laser_lines_doc,
    set_backend_registry,
    two_points_define_line,
    write_laser_lines_doc,
)
from lab_model.language.domain import motor_rotation_store as motor_rot

# Lab language — HTTP routes and command validation bind to these registries.
# Hand-written wrappers are fine; the language must not be decorative.
from lab_model.language import measurables as lab_measurables  # noqa: F401 — register plugins
from lab_model.language.measurables import (
    MEASURABLE_REGISTRY,
    legacy_wire_view,
    materialize_measurable,
    resolve_tensor_with_state_path,
)
from lab_model.language.primitives import (
    ConfirmHoldingTagBody,
    EvalKernelBody,
    HoverBody,
    MoveComponentBody,
    PickComponentBody,
    EndTeleopBody,
    PlaceFromHoverBody,
    PrimitiveId,
    RecordMeasurablesBody,
    ScanRotateInPlaceBody,
    StartTeleopBody,
    StartLiveFeedBody,
    OptimizeBody,
    TeleopGotoBody,
    TeleopGotoParameters,
    TeleopJogBody,
    TeleopJogParameters,
    execute_validated_command,
    fetch_read_primitive,
    parse_command_payload,
    schedule_validated_command,
    validation_error_detail,
)


from lab_model.execution.optimization.errors import EnsemblePreflightError
from lab_model.execution.optimization.preflight import preflight_ensemble, preflight_objective_sources
from lab_model.execution.optimization.compiler import compile_objective_payload
from lab_model.execution.optimization.spec import ObjectiveSpec
from lab_model.execution.optimization.metrics import METRIC_REGISTRY
from lab_model.execution.optimization.metrics import weighted_sum as _weighted_sum_metrics  # noqa: F401 â€” register
from lab_model.execution.optimization.kernels import list_kernels
from lab_model.coordinator.jobs.lease_manager import (
    LeaseConflictError,
    LeaseExpiredError,
    LeaseNotFoundError,
    SessionLeaseManager,
    active_backend_id,
    lease_record_to_api_dict,
)
from lab_model.coordinator.backends.job_hub import JobManagerHub
from lab_model.coordinator.backends.registry import BackendRegistry, BackendRuntime
from lab_model.coordinator.backends.dispatch import (
    RequestLab,
    RequestRuntimeManager,
    active_backend_id_for_request,
    catalog_pins_for_request,
    control_dir_for_request,
    get_request_backend_id,
    recipes_dir_for_request,
    reset_request_backend_id,
    set_request_backend_id,
)
from lab_model.coordinator.jobs.job_manager import JobNotFoundError, parse_snapshot_ref, validate_submit_spec
from lab_model.coordinator.jobs.initialization_policy import normalize_initialization_policy
from lab_model.coordinator.jobs.runner import run_job, schedule_job_runner
from lab_model.coordinator.backends.server import BackendSession, require_backend, resolve_backend_id
from lab_model.execution.edge import edge_agent_registry, edge_command_queue
from lab_model.execution.edge.client import (
    EdgeTransport,
    HttpEdgeClient,
    edge_config_for_backend,
    resolve_edge_client,
)
from lab_model.execution.edge.registry import DEFAULT_STALE_AFTER_S
from lab_model.execution.edge.stream_proxy import (
    media_type_for_transport,
    proxy_stream_response,
    resolve_live_channel_path,
)

backend_registry = BackendRegistry.from_project(_project_root)
set_backend_registry(backend_registry)
job_hub = JobManagerHub()
session_lease_manager = SessionLeaseManager()
lab = RequestLab(backend_registry)
runtime_manager = RequestRuntimeManager(backend_registry)


def _on_edge_evicted(rec) -> None:
    """Fail-closed: drop leases and fail open jobs when an edge goes stale."""
    backend_id = rec.backend_id
    reason = rec.disconnected_reason or "edge disconnected"
    error = f"edge offline: {reason}"
    try:
        lease = session_lease_manager.release_backend(backend_id)
        if lease is not None:
            logger.warning(
                "edge eviction released lease backend=%s lease_id=%s",
                backend_id,
                lease.lease_id,
            )
    except Exception:  # noqa: BLE001
        logger.exception("edge eviction lease release failed backend=%s", backend_id)
    try:
        failed = job_hub.for_backend(backend_id).fail_open_work(error=error)
        if failed:
            logger.warning(
                "edge eviction failed %d job(s) backend=%s",
                len(failed),
                backend_id,
            )
    except Exception:  # noqa: BLE001
        logger.exception("edge eviction job fail failed backend=%s", backend_id)


edge_agent_registry.set_on_evict(_on_edge_evicted)


def _active_backend_id() -> str:
    bid = get_request_backend_id()
    if bid:
        return bid
    # Pre-request contexts (startup logs): first available id for display only.
    ids = backend_registry.known_backend_ids()
    return ids[0] if ids else "unknown"


def _runtime_for_active(*, init: bool = False) -> BackendRuntime:
    return require_backend(backend_registry, _active_backend_id(), init=init)


def _lab_mode_for_active() -> str:
    return _runtime_for_active().lab_mode


def _CONTROL_DIR() -> str:
    return control_dir_for_request(backend_registry)


def _RECIPES_DIR() -> str:
    return recipes_dir_for_request(backend_registry)


def _catalog_pins_store():
    return catalog_pins_for_request(backend_registry)

def _kick_job_runner_for(backend_id: str) -> None:
    """Start the next queued job for one backend on the running event loop.

    When an edge agent is attached for this backend, leave the job queued for
    the agent to claim via ``GET /api/edge/work`` (coordinator â‰  edge).
    """
    if edge_agent_registry.is_attached(backend_id):
        logger.info(
            "edge attached for %s â€” skipping in-process job runner",
            backend_id,
        )
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    mgr = job_hub.for_backend(backend_id)

    async def _run_one(job_id: str) -> None:
        rt = require_backend(backend_registry, backend_id, init=True)
        with BackendSession(rt):
            await run_job(
                job_id,
                lab=rt.lab,
                runtime_manager=rt.runtime_manager,
                lease_manager=session_lease_manager,
                job_manager=mgr,
                backend_id=backend_id,
                control_dir=rt.control_dir(),
                pins_lookup=rt.catalog_pins_store,
                repo_owns_bench=lambda repo_id: _repo_owns_bench_for(rt, repo_id),
            )

    schedule_job_runner(
        job_manager=mgr,
        loop=loop,
        run_fn=_run_one,
    )


def _kick_job_runner() -> None:
    """Kick runners for every backend that has queued work."""
    for bid in backend_registry.known_backend_ids():
        mgr = job_hub.for_backend(bid)
        if mgr.runner_should_start():
            _kick_job_runner_for(bid)


async def _proxy_to_edge(
    *,
    backend_id: str,
    kind: str,
    payload: Dict[str, Any],
    timeout_s: float = 120.0,
) -> Dict[str, Any]:
    """Enqueue work for an attached edge agent and wait for its result (Step B.1).

    Prefer :func:`_southbound_execute` for new call sites â€” this remains for
    ``get_lab_state`` and other non-primitive kinds on the poll queue.
    """
    if not edge_agent_registry.is_attached(backend_id):
        raise HTTPException(
            status_code=409,
            detail=f"no edge agent attached for {backend_id!r}",
        )
    done = await asyncio.to_thread(
        edge_command_queue.submit_and_wait,
        backend_id=backend_id,
        kind=kind,
        payload=payload,
        timeout_s=timeout_s,
    )
    if done.error:
        raise HTTPException(status_code=502, detail=done.error)
    return done.result if isinstance(done.result, dict) else {"status": "ok"}


def _edge_client_for(backend_id: str | None = None):
    """Resolve Phase-3 EdgeClient (poll > HTTP edge.base_url > in-process)."""
    bid = (backend_id or _active_backend_id()).strip()
    cfg = edge_config_for_backend(backend_registry, bid)
    return resolve_edge_client(bid, lab=lab, edge_config=cfg)


async def _southbound_execute(
    command: Dict[str, Any],
    *,
    backend_id: str | None = None,
    timeout_s: float = 120.0,
    lease_id: str | None = None,
):
    """Run one primitive via the unified EdgeClient southbound path."""
    bid = (backend_id or _active_backend_id()).strip()
    client = _edge_client_for(bid)
    result = await client.execute_command(
        command, timeout_s=timeout_s, lease_id=lease_id
    )
    if not result.ok:
        status = 502 if result.transport != EdgeTransport.IN_PROCESS else 409
        raise HTTPException(status_code=status, detail=result.error or "edge execute failed")
    return result


def _telemetry_after_edge(tag_id: str, edge_result) -> Dict[str, Any]:
    """Twin alias enrichment: local lab when in-process, else edge result fields."""
    if edge_result.transport == EdgeTransport.IN_PROCESS and lab is not None:
        try:
            return lab.return_telemetry_for_tag(tag_id) or {}
        except Exception:
            return {}
    raw = edge_result.result if isinstance(edge_result.result, dict) else {}
    tel = raw.get("telemetry")
    return tel if isinstance(tel, dict) else {}


def _command_lease_required() -> bool:
    return _command_lease_required_for(_active_backend_id())


def _mock_auto_approve_publish() -> bool:
    return _mock_auto_approve_publish_for(_active_backend_id())


def _solo_mode() -> bool:
    """Local single-operator mode: mutations do not require a session lease."""
    flag = (os.environ.get("CLOUDLABS_SOLO") or "").strip().lower()
    return flag in ("1", "true", "yes")


def _strict_lease_mock() -> bool:
    strict = (os.environ.get("CLOUDLABS_STRICT_LEASE") or "").strip().lower()
    return strict in ("1", "true", "yes")


def _coordinator_policy() -> Dict[str, Any]:
    return {
        "solo": _solo_mode(),
        "strict_lease_mock": _strict_lease_mock(),
    }


def _command_lease_required_for(backend_id: str) -> bool:
    """Whether mutating commands must present a matching session lease.

    - ``CLOUDLABS_SOLO=1``: never required (laptop / single operator).
    - ``mock.*``: required only when ``CLOUDLABS_STRICT_LEASE=1``.
    - real / other backends: always required (Take control or SDK ``connect``).
    """
    if _solo_mode():
        return False
    if backend_id.startswith("mock."):
        return _strict_lease_mock()
    return True


def _mock_auto_approve_publish_for(backend_id: str) -> bool:
    return backend_id.startswith("mock.")


def _session_lease_runtime_field(backend_id: str) -> Optional[Dict[str, Any]]:
    record = session_lease_manager.active_lease(backend_id)
    if record is None:
        return None
    return lease_record_to_api_dict(record)


def _repo_owns_bench_for(runtime: BackendRuntime, repo_id: str) -> bool:
    from lab_model.coordinator.state.control_manager import read_bench_origin, repo_owns_bench

    safe = (repo_id or "default").strip() or "default"
    return repo_owns_bench(runtime.control_dir(), safe)


print(
    f"[CONFIG] Backend registry: {len(backend_registry.known_backend_ids())} backend(s): "
    + ", ".join(backend_registry.known_backend_ids())
)


def _extract_lease_id(payload: Dict[str, Any], request: Request) -> Optional[str]:
    header = (request.headers.get("X-CloudLabs-Lease") or "").strip()
    if header:
        return header
    body_val = payload.get("lease_id")
    if isinstance(body_val, str) and body_val.strip():
        return body_val.strip()
    return None


def _checkout_skip_dirty_guard(
    payload: "ControlCheckoutBody",
    request: Request,
) -> bool:
    """Plan-only preview and force_reconcile under a valid lease bypass dirty guard."""
    if payload.preview:
        return True
    policy = normalize_initialization_policy(payload.initialization_policy)
    if policy != "force_reconcile":
        return False
    lease_id = _extract_lease_id({}, request)
    if not lease_id:
        return False
    try:
        session_lease_manager.validate_command_lease(
            backend_id=_active_backend_id(),
            lease_id=lease_id,
            require_when_locked=True,
        )
        return True
    except (LeaseConflictError, LeaseNotFoundError, LeaseExpiredError):
        return False


def _lease_conflict_response(exc: LeaseConflictError) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={
            "detail": "backend_locked",
            "holder": exc.holder,
            "backend_id": exc.backend_id,
        },
    )



def _persist_session_checkpoint_on_shutdown() -> None:
    """Persist checkpoints only for backends that were already initialized.

    Never call ``init=True`` / ``require_backend`` here: a failed bind, an
    unavailable backend (e.g. missing lab_automation), or a coordinator that
    never served a Twin session must not construct communicators or log 503s.
    """
    for bid in backend_registry.known_backend_ids():
        try:
            rt = backend_registry.get_runtime(bid, init=False)
            if rt.availability != "ready" or rt.lab is None:
                continue
            with BackendSession(rt):
                saver = getattr(rt.lab, "save_session_checkpoint_if_enabled", None)
                if callable(saver):
                    saver()
        except Exception as e:  # noqa: BLE001
            logger.warning("Shutdown session checkpoint save failed for %s: %s", bid, e)


@asynccontextmanager
async def _app_lifespan(_: FastAPI):
    _install_windows_connection_reset_handler()

    async def _edge_stale_sweeper() -> None:
        while True:
            try:
                edge_agent_registry.sweep_stale()
            except Exception:  # noqa: BLE001
                logger.exception("edge stale sweeper failed")
            await asyncio.sleep(1.0)

    sweeper = asyncio.create_task(_edge_stale_sweeper(), name="edge-stale-sweeper")
    try:
        yield
    finally:
        sweeper.cancel()
        try:
            await sweeper
        except asyncio.CancelledError:
            pass
        # Phase 8 teardown: stop the TELEOP stale-lease sweeper thread (if it
        # ever started) before persisting the session checkpoint, so the
        # checkpoint reflects a quiesced state instead of one mid-sweep.
        for bid in backend_registry.known_backend_ids():
            try:
                rt = backend_registry.get_runtime(bid, init=False)
                if rt.availability != "ready" or rt.lab is None:
                    continue
                with BackendSession(rt):
                    shutdown = getattr(rt.lab, "shutdown_lab_processes", None)
                    if callable(shutdown):
                        shutdown()
                    rt.lab.stop_teleop_sweeper()
            except Exception:
                pass
        _persist_session_checkpoint_on_shutdown()


app = FastAPI(lifespan=_app_lifespan)


@app.middleware("http")
async def _backend_selection_middleware(request: Request, call_next):
    """Bind backend_id + lab-view paths for /api/* routes (except backends listing)."""
    from lab_model.coordinator.backends.context import bind_backend_context, reset_backend_context

    path = request.url.path
    skip = (
        path in ("/api/backends",)
        or path.startswith("/api/jobs/submit")
        or path.startswith("/api/jobs/lease/")
        or not path.startswith("/api/")
    )
    token = None
    paths_token = None
    if not skip:
        backend_id = (request.query_params.get("backend_id") or "").strip()
        if not backend_id:
            backend_id = (request.headers.get("X-CloudLabs-Backend") or "").strip()
        if not backend_id:
            for spec in backend_registry.list_specs():
                rt = backend_registry.get_runtime(spec.backend_id, init=False)
                if rt.availability == "ready":
                    backend_id = rt.backend_id
                    break
        if not backend_id:
            return JSONResponse(
                status_code=400,
                content={
                    "detail": (
                        "backend_id is required (query ?backend_id=, header X-CloudLabs-Backend). "
                        "List options with GET /api/backends."
                    )
                },
            )
        try:
            rt = require_backend(backend_registry, backend_id, init=False)
        except HTTPException as exc:
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
        token = set_request_backend_id(backend_id)
        # Paths must be bound here â€” get_lab_view_paths() / load_layout_document()
        # read contextvars, not the registry singleton.
        if rt.paths.root_dir:
            paths_token = bind_backend_context(rt.paths, rt.manifest)
    try:
        return await call_next(request)
    finally:
        if paths_token is not None:
            reset_backend_context(paths_token)
        if token is not None:
            reset_request_backend_id(token)

# --- Models ---
class RecipeStep(BaseModel):
    step: int
    action: str
    component: Optional[str] = None
    target: Optional[str] = None
    parameters: Dict[str, Any] = {}

class Recipe(BaseModel):
    id: str
    name: str
    description: Optional[str] = ""
    steps: List[RecipeStep]

class RuntimeModeBody(BaseModel):
    mode: str


class SessionReconcileApplyBody(BaseModel):
    tag_ids: List[str]


class RefreshPoseBody(BaseModel):
    """Pose refresh selection: prefer ``apply_tag_ids``; legacy ``preserve_tag_ids``."""

    preserve_tag_ids: List[str] = Field(default_factory=list)
    apply_tag_ids: List[str] = Field(default_factory=list)
    tag_ids: List[str] = Field(default_factory=list)


class ControlCreateRepoBody(BaseModel):
    repo_id: str
    display_name: Optional[str] = None


class ControlCommitBody(BaseModel):
    message: str = ""
    branch: str = "main"
    parent_id: Optional[str] = None


class ControlBranchBody(BaseModel):
    branch: str
    parent_id: str


class ControlCheckoutBody(BaseModel):
    configuration_id: str
    mode: str = "soft"
    preview: bool = False
    initialization_policy: Optional[str] = None
    # When true, the frontend has already driven the reconcile primitives one at
    # a time through /api/command (for step-by-step visibility). The endpoint
    # then only *records* the result â€” projection + pointer + bench claim â€” and
    # runs no motion of its own.
    finalize: bool = False


class ControlObservationsBody(BaseModel):
    configuration_id: str
    message: Optional[str] = None


class ControlSetupBody(BaseModel):
    name: str
    configuration_id: Optional[str] = None
    message: Optional[str] = None
    include_observations: bool = True


class ControlStashBody(BaseModel):
    message: Optional[str] = None
    preview: bool = False
    # See ControlCheckoutBody.finalize. For stash, the frontend must also pass
    # back the ``snapshot`` captured at preview time (the dirty bench, before the
    # primitives drove it back to base) so the stash entry records the right
    # state. Pop needs no snapshot â€” the server already holds the stash.
    finalize: bool = False
    snapshot: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None


async def _run_reserved_background_task(
    manager: Any,
    token: str,
    function: Any,
    args: Tuple[Any, ...],
    kwargs: Dict[str, Any],
) -> None:
    try:
        result = function(*args, **kwargs)
        if inspect.isawaitable(result):
            await result
    finally:
        manager.release_operation(token)


class _ReservedBackgroundTasks:
    """BackgroundTasks facade that releases a runtime reservation afterward."""

    def __init__(
        self,
        delegate: BackgroundTasks,
        manager: Any,
        token: str,
    ) -> None:
        self.delegate = delegate
        self.manager = manager
        self.token = token
        self.scheduled = False

    def add_task(self, function: Any, *args: Any, **kwargs: Any) -> None:
        self.scheduled = True
        self.delegate.add_task(
            _run_reserved_background_task,
            self.manager,
            self.token,
            function,
            args,
            kwargs,
        )


def _session_reconciliation_offers_dict() -> Dict[str, Any]:
    from mock_edge.shared.session_checkpoint import (
        checkpoint_age_hours,
        checkpoint_lab_state,
        merge_offers_with_debug,
        read_checkpoint_document,
        reconciliation_thresholds_from_manifest,
        stale_warning_hours_from_manifest,
    )

    paths = get_lab_view_paths()
    chk_path = getattr(paths, "session_checkpoint_json", "") or ""
    stale_warn_hours = stale_warning_hours_from_manifest()
    thresholds = (
        lab.session_reconciliation_thresholds()
        if lab is not None
        else reconciliation_thresholds_from_manifest()
    )
    thresholds_dict = {"position_mm": thresholds.position_mm, "yaw_deg": thresholds.yaw_deg}

    doc = read_checkpoint_document(chk_path) if chk_path else None
    age_h = checkpoint_age_hours(doc.get("saved_at")) if isinstance(doc, dict) else None
    resp: Dict[str, Any] = {
        "enabled": bool(lab is not None and getattr(lab, "session_checkpoint_enabled", lambda: False)()),
        "skipped_reason": None,
        "checkpoint_path": chk_path or None,
        "checkpoint_saved_at": doc.get("saved_at") if isinstance(doc, dict) else None,
        "checkpoint_lab_mode": doc.get("lab_mode") if isinstance(doc, dict) else None,
        "age_hours": age_h,
        "stale_warning_hours": stale_warn_hours,
        "stale_warning": False,
        "thresholds": thresholds_dict,
        "offers": [],
    }

    if age_h is not None:
        resp["stale_warning"] = float(age_h) >= float(stale_warn_hours)

    if lab is None:
        resp["enabled"] = False
        resp["skipped_reason"] = "lab_unavailable"
        return resp

    chk_state = checkpoint_lab_state(doc) if isinstance(doc, dict) else None

    if not getattr(lab, "session_checkpoint_enabled", lambda: False)():
        resp["enabled"] = False
        resp["skipped_reason"] = "feature_disabled"
        return resp

    cur = lab.get_lab_state()
    if cur.get("system_status") != "IDLE":
        resp["skipped_reason"] = f"busy:{cur.get('system_status')}"
        return resp

    if not isinstance(chk_state, dict):
        resp["skipped_reason"] = "no_checkpoint"
        return resp

    offer_ids, merge_debug = merge_offers_with_debug(
        current_state=cur,
        checkpoint_state=chk_state,
        thresholds=thresholds,
    )
    resp["offers"] = [{"tag_id": tid} for tid in offer_ids]
    resp["debug"] = merge_debug
    try:
        from lab_model.coordinator.backends.lab_view_config import get_lab_manifest

        resp["manifest"] = {
            "session_checkpoint": bool(get_lab_manifest().session_checkpoint),
            "session_reconciliation": get_lab_manifest().as_dict().get(
                "session_reconciliation"
            ),
        }
    except Exception:
        resp["manifest"] = None
    print(
        f"[session-reconcile] backend_id={_active_backend_id()!r} "
        f"enabled={resp['enabled']} skipped={resp.get('skipped_reason')!r} "
        f"offers={len(offer_ids)} checkpoint={chk_path!r} "
        f"exists={os.path.isfile(chk_path) if chk_path else False} "
        f"debug={merge_debug}",
        flush=True,
    )
    return resp


# --- Recipe Executor (Uses Communicator) ---

async def execute_recipe(recipe: Recipe, target_lab: Any = None):
    recipe_lab = target_lab if target_lab is not None else lab
    print(f"[RECIPE] Starting recipe: {recipe.name}")
    
    for step in recipe.steps:
        print(f"[RECIPE] Executing Step {step.step}: {step.action}")
        
        target = step.component or step.target
        envelope = {
            "action": step.action,
            "target_id": target,
            "parameters": step.parameters or {},
        }
        try:
            cmd = parse_command_payload(envelope)
        except ValidationError as e:
            print(f"[RECIPE] Invalid step {step.step}: {validation_error_detail(e)}")
            raise
        await execute_validated_command(recipe_lab, cmd)

        await asyncio.sleep(0.5)
        
    print(f"[RECIPE] Recipe {recipe.name} complete. Saving Golden State...")

    # Save Golden State using Lab State. We capture ``holding`` alongside
    # ``components`` so recipes that end mid-HOLDING (e.g. PICK with no
    # PLACE_FROM_HOVER) are reproducible, and so the compare endpoint can
    # flag an unexpected held tag on replay. Older goldens predating this
    # field are still valid and compare fine -- see ``compare_golden_state``.
    state = recipe_lab.get_lab_state()
    golden_state = {
        "recipe_id": recipe.id,
        "timestamp": datetime.now().isoformat(),
        "components": state.get("components", {}),
        "holding": state.get("holding"),
        "system_status": state.get("system_status"),
        "metrics": {"completion_status": "SUCCESS"}
    }
    
    golden_path = os.path.join(_RECIPES_DIR(), f"{recipe.id}_golden.json")
    with open(golden_path, "w") as f:
        json.dump(golden_state, f, indent=2)
        
    print(f"[RECIPE] Golden State saved to {golden_path}")

# --- API Endpoints ---

# Serve static files (Frontend)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
frontend_path = os.path.join(BASE_DIR, "..", "frontend")
if not os.path.exists(frontend_path):
    os.makedirs(frontend_path)

# Cache-bust version: new value on every server start so browser loads latest JS/CSS
_STATIC_VERSION = str(int(time.time()))


class NoCacheMiddleware(BaseHTTPMiddleware):
    """Set no-cache headers for HTML and static assets so updates are always visible."""
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        path = request.scope.get("path", "")
        if path == "/" or path == "/twin" or path == "/debug" or path.startswith("/static"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response


app.add_middleware(NoCacheMiddleware)
app.mount("/static", StaticFiles(directory=frontend_path), name="static")


def _read_index_html(path: str) -> str:
    """Read HTML file and inject cache-bust version for script assets."""
    with open(path, "r", encoding="utf-8") as f:
        html = f.read()
    html = re.sub(r"js/main\.js\?v=\d+", f"js/main.js?v={_STATIC_VERSION}", html)
    return html


def _html_no_cache(path: str, *, bust_index: bool = False) -> Response:
    if bust_index:
        html = _read_index_html(path)
    else:
        with open(path, "r", encoding="utf-8") as f:
            html = f.read()
    return Response(
        content=html,
        media_type="text/html",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@app.get("/")
async def read_home():
    """Cloud Labs landing â€” not the Twin control room."""
    return _html_no_cache(os.path.join(frontend_path, "home.html"))


@app.get("/twin")
async def read_twin():
    """Twin UI control room (direct control + local VC)."""
    return _html_no_cache(os.path.join(frontend_path, "index.html"), bust_index=True)

@app.get("/api/platform/registries")
async def get_platform_registries():
    """Tunable/measurable plugins and primitive metadata (for UI tooling)."""
    from lab_model.platform import export_platform_registries

    return export_platform_registries()


@app.get("/api/catalog")
async def get_component_catalog():
    """
    Tags listed in ``active_catalog.json`` merged with rows from ``component_library.json``
    under ``LAB_VIEW_PATH``. HTTP edge backends read the request-bound files
    directly because they intentionally have no in-process communicator.
    """
    if lab is None:
        return []
    try:
        client = _edge_client_for()
        if client.transport != EdgeTransport.IN_PROCESS:
            from lab_model.coordinator.catalog.bundle import merged_catalog_rows

            return merged_catalog_rows()
        return lab.get_catalog()
    except Exception as e:
        logger.exception("GET /api/catalog failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to read catalog: {e}")


def _locked_runtime_mode_info() -> Dict[str, Any]:
    physical_armed = (
        _runtime_for_active().communicator == "real"
        and bool(lab)
        and type(lab).__name__ == "RealLabCommunicator"
    )
    active_mode = "physical" if physical_armed else "mock"
    reason = (
        "runtime mode is fixed by the startup manifest"
        if physical_armed
        else "physical backend failed to initialize"
    )
    return {
        "active_mode": active_mode,
        "physical_armed": physical_armed,
        "locked": True,
        "available_modes": [
            {"id": "mock", "label": "Mock UI", "enabled": False, "reason": reason},
            {
                "id": "mujoco",
                "label": "Simulator: MuJoCo",
                "enabled": False,
                "reason": reason,
            },
            {
                "id": "physical",
                "label": "Physical Experiment",
                "enabled": physical_armed,
                "reason": None if physical_armed else reason,
            },
        ],
        "simulator": {
            "running": False,
            "pid": None,
            "viewer": False,
            "realtime": False,
            "last_error": None,
        },
        "last_simulator_error": None,
    }


async def _edge_runtime_mode_info(rt: BackendRuntime) -> Dict[str, Any]:
    simulator: Dict[str, Any] = {
        "running": False,
        "pid": None,
        "viewer": False,
        "realtime": False,
        "last_error": None,
    }
    last_error = None
    client = _edge_client_for(rt.backend_id)
    base_url = getattr(client, "base_url", None)
    if base_url:
        try:
            async with httpx.AsyncClient(base_url=base_url, timeout=5.0) as edge:
                resp = await edge.get("/lab-state")
                resp.raise_for_status()
                state = resp.json()
            if isinstance(state, dict) and isinstance(state.get("simulator"), dict):
                simulator = {**simulator, **state["simulator"]}
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            simulator["last_error"] = last_error

    sim_backend = str(simulator.get("backend") or "").lower()
    active_mode = (
        "mujoco"
        if sim_backend.startswith("mujoco") or bool(simulator.get("mujoco_running"))
        else "mock"
    )
    reason = "runtime mode is fixed by the selected edge backend"
    return {
        "active_mode": active_mode,
        "physical_armed": False,
        "locked": True,
        "available_modes": [
            {
                "id": "mock",
                "label": "Mock UI",
                "enabled": active_mode == "mock",
                "reason": None if active_mode == "mock" else reason,
            },
            {
                "id": "mujoco",
                "label": "Simulator: MuJoCo",
                "enabled": active_mode == "mujoco",
                "reason": None if active_mode == "mujoco" else reason,
            },
            {
                "id": "physical",
                "label": "Physical Experiment",
                "enabled": False,
                "reason": reason,
            },
        ],
        "simulator": simulator,
        "last_simulator_error": last_error or simulator.get("last_error"),
    }


@app.get("/api/runtime-mode")
async def get_runtime_mode():
    rt = _runtime_for_active(init=False)
    if rt.spec.edge.configured:
        return await _edge_runtime_mode_info(rt)
    if runtime_manager is None:
        return _locked_runtime_mode_info()
    return runtime_manager.mode_info()


@app.post("/api/runtime-mode")
async def set_runtime_mode(payload: RuntimeModeBody):
    if runtime_manager is None:
        raise HTTPException(
            status_code=403,
            detail="Runtime mode is locked by the startup manifest",
        )
    if payload.mode.strip().lower() == "physical":
        raise HTTPException(
            status_code=403,
            detail="Physical mode can only be armed by the startup manifest",
        )
    try:
        return await asyncio.to_thread(runtime_manager.switch_mode, payload.mode)
    except Exception as exc:
        from mock_edge.host.runtime_mode import RuntimeModeError

        if isinstance(exc, RuntimeModeError):
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        logger.exception("Runtime mode switch failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/runtime-mode/refresh-mujoco")
async def refresh_mujoco_runtime():
    rt = _runtime_for_active(init=False)
    client = _edge_client_for(rt.backend_id)
    base_url = getattr(client, "base_url", None)
    if not base_url:
        raise HTTPException(
            status_code=409,
            detail="Refresh MuJoCo requires a configured HTTP simulation edge",
        )
    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=30.0) as edge:
            resp = await edge.post("/simulator/restart")
            body = resp.json()
        if resp.status_code >= 400:
            detail = body.get("error") or body.get("detail") or body
            raise HTTPException(status_code=resp.status_code, detail=detail)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("Refresh MuJoCo failed: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    mode_info = await _edge_runtime_mode_info(rt)
    mode_info["restart"] = body if isinstance(body, dict) else {"status": "ok"}
    return mode_info


@app.post("/api/components")
async def add_component(payload: Dict[str, Any], background_tasks: BackgroundTasks):
    """
    Adds a component: `placement_mode` is `breadboard` (default) or `storage` (mock: packed in Q3).
    Real lab still expects a physical place + rescan unless using mock.
    """
    background_tasks.add_task(lab.add_component_to_state, payload)
    mode = (payload.get("placement_mode") or "breadboard").lower()
    return {"status": "accepted", "message": f"Request submitted ({mode}): {payload.get('name')}"}


class ComponentAddFromInventoryBody(BaseModel):
    tag_id: str = Field(..., min_length=1)
    placement_mode: str = Field(default="breadboard")


@app.get("/api/catalog/active-tags")
async def get_active_catalog_tags():
    """Controlled tag ids (``active_catalog.json``) and full library tag ids."""
    from lab_model.coordinator.catalog.active_catalog_store import list_active_catalog_tags
    from lab_model.coordinator.catalog.bundle import library_by_tag

    try:
        return {
            "tag_ids": list_active_catalog_tags(),
            "library_tag_ids": sorted(library_by_tag().keys()),
        }
    except Exception as e:
        logger.exception("GET /api/catalog/active-tags failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to read active catalog: {e}")


@app.get("/api/catalog/library-rows")
async def get_library_catalog_rows():
    """All component_library rows (for sidebar display of off-catalog inventory)."""
    from lab_model.coordinator.catalog.bundle import library_by_tag

    try:
        return list(library_by_tag().values())
    except Exception as e:
        logger.exception("GET /api/catalog/library-rows failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to read component library: {e}")


class ComponentTrackBody(BaseModel):
    tag_id: str = Field(..., min_length=1)


@app.post("/api/components/track")
async def track_component(body: ComponentTrackBody):
    """Add a part to the controlled set and materialize it on the mock bench when applicable."""
    if lab is None:
        raise HTTPException(status_code=503, detail="Lab not initialized")
    _assert_lab_idle_for_control()
    try:
        result = await lab.track_component({"tag_id": body.tag_id.strip()})
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("POST /api/components/track failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if hasattr(lab, "_load_catalog"):
        lab._load_catalog()
    return result


@app.post("/api/components/untrack")
async def untrack_component(body: ComponentTrackBody):
    """Remove a part from the controlled set; mock also drops it from runtime (library)."""
    if lab is None:
        raise HTTPException(status_code=503, detail="Lab not initialized")
    _assert_lab_idle_for_control()
    try:
        result = await lab.untrack_component(body.tag_id.strip())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("POST /api/components/untrack failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return result


@app.post("/api/components/add")
async def add_component_from_inventory(body: ComponentAddFromInventoryBody):
    """Place a catalog part on the bench from OFF_TABLE inventory or the library."""
    if lab is None:
        raise HTTPException(status_code=503, detail="Lab not initialized")
    _assert_lab_idle_for_control()
    payload = {
        "tag_id": body.tag_id.strip(),
        "placement_mode": (body.placement_mode or "breadboard").lower(),
    }
    try:
        result = await lab.add_component_from_inventory(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("POST /api/components/add failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if hasattr(lab, "_load_catalog"):
        lab._load_catalog()
    return result

@app.get("/api/components/{tag_id}/tunables")
async def get_component_tunables(tag_id: str):
    """Commanded intent for one component (see refactor.md). Primitive: ``GET_TUNABLES``."""
    if lab is None:
        raise HTTPException(status_code=503, detail="Lab not initialized")
    try:
        return fetch_read_primitive(lab, PrimitiveId.GET_TUNABLES, tag_id)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=validation_error_detail(e))


@app.get("/api/components/{tag_id}/measurables")
async def get_component_measurables(tag_id: str):
    """Lab-reported values for one component. Primitive: ``GET_MEASURABLES``."""
    if lab is None:
        raise HTTPException(status_code=503, detail="Lab not initialized")
    try:
        return fetch_read_primitive(lab, PrimitiveId.GET_MEASURABLES, tag_id)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=validation_error_detail(e))


@app.get("/api/components/{tag_id}/parameters")
async def get_component_parameters(tag_id: str):
    """Static identity / manufacturer / constants. Primitive: ``GET_PARAMETERS``."""
    if lab is None:
        raise HTTPException(status_code=503, detail="Lab not initialized")
    try:
        return fetch_read_primitive(lab, PrimitiveId.GET_PARAMETERS, tag_id)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=validation_error_detail(e))


@app.post("/api/components/{tag_id}/measurables/record")
async def post_component_record_measurables(tag_id: str):
    """Record fresh measurables (``RECORD_MEASURABLES``) via EdgeClient."""
    client = _edge_client_for()
    if client.transport == EdgeTransport.IN_PROCESS:
        if lab is None:
            raise HTTPException(status_code=503, detail="Lab not initialized")
        state = lab.get_lab_state()
        current_status = state.get("system_status")
        if current_status == "BUSY" or current_status == "OPTIMIZING":
            raise HTTPException(status_code=409, detail=f"System is {current_status}. Please wait.")
    edge_result = await _southbound_execute(
        {"action": PrimitiveId.RECORD_MEASURABLES.value, "target_id": tag_id}
    )
    meas = None
    if edge_result.transport == EdgeTransport.IN_PROCESS and lab is not None:
        try:
            meas = fetch_read_primitive(lab, PrimitiveId.GET_MEASURABLES, tag_id)
        except ValidationError as e:
            raise HTTPException(status_code=422, detail=validation_error_detail(e))
    else:
        raw = edge_result.result if isinstance(edge_result.result, dict) else {}
        meas = raw.get("measurables")
    return {
        "status": "ok",
        "measurables": meas,
        "epoch_ms": edge_result.epoch_ms,
        "latch_quality": edge_result.latch_quality,
        "edge_transport": edge_result.transport.value,
    }


async def _edge_measurable_tensor(
    client: HttpEdgeClient,
    tag_id: str,
    field: str,
    *,
    record: bool,
    resolve: bool,
) -> Dict[str, Any]:
    """Tensor read for an HTTP edge (no in-process ``lab`` state).

    The canonical envelope comes from the ``RECORD_MEASURABLES`` execute result
    (``record=true``) or a ``GET /lab-state`` poll. Lazy images are pointed at the
    coordinator proxy route; ``resolve=true`` fetches the edge's JPEG bytes on
    demand and decodes them into a numpy array inline.
    """
    from dataclasses import replace  # noqa: PLC0415
    from lab_model.language.domain.component import get_measurables  # noqa: PLC0415
    from lab_model.language.measurables.resolve_data import (  # noqa: PLC0415
        resolve_tensor_from_bytes,
    )
    from lab_model.language.measurables.tensor import LazyRef  # noqa: PLC0415

    raw: Any = None
    if record:
        edge_result = await _southbound_execute(
            {"action": PrimitiveId.RECORD_MEASURABLES.value, "target_id": tag_id}
        )
        measurables = (
            edge_result.result.get("measurables")
            if isinstance(edge_result.result, dict)
            else None
        )
        if isinstance(measurables, dict):
            raw = measurables.get(field)
    if raw is None:
        state = client.get_lab_state() or {}
        entry = (state.get("components") or {}).get(tag_id)
        if isinstance(entry, dict):
            raw = get_measurables(entry).get(field)
    if raw is None:
        raise HTTPException(
            status_code=404,
            detail=f"Measurable {field!r} not set on {tag_id}; use ?record=true",
        )

    is_lazy_image = (
        field in MEASURABLE_REGISTRY
        and MEASURABLE_REGISTRY[field].tensor.layout == "lazy_image"
    )
    fetch_url = f"/api/components/{tag_id}/camera-image"
    tensor = materialize_measurable(
        tag_id,
        field,
        raw,
        backend_id=_active_backend_id(),
        fetch_url=fetch_url if is_lazy_image else None,
    )

    # The envelope short-circuits materialize, so its LazyRef still points at the
    # edge-relative path — capture it before rewriting for outgoing clients.
    edge_href = tensor.data.href if isinstance(tensor.data, LazyRef) else None

    if resolve and isinstance(tensor.data, LazyRef):
        href = (
            edge_href
            if isinstance(edge_href, str) and edge_href.startswith("/measurables/")
            else f"/measurables/{tag_id}/camera_image.jpg"
        )
        data = client.fetch_bytes(href)
        if data:
            tensor = resolve_tensor_from_bytes(tensor, data)

    # Still lazy (not resolved): expose the coordinator proxy URL so any client
    # resolves through the coordinator rather than the edge-relative path.
    if isinstance(tensor.data, LazyRef) and is_lazy_image:
        tensor = replace(
            tensor,
            data=LazyRef(kind="url", href=fetch_url, format=tensor.data.format),
        )

    return {"status": "ok", "tensor": tensor.to_api_dict(include_data=True)}


@app.get("/api/components/{tag_id}/measurables/{field}/tensor")
async def get_measurable_tensor(
    tag_id: str,
    field: str,
    *,
    record: bool = False,
    resolve: bool = False,
):
    """Return a ``MeasurableTensor`` envelope for one measurable field (Phase D).

    Query ``record=true`` to capture fresh measurables first (same as
    ``RECORD_MEASURABLES``). Query ``resolve=true`` to materialize lazy image
    payloads server-side (numpy array serialized in JSON).
    """
    safe_field = (field or "").strip()
    if safe_field.startswith("measurables."):
        safe_field = safe_field.split(".", 1)[1]
    if not safe_field:
        raise HTTPException(status_code=400, detail="field is required")

    client = _edge_client_for()
    if isinstance(client, HttpEdgeClient):
        return await _edge_measurable_tensor(
            client, tag_id, safe_field, record=record, resolve=resolve
        )

    if lab is None:
        raise HTTPException(status_code=503, detail="Lab not initialized")

    if record:
        state = lab.get_lab_state()
        current_status = state.get("system_status")
        if current_status in ("BUSY", "OPTIMIZING"):
            raise HTTPException(
                status_code=409,
                detail=f"System is {current_status}. Please wait.",
            )
        await lab.record_measurables_for_tag(tag_id)

    from lab_model.language.domain.component import get_measurables  # noqa: PLC0415
    from lab_model.language.measurables.tensor import LazyRef

    state = lab.get_lab_state()
    entry = (state.get("components") or {}).get(tag_id)
    if not isinstance(entry, dict):
        raise HTTPException(status_code=404, detail=f"Unknown tag {tag_id}")
    meas = get_measurables(entry)
    if safe_field not in meas or meas[safe_field] is None:
        raise HTTPException(
            status_code=404,
            detail=f"Measurable {safe_field!r} not set on {tag_id}; "
            "POST .../measurables/record or use ?record=true",
        )

    raw = meas[safe_field]
    fetch_url = f"/api/components/{tag_id}/camera-image"
    tensor = materialize_measurable(
        tag_id,
        safe_field,
        raw,
        backend_id=_active_backend_id(),
        fetch_url=fetch_url if safe_field in MEASURABLE_REGISTRY
        and MEASURABLE_REGISTRY[safe_field].tensor.layout == "lazy_image"
        else None,
    )

    if resolve and isinstance(tensor.data, LazyRef):
        wire = legacy_wire_view(raw) if isinstance(raw, dict) else None
        path = None
        if isinstance(wire, dict):
            path = wire.get("path")
        if isinstance(tensor.data, LazyRef) and tensor.data.kind == "file":
            path = path or tensor.data.href
        if isinstance(path, str) and path:
            tensor = resolve_tensor_with_state_path(tensor, filesystem_path=path)

    return {"status": "ok", "tensor": tensor.to_api_dict(include_data=True)}


# ---------------------------------------------------------------------------
# Phase 6 â€” per-component telemetry routes
#
# Catalog entries declare their telemetry channels (e.g. ``stream``,
# ``preview``) with direct URLs that include a ``{tag_id}`` token (see
# universal_component_architecture.md Â§13.2 and Â§16.6). These routes
# resolve those URLs for a given tag, validate the channel against the
# component's declared ``capabilities.telemetry`` block, and delegate to
# the existing ``LabCommunicator`` MJPEG / single-frame helpers.
#
# Lab-wide ``/api/table-cam/*`` was removed in Phase 9d; use these routes.
# ``/api/video-feed/*`` was removed in Phase 9c; live MJPEG is available
# via ``/api/components/{tag_id}/telemetry/stream`` on camera components.
# ``/api/optimization-feed/*`` was removed in Phase 9b.
# ---------------------------------------------------------------------------


def _catalog_row_or_404(tag_id: str) -> Dict[str, Any]:
    """Return the catalog row for ``tag_id`` or raise 404/503."""
    if lab is None:
        raise HTTPException(status_code=503, detail="Lab not initialized")
    catalog_row = (lab.catalog_map or {}).get(tag_id)
    if not isinstance(catalog_row, dict):
        raise HTTPException(status_code=404, detail=f"Unknown tag {tag_id!r}")
    return catalog_row


def _telemetry_lookup(tag_id: str, channel: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Resolve ``(catalog_row, channel_descriptor)`` for a per-tag telemetry route.

    Raises a precise HTTPException for every "no" path:
    - 503 if lab is not initialized.
    - 404 if the tag is not in the catalog.
    - 404 if the channel is not declared on the component.
    """
    from lab_model.coordinator.catalog.schema import telemetry_channel  # noqa: PLC0415

    if lab is None:
        raise HTTPException(status_code=503, detail="Lab not initialized")
    catalog_row = (lab.catalog_map or {}).get(tag_id)
    if not isinstance(catalog_row, dict):
        raise HTTPException(status_code=404, detail=f"Unknown tag {tag_id!r}")
    desc = telemetry_channel(catalog_row, channel)
    if desc is None:
        raise HTTPException(
            status_code=404,
            detail=f"Component {tag_id!r} does not declare telemetry channel {channel!r}",
        )
    return catalog_row, desc


def _resolve_cam_id_or_400(catalog_row: Dict[str, Any], tag_id: str) -> int:
    from lab_model.coordinator.catalog.schema import (  # noqa: PLC0415
        resolve_cam_id_for_tag,
        resolve_hardware_binding,
    )

    cam_id = resolve_cam_id_for_tag(catalog_row)
    if cam_id is None:
        binding = resolve_hardware_binding(catalog_row)
        hint = (
            f"backend={binding.backend!r}" if binding else "no hardware_binding"
        )
        raise HTTPException(
            status_code=400,
            detail=(
                f"Component {tag_id!r} declares a table_cam telemetry channel but no "
                f"recorder cam_id could be resolved ({hint}). "
                f"Add properties.hardware_binding with backend recorder_tcp."
            ),
        )
    return cam_id


@app.get("/api/components/{tag_id}/telemetry/stream")
async def get_component_telemetry_stream(tag_id: str, fps: int = 18):
    """Per-component MJPEG telemetry stream (Phase 6 / Â§13.2 ``stream`` channel).

    When an HTTP Edge Contract endpoint is configured, the coordinator
    **proxies** the edge capability channel (no BGR re-encode). Otherwise
    delegates to in-process ``lab.get_table_cam_stream`` / ``get_video_stream``.
    """
    from lab_model.coordinator.catalog.schema import resolve_telemetry_stream_backend

    # Phase 3: HTTP edge stream proxy (Tier B) when edge.base_url is set.
    client = _edge_client_for()
    if isinstance(client, HttpEdgeClient):
        caps = client.get_capabilities()
        resolved = resolve_live_channel_path(
            caps,
            measurable_or_channel=f"{tag_id}.camera_image",
            prefer_mjpeg=True,
        )
        if resolved is None:
            resolved = resolve_live_channel_path(
                caps, measurable_or_channel=tag_id, prefer_mjpeg=True
            )
        if resolved is not None:
            path, transport = resolved
            url = client.absolute_stream_url(path)
            if url:
                return proxy_stream_response(
                    url,
                    media_type=media_type_for_transport(transport)
                    or "multipart/x-mixed-replace; boundary=frame",
                )

    catalog_row, _desc = _telemetry_lookup(tag_id, "stream")
    from lab_model.language.domain.component import is_live_feed_active

    comp = ((lab.current_state or {}).get("components") or {}).get(tag_id)
    if not isinstance(comp, dict) or not is_live_feed_active(comp, "stream"):
        raise HTTPException(
            status_code=409,
            detail=(
                f"Live feed is not active for {tag_id!r}. "
                f"Call START_LIVE_FEED before opening the stream."
            ),
        )
    backend = resolve_telemetry_stream_backend(catalog_row)
    try:
        if backend == "overhead":
            gen = lab.get_video_stream(int(fps))
        elif backend == "table_cam":
            cam_id = _resolve_cam_id_or_400(catalog_row, tag_id)
            gen = lab.get_table_cam_stream(int(cam_id), int(fps))
        else:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Component {tag_id!r} declares telemetry.stream but no "
                    f"table_cam or overhead backend could be resolved."
                ),
            )
    except NotImplementedError as exc:
        raise HTTPException(
            status_code=501,
            detail="Telemetry streaming is unavailable for this lab backend.",
        ) from exc
    return StreamingResponse(
        gen,
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/components/{tag_id}/telemetry/preview")
async def get_component_telemetry_preview(tag_id: str, exposure: float = 0.2):
    """Per-component single-frame telemetry preview (``JPEGPoll`` / fast TeleOp poll).

    When live feed is active on a table recorder, returns the latest **JPEG**
    from the preview ring buffer (fast). Otherwise falls back to full ``CAP``
    still capture (slow, used by ``RECORD_MEASURABLES`` contract).
    """
    from lab_model.coordinator.catalog.schema import (
        live_feed_channel,
        resolve_telemetry_stream_backend,
    )
    from lab_model.language.domain.component import is_live_feed_active

    catalog_row = _catalog_row_or_404(tag_id)
    # JPEGPoll declares ``live_feed.stream`` with url ``.../telemetry/preview`` â€”
    # there is no separate ``preview`` catalog channel.
    if (
        live_feed_channel(catalog_row, "stream") is None
        and resolve_telemetry_stream_backend(catalog_row) == "none"
    ):
        raise HTTPException(
            status_code=404,
            detail=(
                f"Component {tag_id!r} does not declare live_feed.stream "
                f"or a resolvable preview backend."
            ),
        )
    backend = resolve_telemetry_stream_backend(catalog_row)
    if backend == "overhead":
        raise HTTPException(
            status_code=501,
            detail=(
                f"telemetry.preview is not supported for overhead camera {tag_id!r}; "
                f"use telemetry.stream (MJPEG) instead."
            ),
        )
    cam_id = _resolve_cam_id_or_400(catalog_row, tag_id)
    comp = ((lab.current_state or {}).get("components") or {}).get(tag_id)
    live_on = isinstance(comp, dict) and is_live_feed_active(comp, "stream")
    if backend == "table_cam":
        connected_map = getattr(lab, "_table_cam_connected", None)
        cam_connected = (
            isinstance(connected_map, dict)
            and bool(connected_map.get(int(cam_id)))
        )
        # Fast JPEG ring path while live, or warm "LIVE OFF" placeholder after
        # END_LIVE_FEED (streaming stopped, TCP still up). Avoid slow CAP when
        # JPEGPoll races a session teardown.
        if live_on or cam_connected:
            try:
                jpeg = lab.fetch_table_cam_preview_jpeg(int(cam_id))
            except NotImplementedError as exc:
                raise HTTPException(
                    status_code=501,
                    detail="Telemetry preview is unavailable for this lab backend.",
                ) from exc
            if jpeg:
                return Response(content=jpeg, media_type="image/jpeg")
        if not live_on:
            return Response(status_code=204)
    elif live_on:
        try:
            jpeg = lab.fetch_table_cam_preview_jpeg(int(cam_id))
        except NotImplementedError as exc:
            raise HTTPException(
                status_code=501,
                detail="Telemetry preview is unavailable for this lab backend.",
            ) from exc
        if jpeg:
            return Response(content=jpeg, media_type="image/jpeg")
    try:
        png_bytes = lab.capture_table_cam(int(cam_id), float(exposure))
    except NotImplementedError as exc:
        raise HTTPException(
            status_code=501,
            detail="Telemetry preview is unavailable for this lab backend.",
        ) from exc
    if not png_bytes:
        raise HTTPException(
            status_code=503,
            detail=(
                f"Lab returned an empty preview for cam {cam_id}. Is the camera "
                f"connected? Mock mode requires the table cam to be opened first."
            ),
        )
    return Response(content=png_bytes, media_type="image/png")


@app.get("/api/components/{tag_id}/telemetry/optimization-stream")
async def get_component_optimization_stream(tag_id: str, fps: int = 5):
    """Per-component optimization MJPEG stream (Phase 9b).

    Serves optimizer iteration thumbnails (the same payload as the
    legacy ``/api/optimization-feed/stream`` route). During an active
    ``OPTIMIZE`` run, ``tag_id`` must match
    ``lab_state.optimization_target_id``; otherwise the route returns
    **409**. In REAL mode delegates to ``lab.get_optimization_stream``;
    MOCK returns the static ``mock_feed.svg`` placeholder.
    """
    if lab is None:
        raise HTTPException(status_code=503, detail="Lab not initialized")
    catalog_row = (lab.catalog_map or {}).get(tag_id)
    if not isinstance(catalog_row, dict):
        raise HTTPException(status_code=404, detail=f"Unknown tag {tag_id!r}")

    state = lab.get_lab_state()
    active_target = state.get("optimization_target_id")
    sys_status = state.get("system_status")
    if sys_status == "OPTIMIZING" and active_target and tag_id != active_target:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Component {tag_id!r} is not the active optimization target "
                f"({active_target!r})"
            ),
        )

    stream_headers = {
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
        "Expires": "0",
        "X-Accel-Buffering": "no",
    }
    if _lab_mode_for_active() == "REAL" and hasattr(lab, "get_optimization_stream"):
        return StreamingResponse(
            lab.get_optimization_stream(fps=fps),
            media_type="multipart/x-mixed-replace; boundary=frame",
            headers=stream_headers,
        )
    return FileResponse(
        os.path.join(frontend_path, "mock_feed.svg"),
        headers=stream_headers,
    )


# ---------------------------------------------------------------------------
# Phase 8 â€” per-component TELEOP (universal_component_architecture.md Â§16.5)
#
# Three routes drive a single component's "in-air manual mode":
#
#   POST /api/components/{tag_id}/teleop/start
#       Acquire the per-component TELEOP lease. Nulls the component's
#       measurables (Golden Rule Â§3.2) and stamps a TTL timestamp so the
#       LabCommunicator's stale-lease sweeper can recover from a browser
#       crash without an explicit END_TELEOP.
#   POST /api/components/{tag_id}/teleop/end
#       Release the lease (idempotent â€” UI fires this on page unload).
#   POST /api/components/{tag_id}/telemetry/jog
#       One absolute jog frame: nominal_pose and/or nominal_motor_positions.
#       Frames are absolute, not deltas, so frame loss is self-healing.
#
# All three reuse the standard dispatch pipeline (``execute_validated_command``)
# so logging, validation, and per-primitive bookkeeping match the rest of
# the API. We return a synchronous 200 from each â€” these primitives are
# cheap (state mutations, no hardware blocking calls in Phase 8a) and the
# operator needs the ack before sending the next frame.
# ---------------------------------------------------------------------------


def _refuse_teleop_if_lab_down(tag_id: str) -> Dict[str, Any]:
    """Pre-flight check shared by all three TELEOP routes.

    Returns the catalog row on success; raises 404/503 on failure.
    Remote EdgeClient transports skip the in-process catalog gate (edge owns it).
    """
    client = _edge_client_for()
    if client.transport != EdgeTransport.IN_PROCESS:
        return {}
    if lab is None:
        raise HTTPException(status_code=503, detail="Lab not initialized")
    catalog_row = (lab.catalog_map or {}).get(tag_id)
    if not isinstance(catalog_row, dict):
        raise HTTPException(status_code=404, detail=f"Unknown tag {tag_id!r}")
    return catalog_row


@app.post("/api/components/{tag_id}/teleop/start")
async def post_component_teleop_start(tag_id: str):
    """Acquire the per-component TELEOP lease for ``tag_id``.

    Sets ``tunables.teleop_active=True`` and stamps ``teleop_last_jog_ts``.
    Nulls measurables per Â§3.2 Golden Rule. Refusals (BUSY/OPTIMIZING with
    the strict-quiet manifest knob, another component already teleoped,
    stored part) bubble up as 409 ``Conflict``.
    """
    _refuse_teleop_if_lab_down(tag_id)
    try:
        StartTeleopBody(action="START_TELEOP", target_id=tag_id)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=validation_error_detail(e))
    edge_result = await _southbound_execute(
        {"action": "START_TELEOP", "target_id": tag_id}
    )
    tunables = None
    if edge_result.transport == EdgeTransport.IN_PROCESS and lab is not None:
        tunables = fetch_read_primitive(lab, PrimitiveId.GET_TUNABLES, tag_id)
    return {
        "status": "ok",
        "message": f"TELEOP started for {tag_id}",
        "tunables": tunables,
        "telemetry": _telemetry_after_edge(tag_id, edge_result),
        "epoch_ms": edge_result.epoch_ms,
        "edge_transport": edge_result.transport.value,
    }


@app.post("/api/components/{tag_id}/teleop/end")
async def post_component_teleop_end(tag_id: str):
    """Release the per-component TELEOP lease for ``tag_id`` (idempotent).

    Always returns 200 â€” ending an already-released session is a success.
    This is the typical browser-unload path; the UI fires END on
    ``beforeunload`` and the server may or may not have already swept the
    stale lease.
    """
    _refuse_teleop_if_lab_down(tag_id)
    edge_result = await _southbound_execute(
        {"action": "END_TELEOP", "target_id": tag_id}
    )
    tunables = None
    if edge_result.transport == EdgeTransport.IN_PROCESS and lab is not None:
        tunables = fetch_read_primitive(lab, PrimitiveId.GET_TUNABLES, tag_id)
    return {
        "status": "ok",
        "message": f"TELEOP ended for {tag_id}",
        "tunables": tunables,
        "telemetry": _telemetry_after_edge(tag_id, edge_result),
        "epoch_ms": edge_result.epoch_ms,
        "edge_transport": edge_result.transport.value,
    }


@app.post("/api/components/{tag_id}/telemetry/jog")
async def post_component_telemetry_jog(tag_id: str, request: Request):
    """One absolute jog frame for the component currently under TELEOP.

    Body shape (validated by :class:`TeleopJogParameters`)::

        {
          "nominal_pose": {"x": 12.3, "y": 4.5, "rotation": 30.0},
          "nominal_motor_positions": {"1": 45.0},
          "frame_id": 42
        }

    At least one of ``nominal_pose`` / ``nominal_motor_positions`` is
    required; ``frame_id`` is optional client metadata.

    Refusals:

    - 404 â€” unknown tag.
    - 409 â€” tag is not in TELEOP (must START first).
    - 422 â€” malformed body.
    - 503 â€” lab not initialized.
    """
    _refuse_teleop_if_lab_down(tag_id)
    try:
        raw_body = await request.json()
    except Exception:
        raise HTTPException(status_code=422, detail="Body must be JSON.")
    if not isinstance(raw_body, dict):
        raise HTTPException(status_code=422, detail="Body must be a JSON object.")
    try:
        params = TeleopJogParameters.model_validate(raw_body)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=validation_error_detail(e))

    edge_result = await _southbound_execute(
        {
            "action": "TELEOP_JOG",
            "target_id": tag_id,
            "parameters": params.model_dump(exclude_none=True),
        }
    )
    return {
        "status": "ok",
        "frame_id": params.frame_id,
        "telemetry": _telemetry_after_edge(tag_id, edge_result),
        "epoch_ms": edge_result.epoch_ms,
        "edge_transport": edge_result.transport.value,
    }


@app.post("/api/components/{tag_id}/telemetry/goto")
async def post_component_telemetry_goto(tag_id: str, request: Request):
    """Release-to-go TeleOp: move to ``target_pose`` at ``speed``."""
    _refuse_teleop_if_lab_down(tag_id)
    try:
        raw_body = await request.json()
    except Exception:
        raise HTTPException(status_code=422, detail="Body must be JSON.")
    if not isinstance(raw_body, dict):
        raise HTTPException(status_code=422, detail="Body must be a JSON object.")
    try:
        params = TeleopGotoParameters.model_validate(raw_body)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=validation_error_detail(e))

    edge_result = await _southbound_execute(
        {
            "action": "TELEOP_GOTO",
            "target_id": tag_id,
            "parameters": params.model_dump(exclude_none=True),
        }
    )
    return {
        "status": "ok",
        "telemetry": _telemetry_after_edge(tag_id, edge_result),
        "epoch_ms": edge_result.epoch_ms,
        "edge_transport": edge_result.transport.value,
    }


@app.get("/api/components/{tag_id}/telemetry/live-pose")
async def get_component_telemetry_live_pose(tag_id: str):
    """High-rate live pose for TeleOp (in-memory; not in lab_state JSON).

    Deprecated hot path â€” prefer ``WS /api/components/{tag_id}/teleop/session``.
    Kept for debug clients and HTTP fallback.
    """
    _refuse_teleop_if_lab_down(tag_id)
    from lab_model.language.domain.component import is_teleop_ready

    state = lab.get_lab_state()
    entry = (state.get("components") or {}).get(tag_id)
    if not isinstance(entry, dict) or not is_teleop_ready(entry):
        raise HTTPException(
            status_code=409,
            detail=f"{tag_id!r} is not in an active ready TELEOP session.",
        )
    pose = lab.get_teleop_live_pose(tag_id)
    if pose is None:
        raise HTTPException(status_code=503, detail="Live pose unavailable.")
    return {"status": "ok", "pose": pose}


@app.websocket("/api/components/{tag_id}/teleop/session")
async def ws_component_teleop_session(websocket: WebSocket, tag_id: str):
    """Duplex TeleOp session: server-push pose @ ~50 Hz; client ``goto`` / ``ping``.

    Proxies to the edge's ``/ws/teleop`` when an HTTP Edge Contract backend is
    configured (edge owns the arm + coordinate transform); otherwise drives the
    in-process ``LabCommunicator``.
    """
    if runtime_manager is not None and runtime_manager.mode == "mujoco":
        await websocket.close(code=4403, reason="TeleOp is unavailable in MuJoCo v1")
        return

    # HTTP edge: proxy to the edge's own teleop socket (no in-process lab needed).
    client = _edge_client_for()
    if isinstance(client, HttpEdgeClient):
        from lab_model.execution.orchestration.teleop_session_ws import (
            run_teleop_session_proxy,
        )

        await run_teleop_session_proxy(websocket, client, tag_id)
        return

    if lab is None:
        await websocket.close(code=1013, reason="Lab not initialized")
        return
    catalog_row = (lab.catalog_map or {}).get(tag_id)
    if not isinstance(catalog_row, dict):
        await websocket.close(code=4404, reason=f"Unknown tag {tag_id!r}")
        return
    from lab_model.execution.orchestration.teleop_session_ws import run_teleop_session_websocket

    await run_teleop_session_websocket(websocket, lab, tag_id)


@app.post("/api/components/{tag_id}/telemetry/live-feed/start")
async def post_component_live_feed_start(tag_id: str, channel: str = "stream"):
    """Connect and start live feed (``START_LIVE_FEED``) via EdgeClient."""
    _refuse_teleop_if_lab_down(tag_id)
    edge_result = await _southbound_execute(
        {
            "action": "START_LIVE_FEED",
            "target_id": tag_id,
            "channel": channel,
        }
    )
    return {
        "status": "ok",
        "message": f"Live feed started for {tag_id}",
        "telemetry": _telemetry_after_edge(tag_id, edge_result),
        "epoch_ms": edge_result.epoch_ms,
        "edge_transport": edge_result.transport.value,
    }


@app.post("/api/components/{tag_id}/telemetry/live-feed/end")
async def post_component_live_feed_end(tag_id: str, channel: str = "all"):
    """Stop and disconnect live feed (``END_LIVE_FEED``) via EdgeClient."""
    _refuse_teleop_if_lab_down(tag_id)
    edge_result = await _southbound_execute(
        {
            "action": "END_LIVE_FEED",
            "target_id": tag_id,
            "channel": channel,
        }
    )
    return {
        "status": "ok",
        "message": f"Live feed ended for {tag_id}",
        "telemetry": _telemetry_after_edge(tag_id, edge_result),
        "epoch_ms": edge_result.epoch_ms,
        "edge_transport": edge_result.transport.value,
    }


@app.get("/api/components/{tag_id}/telemetry")
async def get_component_telemetry(tag_id: str):
    """Return saved telemetry session state (teleop + live_feed)."""
    if lab is None:
        raise HTTPException(status_code=503, detail="Lab not initialized")
    return {"status": "ok", "telemetry": lab.return_telemetry_for_tag(tag_id)}


@app.get("/api/components/{tag_id}/camera-image")
async def get_component_camera_image(tag_id: str):
    """Stream the PNG for ``measurables.camera_image`` (tensor LazyRef or legacy wire).

    Returns **404** when no image is currently recorded (e.g. after a motion
    nulled measurables — the operator must POST to ``.../measurables/record``).
    Path is read from saved lab state, not from the request, so there is no
    user-controlled path traversal vector; the on-disk file is still checked
    for existence + supported format as defense-in-depth.

    For HTTP edge backends there is no local file: the JPEG is proxied on
    demand from the edge's latched-frame route (``/measurables/{tag}/
    camera_image.jpg``).
    """
    client = _edge_client_for()
    if isinstance(client, HttpEdgeClient):
        data = client.fetch_bytes(f"/measurables/{tag_id}/camera_image.jpg")
        if not data:
            raise HTTPException(status_code=404, detail="No camera image recorded")
        return Response(content=data, media_type="image/jpeg")

    if lab is None:
        raise HTTPException(status_code=503, detail="Lab not initialized")
    state = lab.get_lab_state()
    entry = (state.get("components") or {}).get(tag_id)
    if not isinstance(entry, dict):
        raise HTTPException(status_code=404, detail=f"Unknown tag {tag_id}")
    from lab_model.language.domain.component import get_measurables  # noqa: PLC0415

    ci = get_measurables(entry).get("camera_image")
    if ci is None:
        raise HTTPException(status_code=404, detail="No camera image recorded")
    wire = legacy_wire_view(ci)
    if not isinstance(wire, dict):
        raise HTTPException(status_code=404, detail="No camera image recorded")
    path = wire.get("path")
    if not isinstance(path, str) or not path:
        raise HTTPException(status_code=404, detail="No camera image path")
    abs_path = os.path.abspath(path)
    if not os.path.isfile(abs_path):
        raise HTTPException(status_code=404, detail="Camera image file missing")
    fmt = str(wire.get("format") or "png").lower()
    media_by_fmt = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}
    if fmt not in media_by_fmt:
        raise HTTPException(status_code=400, detail=f"Unsupported camera image format {fmt!r}")
    return FileResponse(abs_path, media_type=media_by_fmt[fmt])


@app.get("/api/lab-state")
async def get_lab_state():
    """Return runtime lab state.

    When an edge agent is attached, the **edge** is the source of truth:
    prefer the latest heartbeat snapshot, otherwise proxy ``get_lab_state``.
    """
    bid = _active_backend_id()
    logger.debug("GET /api/lab-state backend=%s", bid)
    try:
        state: Optional[Dict[str, Any]] = None
        edge_source = False
        if edge_agent_registry.is_attached(bid):
            cached = edge_agent_registry.get_cached_lab_state(bid)
            if isinstance(cached, dict):
                state = cached
                edge_source = True
            else:
                # On-demand fetch from edge (agent must poll commands).
                try:
                    proxied = await _proxy_to_edge(
                        backend_id=bid,
                        kind="get_lab_state",
                        payload={},
                        timeout_s=5.0,
                    )
                    if isinstance(proxied, dict) and isinstance(
                        proxied.get("lab_state"), dict
                    ):
                        state = proxied["lab_state"]
                        edge_source = True
                        # Refresh cache so subsequent polls are cheap.
                        rec = edge_agent_registry.get_for_backend(bid)
                        if rec is not None:
                            edge_agent_registry.heartbeat(
                                rec.agent_id, lab_state=state
                            )
                except HTTPException as exc:
                    disconnect = edge_agent_registry.last_disconnect(bid)
                    raise HTTPException(
                        status_code=503,
                        detail={
                            "message": (
                                "edge attached but lab_state unavailable "
                                f"({exc.detail})"
                            ),
                            "edge_offline": disconnect,
                        },
                    ) from exc

        if state is None:
            _edge_client = _edge_client_for(bid)
            if _edge_client.transport == EdgeTransport.HTTP:
                try:
                    async with httpx.AsyncClient(
                        base_url=_edge_client.base_url,
                        timeout=10.0,
                    ) as client:
                        resp = await client.get("/lab-state")
                        resp.raise_for_status()
                        edge_state = resp.json()
                    if isinstance(edge_state, dict):
                        state = edge_state
                        edge_source = True
                except Exception as exc:  # noqa: BLE001
                    raise HTTPException(
                        status_code=503,
                        detail={
                            "message": "edge lab_state unavailable",
                            "reason": str(exc),
                        },
                    ) from exc
            else:
                state = lab.get_lab_state()

        logger.debug("GET /api/lab-state: ok edge_source=%s", edge_source)
        if isinstance(state, dict):
            active_runtime = _runtime_for_active(init=False).lab_mode.upper()
            disconnect = edge_agent_registry.last_disconnect(bid)
            edge_rec = edge_agent_registry.get_for_backend(bid)
            state = {
                **state,
                "lab_mode": active_runtime,
                "runtime_mode": active_runtime.lower(),
                "session_lease": _session_lease_runtime_field(bid),
                "active_backend_id": bid,
                "active_job_id": job_hub.active_job_id(bid),
                "edge_attached": edge_rec is not None,
                "edge_state_source": "edge" if edge_source else "coordinator",
                "edge_agent": edge_rec.to_api_dict() if edge_rec else None,
                "edge_offline": disconnect,
                "edge_stale_after_s": DEFAULT_STALE_AFTER_S,
            }
        return JSONResponse(content=state)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("GET /api/lab-state failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to read Lab State: {str(e)}")

def _schedule_pose_refresh(
    background_tasks: BackgroundTasks,
    payload: Optional[RefreshPoseBody] = None,
) -> Dict[str, Any]:
    if lab is None:
        raise HTTPException(status_code=500, detail="Lab Communicator not initialized")
    fn = getattr(lab, "refresh_pose_from_camera", None)
    if callable(fn):
        body = payload or RefreshPoseBody()
        kwargs = {
            "preserve_tag_ids": list(body.preserve_tag_ids or []),
            "apply_tag_ids": list(body.apply_tag_ids or []),
            "tag_ids": list(body.tag_ids or []),
        }
        background_tasks.add_task(functools.partial(fn, **kwargs))
        scoped = kwargs["tag_ids"] or kwargs["apply_tag_ids"]
        scope_note = f" (scope: {', '.join(scoped)})" if scoped else ""
        return {
            "status": "accepted",
            "message": f"Pose refresh from camera started{scope_note}",
            "scan_tag_ids": kwargs["apply_tag_ids"] or None,
        }
    return {
        "status": "ok",
        "message": "Pose refresh not supported for this lab backend",
    }


def _pose_refresh_offers_dict(scope_tag_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    from mock_edge.shared.session_checkpoint import reconciliation_thresholds_from_manifest
    from lab_model.coordinator.state.pose_refresh_offers import build_pose_refresh_offers
    from lab_model.coordinator.state.pose_refresh_selection import normalize_tag_id_list

    thresholds = (
        lab.session_reconciliation_thresholds()
        if lab is not None
        else reconciliation_thresholds_from_manifest()
    )
    thresholds_dict = {"position_mm": thresholds.position_mm, "yaw_deg": thresholds.yaw_deg}
    resp: Dict[str, Any] = {
        "supported": False,
        "skipped_reason": None,
        "thresholds": thresholds_dict,
        "offers": [],
        "hardware_note": (
            "Applying refresh runs a camera scan and updates tunables.reported_pose "
            "(bench report of the pose tunable; legacy measurables.pose is mirrored)."
            "for checked components."
        ),
    }

    if lab is None:
        resp["skipped_reason"] = "lab_unavailable"
        return resp

    preview_fn = getattr(lab, "preview_refresh_pose_candidates", None)
    if not callable(preview_fn):
        resp["skipped_reason"] = "preview_not_supported"
        return resp

    cur = lab.get_lab_state()
    if cur.get("system_status") != "IDLE":
        resp["skipped_reason"] = f"busy:{cur.get('system_status')}"
        return resp

    scope = normalize_tag_id_list(scope_tag_ids) if scope_tag_ids else []
    try:
        proposed = preview_fn(tag_ids=scope if scope else None)
    except TypeError:
        proposed = preview_fn()
    if not proposed:
        resp["skipped_reason"] = "no_eligible_components"
        return resp

    components = cur.get("components") or {}
    if scope:
        scope_set = set(scope)
        proposed = {k: v for k, v in proposed.items() if k in scope_set}
    offers = build_pose_refresh_offers(components, proposed, thresholds)
    resp["supported"] = True
    resp["offers"] = offers
    resp["eligible_count"] = len(offers)
    return resp


@app.get("/api/lab-state/refresh-pose/offers")
async def refresh_lab_pose_offers(tag_ids: Optional[str] = Query(None)):
    """Dry-run scan deltas vs current poses (mock: deterministic simulated scan)."""
    scope = None
    if tag_ids and tag_ids.strip():
        scope = [t.strip() for t in tag_ids.split(",") if t.strip()]
    return _pose_refresh_offers_dict(scope)


@app.post("/api/lab-state/refresh-pose")
async def refresh_lab_pose_from_camera(
    background_tasks: BackgroundTasks,
    payload: Optional[RefreshPoseBody] = Body(None),
):
    """
    Re-localize component poses from the overhead / table camera (real: full scan; mock: simulated noise).
  Supports scoped refresh via ``tag_ids`` / ``apply_tag_ids`` (preferred) or legacy ``preserve_tag_ids``.
    """
    cur = getattr(lab, "get_lab_state", lambda: {})
    try:
        st = cur()
    except Exception:  # noqa: BLE001
        st = {}
    if isinstance(st, dict):
        cs = st.get("system_status")
        if cs in ("BUSY", "OPTIMIZING"):
            raise HTTPException(status_code=409, detail=f"System is {cs}. Please wait.")
    return _schedule_pose_refresh(background_tasks, payload)


@app.post("/api/lab-state/refresh")
async def refresh_lab_state_legacy(
    background_tasks: BackgroundTasks,
    payload: Optional[RefreshPoseBody] = Body(None),
):
    """Deprecated: use ``POST /api/lab-state/refresh-pose`` (same behavior)."""
    return _schedule_pose_refresh(background_tasks, payload)


@app.get("/api/session-reconciliation/offers")
async def api_session_reconciliation_offers():
    return _session_reconciliation_offers_dict()


@app.post("/api/session-reconciliation/apply")
async def api_session_reconciliation_apply(payload: SessionReconcileApplyBody):
    if lab is None:
        raise HTTPException(status_code=503, detail="Lab communicator not initialized")
    if not getattr(lab, "session_checkpoint_enabled", lambda: False)():
        raise HTTPException(status_code=400, detail="Session checkpoint disabled for this communicator")
    cur = lab.get_lab_state()
    if cur.get("system_status") != "IDLE":
        raise HTTPException(
            status_code=409,
            detail=f"System is {cur.get('system_status')}; reconciliation only applies in IDLE.",
        )
    merged = lab.apply_session_reconciliation_tags(payload.tag_ids)
    return {"status": "ok", "applied_tag_ids": merged}


@app.post("/api/session-reconciliation/save")
async def api_session_checkpoint_save():
    """Write ``session_last_lab_state.json`` now (same shape as graceful shutdown save)."""

    if lab is None:
        raise HTTPException(status_code=503, detail="Lab communicator not initialized")
    if not getattr(lab, "session_checkpoint_enabled", lambda: False)():
        raise HTTPException(status_code=400, detail="Session checkpoint disabled for this communicator")
    cur = lab.get_lab_state()
    if cur.get("system_status") in ("BUSY", "OPTIMIZING"):
        raise HTTPException(
            status_code=409,
            detail=f"System is {cur.get('system_status')}. Please wait.",
        )
    lab.save_session_checkpoint_if_enabled()
    return {"status": "ok"}


_control_managers: Dict[str, Any] = {}


def _get_control_manager(repo_id: str):
    from lab_model.coordinator.state.control_manager import ControlManager

    safe = (repo_id or "default").strip() or "default"
    if safe not in _control_managers:
        _control_managers[safe] = ControlManager(_CONTROL_DIR(), safe)
    return _control_managers[safe]


_CONTROL_DEBUG = os.environ.get("CONTROL_DEBUG", "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "",
)


def _control_log(event: str, **fields: Any) -> None:
    """Lightweight tracing for the version-control state machine.

    Enabled by default; set CONTROL_DEBUG=0 to silence. Prints one compact line
    per event so we can follow ownership / applied / dirty decisions live.
    """
    if not _CONTROL_DEBUG:
        return
    parts = " ".join(f"{k}={v!r}" for k, v in fields.items())
    print(f"[control-debug] {event} {parts}", flush=True)


def _repo_owns_bench(repo_id: str) -> bool:
    """True when ``repo_id`` is the physical owner of the current global bench.

    Switching repos never moves the bench, so only the owning repo gets
    applied-based dirty detection; other repos diff against the empty state.
    """
    from lab_model.coordinator.state.control_manager import read_bench_origin, repo_owns_bench

    safe = (repo_id or "default").strip() or "default"
    owns = repo_owns_bench(_CONTROL_DIR(), safe)
    if _CONTROL_DEBUG:
        origin = read_bench_origin(_CONTROL_DIR())
        _control_log(
            "owns_bench",
            repo=safe,
            owns=owns,
            origin_repo=(origin or {}).get("repo_id"),
            origin_cfg=(origin or {}).get("configuration_id"),
        )
    return owns


def _claim_bench(repo_id: str, configuration_id: Optional[str]) -> None:
    """Record that ``repo_id`` physically realized the current bench."""
    from lab_model.coordinator.state.control_manager import write_bench_origin

    safe = (repo_id or "default").strip() or "default"
    _control_log("claim_bench", repo=safe, configuration_id=configuration_id)
    write_bench_origin(_CONTROL_DIR(), safe, configuration_id)


def _assert_lab_idle_for_control() -> None:
    if lab is None:
        raise HTTPException(status_code=503, detail="Lab communicator not initialized")
    status = (lab.get_lab_state() or {}).get("system_status")
    if status in ("BUSY", "OPTIMIZING"):
        raise HTTPException(
            status_code=409,
            detail=f"System is {status}. Please wait.",
        )


def _lab_runtime_manager():
    manager = getattr(lab, "_lab_runtime_manager", None)
    if manager is None:
        raise HTTPException(
            status_code=500,
            detail="RuntimeManager not available on lab communicator",
        )
    return manager


def _invalidate_control_manager_cache(repo_id: Optional[str] = None) -> None:
    if repo_id is None:
        _control_managers.clear()
        return
    safe = (repo_id or "default").strip() or "default"
    _control_managers.pop(safe, None)


@app.get("/api/control/repos")
async def control_list_repos():
    from lab_model.coordinator.state.control_manager import list_control_repos

    return {"repos": list_control_repos(_CONTROL_DIR())}


@app.post("/api/control/backfill-lines")
async def control_backfill_lines(payload: Dict[str, Any] = Body(default={})):
    """One-time, idempotent migration: inject the currently-drawn alignment
    overlays (guides + laser lines) into every commit across every repo that
    predates line-versioning. Only fills documents missing the keys.

    Guides come from the request body (the browser's localStorage import) when
    provided, otherwise from the live runtime; laser lines come from the runtime
    (seeded from the lab bundle).
    """
    from lab_model.coordinator.state.control_manager import list_control_repos
    from lab_model.coordinator.state.projections import (
        normalize_alignment_guides,
        normalize_laser_lines_doc,
    )

    runtime = lab.get_lab_state() if lab is not None else {}
    if isinstance(payload.get("alignment_guides"), list):
        guides = normalize_alignment_guides(payload.get("alignment_guides"))
    else:
        guides = normalize_alignment_guides(runtime.get("alignment_guides"))
    laser = normalize_laser_lines_doc(runtime.get("laser_lines"))

    repos = list_control_repos(_CONTROL_DIR())
    updated = 0
    for repo in repos:
        mgr = _get_control_manager(repo["repo_id"])
        updated += mgr.backfill_overlays(alignment_guides=guides, laser_lines=laser)
    return {"repos": len(repos), "updated": updated, "guides": len(guides)}


@app.post("/api/control/backfill-optimization-metadata")
async def control_backfill_optimization_metadata():
    """One-time, idempotent migration: infer optimization metadata on legacy commits.

    Reads legacy ``tunables.placement.mode`` and linked observation pins, writes
    ``metadata.optimization`` on each configuration document, and strips
    ``placement`` from stored configuration tunables.
    """
    from lab_model.coordinator.state.control_manager import list_control_repos

    repos = list_control_repos(_CONTROL_DIR())
    updated = 0
    for repo in repos:
        mgr = _get_control_manager(repo["repo_id"])
        updated += mgr.backfill_optimization_metadata()
    return {"repos": len(repos), "updated": updated}


@app.post("/api/control/repos")
async def control_create_repo(payload: ControlCreateRepoBody):
    from lab_model.coordinator.state.control_manager import create_control_repo, validate_repo_id

    try:
        validate_repo_id(payload.repo_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        repo = create_control_repo(
            _CONTROL_DIR(),
            payload.repo_id,
            display_name=payload.display_name,
        )
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=f"Repo already exists: {exc}") from exc
    _invalidate_control_manager_cache(repo["repo_id"])
    return {"status": "ok", "repo": repo}


@app.get("/api/control/{repo_id}/status")
async def control_status(repo_id: str):
    runtime = lab.get_lab_state() if lab is not None else None
    status = _get_control_manager(repo_id).status(
        runtime, owns_bench=_repo_owns_bench(repo_id)
    )
    working = status.get("working") or {}
    _control_log(
        "status",
        repo=repo_id,
        applied=(status.get("applied") or {}).get("configuration_id"),
        applied_branch=(status.get("applied") or {}).get("branch"),
        heads=status.get("heads"),
        on_head=working.get("on_head"),
        detached=working.get("detached"),
        dirty=working.get("dirty"),
        unadopted=working.get("unadopted"),
        owns_bench=working.get("owns_bench"),
    )
    return status


@app.get("/api/control/{repo_id}/history")
async def control_history(repo_id: str, branch: Optional[str] = Query(None)):
    mgr = _get_control_manager(repo_id)
    return {
        "repo_id": repo_id,
        "branch": branch,
        "head": mgr.get_head(branch or "main") if branch else mgr.status().get("heads"),
        "nodes": mgr.list_history(branch),
    }


@app.get("/api/control/{repo_id}/configurations/{commit_id}")
async def control_get_configuration(repo_id: str, commit_id: str):
    try:
        return _get_control_manager(repo_id).get_configuration(commit_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/control/{repo_id}/diff")
async def control_diff(
    repo_id: str,
    from_id: str = Query(..., alias="from"),
    to_id: str = Query(..., alias="to"),
):
    try:
        changes = _get_control_manager(repo_id).diff_configurations(from_id, to_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"from": from_id, "to": to_id, "changes": changes}


@app.post("/api/control/{repo_id}/configurations")
async def control_commit_configuration(repo_id: str, payload: ControlCommitBody):
    _assert_lab_idle_for_control()
    from lab_model.coordinator.catalog.bundle import active_tag_ids, library_by_tag
    from lab_model.coordinator.catalog.catalog_hash import compute_active_catalog_hash

    runtime = lab.get_lab_state()
    mgr = _get_control_manager(repo_id)
    working = mgr.working_state(runtime, owns_bench=_repo_owns_bench(repo_id))
    if working.get("detached"):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "detached_head",
                "message": (
                    "You are on an older commit (detached). Fork a new branch "
                    "here before committing changes."
                ),
            },
        )
    if working.get("viewing"):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "viewing_preview",
                "message": "Return to the bench before committing.",
            },
        )
    try:
        catalog_hash = compute_active_catalog_hash()
    except Exception:
        catalog_hash = None
    document = mgr.commit_from_runtime(
        runtime,
        message=payload.message,
        branch=payload.branch or "main",
        parent_id=payload.parent_id,
        catalog_hash=catalog_hash,
    )
    # Committing the live bench means this repo now owns it at the new node.
    _claim_bench(repo_id, str(document.get("id")) if document.get("id") else None)
    return {"status": "ok", "commit": document, "catalog_hash": catalog_hash}


@app.get("/api/control/{repo_id}/checkout-report")
async def control_checkout_report(
    repo_id: str,
    configuration_id: str = Query(..., min_length=1),
):
    """Tag/catalog compatibility between a commit and the live runtime bench."""
    from lab_model.coordinator.catalog.bundle import active_tag_ids, library_by_tag
    from lab_model.coordinator.catalog.catalog_hash import compute_active_catalog_hash

    mgr = _get_control_manager(repo_id)
    try:
        current_hash = compute_active_catalog_hash()
        catalog_ids = active_tag_ids()
        library_ids = list(library_by_tag().keys())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Catalog unavailable: {exc}") from exc

    runtime = lab.get_lab_state() if lab is not None else {}
    try:
        report = mgr.checkout_compatibility_report(
            configuration_id,
            runtime,
            current_catalog_hash=current_hash,
            catalog_tag_ids=catalog_ids,
            library_tag_ids=library_ids,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return report


@app.post("/api/control/{repo_id}/branches")
async def control_fork_branch(repo_id: str, payload: ControlBranchBody):
    branch = (payload.branch or "").strip()
    if not branch:
        raise HTTPException(status_code=400, detail="branch is required")
    try:
        _get_control_manager(repo_id).fork_branch(
            branch=branch,
            parent_id=payload.parent_id,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    # Forking carries the live bench onto the new branch, so this repo owns it.
    _claim_bench(repo_id, payload.parent_id)
    return {
        "status": "ok",
        "branch": branch,
        "head": payload.parent_id,
    }


@app.post("/api/control/{repo_id}/checkout")
async def control_checkout(repo_id: str, payload: ControlCheckoutBody, request: Request):
    _assert_lab_idle_for_control()
    mode = (payload.mode or "soft").strip().lower()
    mgr = _get_control_manager(repo_id)
    _control_log(
        "checkout:request",
        repo=repo_id,
        mode=mode,
        target=payload.configuration_id,
        preview=getattr(payload, "preview", None),
        applied=mgr.get_applied().get("configuration_id"),
    )
    try:
        document = mgr.get_configuration(payload.configuration_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    configuration = document.get("configuration") or {}
    metadata = document.get("metadata") or {}
    branch = str(document.get("branch") or "main")

    if mode == "adopt":
        # "Set as node": declare this node as the current node WITHOUT moving the
        # bench (git reset --soft). No reconcile plan, no projection â€” the
        # physical bench is left exactly as-is and becomes uncommitted edits
        # relative to the adopted node. Used to establish a base when you enter a
        # repo and have no current node yet.
        _control_log(
            "checkout:adopt",
            repo=repo_id,
            target=payload.configuration_id,
            branch=branch,
        )
        mgr.set_applied(payload.configuration_id, branch=branch)
        mgr.set_viewing(None)
        _claim_bench(repo_id, payload.configuration_id)
        if hasattr(lab, "_persist_state"):
            lab._persist_state()
        return {
            "status": "ok",
            "mode": "adopt",
            "configuration_id": payload.configuration_id,
            "branch": branch,
            "applied": mgr.get_applied(),
            "working": mgr.working_state(
                lab.get_lab_state(), owns_bench=_repo_owns_bench(repo_id)
            ),
        }

    if mode == "hard" and payload.finalize:
        # Step-by-step "Apply on bench": the frontend already ran every reconcile
        # primitive through /api/command (so the operator saw each one), and the
        # bench now physically matches the target. This records the outcome only:
        # snap tunables to the node's nominal poses, move the applied pointer, and
        # claim the bench. No reconcile plan, no motion.
        _control_log(
            "checkout:finalize",
            repo=repo_id,
            target=payload.configuration_id,
            branch=branch,
        )
        runtime_mgr = _lab_runtime_manager()
        runtime_mgr.apply_hard_checkout_projection(
            configuration,
            source=f"hard_checkout:{payload.configuration_id}",
            metadata=metadata if isinstance(metadata, dict) else None,
        )
        mgr.set_applied(payload.configuration_id, branch=branch)
        mgr.set_viewing(None)
        _claim_bench(repo_id, payload.configuration_id)
        if hasattr(lab, "_persist_state"):
            lab._persist_state()
        return {
            "status": "ok",
            "mode": "hard",
            "finalized": True,
            "configuration_id": payload.configuration_id,
            "branch": branch,
            "applied": mgr.get_applied(),
            "steps_executed": 0,
        }

    runtime = lab.get_lab_state()

    # Git-like guard applies ONLY to a hard checkout, the one mode that
    # physically moves the bench: you cannot reconcile away from a dirty working
    # table without committing or stashing first. Soft preview is read-only, and
    # "adopt" (handled above) only repoints the HEAD â€” both leave the bench
    # untouched, so neither is gated. (In particular, an unadopted repo reads as
    # dirty-vs-empty, so gating preview here would block you from ever clicking a
    # node to Set it.)
    if mode == "hard":
        owns_bench = _repo_owns_bench(repo_id)
        applied_id = mgr.get_applied().get("configuration_id") if owns_bench else None
        is_dirty = mgr.runtime_is_dirty(runtime, owns_bench=owns_bench)
        skip_dirty = _checkout_skip_dirty_guard(payload, request)
        _control_log(
            "checkout:hard-guard",
            repo=repo_id,
            target=payload.configuration_id,
            owns_bench=owns_bench,
            applied=applied_id,
            dirty=is_dirty,
            skip_dirty=skip_dirty,
            init_policy=normalize_initialization_policy(payload.initialization_policy),
            blocked=(is_dirty and payload.configuration_id != applied_id and not skip_dirty),
        )
        if is_dirty and payload.configuration_id != applied_id and not skip_dirty:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "dirty_working_table",
                    "message": (
                        "You have uncommitted changes on the bench. Commit or stash "
                        "them before checking out another configuration."
                    ),
                },
            )

    if mode in ("soft", "hard"):
        from lab_model.coordinator.catalog.bundle import active_tag_ids, library_by_tag
        from lab_model.coordinator.catalog.catalog_hash import compute_active_catalog_hash
        try:
            compat = mgr.checkout_compatibility_report(
                payload.configuration_id,
                runtime,
                current_catalog_hash=compute_active_catalog_hash(),
                catalog_tag_ids=active_tag_ids(),
                library_tag_ids=list(library_by_tag().keys()),
            )
        except Exception:
            compat = {"ready": True, "issues": []}
        if not compat.get("ready") and compat.get("blocking_count", 0) > 0:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "Bench is incompatible with configuration (blocking issues)",
                    "compatibility": compat,
                },
            )

    if mode == "soft":
        # Read-only preview. The live bench is NEVER mutated: the frontend
        # renders the returned configuration as an overlay. This is the core of
        # the state-machine separation â€” preview can no longer contaminate the
        # live runtime, so layout/dirty/compat (all computed against the real
        # bench) cannot fire spurious warnings while you are only *viewing* a
        # node. No `set_viewing`, no projection, no persist.
        return {
            "status": "ok",
            "mode": "soft",
            "configuration_id": payload.configuration_id,
            "configuration": configuration,
            "metadata": metadata if isinstance(metadata, dict) else {},
            "compatibility": compat,
        }
    if mode == "hard":
        # The live runtime IS the physical bench (preview never mutates it), so
        # always plan straight from the runtime. Correct across detached commits
        # AND across repos (membership reconcile turns component add/remove into
        # PLACE_FROM_STORAGE / STORE_COMPONENT).
        from_id = mgr.get_applied().get("configuration_id")
        plan = mgr.plan_checkout_from_runtime(runtime, payload.configuration_id)
        if payload.preview:
            return {
                "status": "planned",
                "mode": "hard",
                "configuration_id": payload.configuration_id,
                "from_id": from_id,
                "branch": branch,
                "plan": plan,
                "steps": len(plan),
                "compatibility": compat,
            }

        from lab_model.coordinator.state.reconcile_executor import (
            ReconcilePlanError,
            execute_reconcile_plan,
        )

        runtime_mgr = _lab_runtime_manager()
        try:
            if plan:
                await execute_reconcile_plan(
                    lab,
                    plan,
                    source=f"checkout:{payload.configuration_id}",
                )
        except ReconcilePlanError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=409,
                detail=f"Hard checkout failed: {exc}",
            ) from exc

        runtime_mgr.apply_hard_checkout_projection(
            configuration,
            source=f"hard_checkout:{payload.configuration_id}",
            metadata=metadata if isinstance(metadata, dict) else None,
        )
        mgr.set_applied(payload.configuration_id, branch=branch)
        mgr.set_viewing(None)
        # This repo now physically owns the bench at the checked-out node.
        _claim_bench(repo_id, payload.configuration_id)
        if hasattr(lab, "_persist_state"):
            lab._persist_state()
        return {
            "status": "ok",
            "mode": "hard",
            "configuration_id": payload.configuration_id,
            "from_id": from_id,
            "branch": branch,
            "plan": plan,
            "steps_executed": len(plan),
            "applied": mgr.get_applied(),
            "compatibility": compat,
        }
    raise HTTPException(status_code=400, detail="mode must be soft, hard, or adopt")


@app.post("/api/control/{repo_id}/stash")
async def control_stash(repo_id: str, payload: ControlStashBody):
    """Set uncommitted bench changes aside and reconcile back to the applied node.

    Single-slot: refuses if a stash already exists. Physically moves the bench
    to the clean applied configuration (a reconcile plan), then records the
    snapshot so it can be popped later.
    """
    _assert_lab_idle_for_control()
    mgr = _get_control_manager(repo_id)
    runtime = lab.get_lab_state()
    owns_bench = _repo_owns_bench(repo_id)
    working = mgr.working_state(runtime, owns_bench=owns_bench)

    if mgr.get_stash() is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "stash_exists",
                "message": "A stash already exists. Pop or drop it first.",
            },
        )
    applied = working.get("applied") or {}
    applied_id = applied.get("configuration_id")

    from lab_model.coordinator.state.projections import (
        EMPTY_CONFIGURATION,
        extract_configuration,
        extract_configuration_metadata,
    )

    # Stash base: the applied node when this repo owns the bench, otherwise the
    # shared empty state (a foreign bench is uncommitted work on top of empty,
    # so stashing clears the table back to empty â€” every part returns to
    # storage â€” leaving a clean slate to check out this repo's nodes).
    if applied_id:
        try:
            base_cfg = mgr.get_configuration(applied_id).get("configuration") or {}
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    else:
        base_cfg = EMPTY_CONFIGURATION

    if payload.finalize:
        # Step-by-step stash: the frontend already drove the primitives back to
        # base, so the runtime is clean now (dirty check would wrongly reject).
        # The dirty snapshot was captured at preview time and passed back here.
        snapshot = payload.snapshot or {}
        if not snapshot:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "missing_snapshot",
                    "message": "finalize requires the snapshot captured at preview time.",
                },
            )
        runtime_mgr = _lab_runtime_manager()
        runtime_mgr.apply_hard_checkout_projection(base_cfg, source="stash")
        mgr.set_applied(applied_id, branch=applied.get("branch"))
        mgr.save_stash(
            snapshot,
            base_configuration_id=applied_id,
            base_branch=applied.get("branch"),
            message=payload.message,
            metadata=payload.metadata if isinstance(payload.metadata, dict) else None,
        )
        _claim_bench(repo_id, applied_id)
        if hasattr(lab, "_persist_state"):
            lab._persist_state()
        return {
            "status": "ok",
            "finalized": True,
            "stash": mgr.stash_summary(),
            "steps_executed": 0,
            "working": mgr.working_state(
                lab.get_lab_state(), owns_bench=_repo_owns_bench(repo_id)
            ),
        }

    if not working.get("dirty"):
        raise HTTPException(
            status_code=409,
            detail={"code": "nothing_to_stash", "message": "No uncommitted changes to stash."},
        )

    snapshot = extract_configuration(runtime)
    snapshot_metadata = extract_configuration_metadata(runtime)
    plan = mgr.plan_runtime_to_configuration(runtime, base_cfg)

    if payload.preview:
        return {
            "status": "planned",
            "plan": plan,
            "steps": len(plan),
            "base_configuration_id": applied_id,
            "base_branch": applied.get("branch"),
            "snapshot": snapshot,
            "metadata": snapshot_metadata,
        }

    from lab_model.coordinator.state.reconcile_executor import (
        ReconcilePlanError,
        execute_reconcile_plan,
    )

    runtime_mgr = _lab_runtime_manager()
    try:
        if plan:
            await execute_reconcile_plan(lab, plan, source="stash")
    except ReconcilePlanError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=409, detail=f"Stash failed: {exc}") from exc

    runtime_mgr.apply_hard_checkout_projection(base_cfg, source="stash")
    mgr.set_applied(applied_id, branch=applied.get("branch"))
    stash = mgr.save_stash(
        snapshot,
        base_configuration_id=applied_id,
        base_branch=applied.get("branch"),
        message=payload.message,
        metadata=snapshot_metadata or None,
    )
    # The bench is now clean at this repo's base, so this repo owns it.
    _claim_bench(repo_id, applied_id)
    if hasattr(lab, "_persist_state"):
        lab._persist_state()
    return {
        "status": "ok",
        "stash": mgr.stash_summary(),
        "steps_executed": len(plan),
        "working": mgr.working_state(
            lab.get_lab_state(), owns_bench=_repo_owns_bench(repo_id)
        ),
    }


@app.post("/api/control/{repo_id}/stash/pop")
async def control_stash_pop(repo_id: str, payload: ControlStashBody = ControlStashBody()):
    """Reconcile the bench to the stashed snapshot and restore it as uncommitted.

    Pop is only allowed on a clean HEAD: the snapshot lands as uncommitted
    changes, and uncommitted changes can only ever live on HEAD.
    """
    _assert_lab_idle_for_control()
    mgr = _get_control_manager(repo_id)
    runtime = lab.get_lab_state()
    working = mgr.working_state(runtime, owns_bench=_repo_owns_bench(repo_id))

    stash = mgr.get_stash()
    if stash is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "no_stash", "message": "No stash to pop."},
        )
    snapshot = stash.get("configuration") or {}
    stash_metadata = stash.get("metadata") or {}

    if payload.finalize:
        # Step-by-step pop: the frontend already drove the primitives to restore
        # the stash snapshot on the bench (which is dirty-vs-applied by design â€”
        # so the pre-pop dirty guard would wrongly reject). Record only: snap
        # tunables to the snapshot and clear the stash slot. No motion.
        runtime_mgr = _lab_runtime_manager()
        runtime_mgr.apply_configuration_projection(
            snapshot,
            source="stash_pop",
            metadata=stash_metadata if isinstance(stash_metadata, dict) else None,
        )
        mgr.clear_stash()
        if hasattr(lab, "_persist_state"):
            lab._persist_state()
        return {
            "status": "ok",
            "finalized": True,
            "steps_executed": 0,
            "working": mgr.working_state(
                lab.get_lab_state(), owns_bench=_repo_owns_bench(repo_id)
            ),
        }

    if working.get("viewing"):
        raise HTTPException(
            status_code=409,
            detail={"code": "viewing_preview", "message": "Return to the bench before popping the stash."},
        )
    if working.get("detached"):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "detached_head",
                "message": "Fork a branch before popping the stash (changes can only land on HEAD).",
            },
        )
    if working.get("dirty"):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "dirty_working_table",
                "message": "Commit or drop your current changes before popping the stash.",
            },
        )

    plan = mgr.plan_runtime_to_configuration(runtime, snapshot)

    if payload.preview:
        return {
            "status": "planned",
            "plan": plan,
            "steps": len(plan),
        }

    from lab_model.coordinator.state.reconcile_executor import (
        ReconcilePlanError,
        execute_reconcile_plan,
    )

    runtime_mgr = _lab_runtime_manager()
    try:
        if plan:
            await execute_reconcile_plan(lab, plan, source="stash_pop")
    except ReconcilePlanError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=409, detail=f"Stash pop failed: {exc}") from exc

    runtime_mgr.apply_configuration_projection(
        snapshot,
        source="stash_pop",
        metadata=stash_metadata if isinstance(stash_metadata, dict) else None,
    )
    mgr.clear_stash()
    if hasattr(lab, "_persist_state"):
        lab._persist_state()
    return {
        "status": "ok",
        "steps_executed": len(plan),
        "working": mgr.working_state(
            lab.get_lab_state(), owns_bench=_repo_owns_bench(repo_id)
        ),
    }


@app.delete("/api/control/{repo_id}/stash")
async def control_stash_drop(repo_id: str):
    """Discard the stash without touching the bench (metadata only)."""
    mgr = _get_control_manager(repo_id)
    had_stash = mgr.get_stash() is not None
    mgr.clear_stash()
    return {"status": "ok", "dropped": had_stash}


@app.post("/api/control/{repo_id}/observations")
async def control_pin_observations(repo_id: str, payload: ControlObservationsBody):
    _assert_lab_idle_for_control()
    runtime = lab.get_lab_state()
    try:
        pin = _get_control_manager(repo_id).pin_observations(
            runtime,
            configuration_id=payload.configuration_id,
            message=payload.message,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "ok", "observations": pin}


@app.post("/api/control/{repo_id}/setups")
async def control_save_setup(repo_id: str, payload: ControlSetupBody):
    _assert_lab_idle_for_control()
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    mgr = _get_control_manager(repo_id)
    runtime = lab.get_lab_state()
    if payload.include_observations:
        setup = mgr.build_setup_from_runtime(
            runtime,
            name=name,
            configuration_id=payload.configuration_id,
            message=payload.message,
        )
    else:
        if payload.configuration_id:
            setup = mgr.save_setup(
                name=name,
                configuration_id=payload.configuration_id,
                message=payload.message,
            )
        else:
            commit = mgr.commit_from_runtime(
                runtime,
                message=payload.message or f"setup {name}",
            )
            setup = mgr.save_setup(
                name=name,
                configuration_id=str(commit["id"]),
                message=payload.message,
            )
    return {"status": "ok", "setup": setup}


def _lab_component_wh(tag_id: str) -> Tuple[float, float]:
    """Catalog width/height in mm for layout analysis (mock vs real)."""
    if lab is None:
        return (90.0, 90.0)
    if hasattr(lab, "_get_component_wh"):
        return lab._get_component_wh(tag_id)
    if hasattr(lab, "_catalog_wh"):
        return lab._catalog_wh(tag_id)
    return (90.0, 90.0)


@app.get("/api/layout-conflicts")
async def get_layout_conflicts():
    """Semantic vs geometry issues for inventory modals (PLACED in Q3, STORED off-slot, etc.)."""
    if lab is None:
        raise HTTPException(status_code=503, detail="Lab not initialized")
    from lab_model.language.domain.storage_region import analyze_layout_issues

    state = lab.get_lab_state()
    comps = state.get("components") or {}
    stored_intent = None
    getter = getattr(lab, "get_stored_intent_for_layout", None)
    if callable(getter):
        stored_intent = getter()
    issues = analyze_layout_issues(comps, _lab_component_wh, stored_intent=stored_intent)
    return {"issues": issues}


@app.get("/api/storage-grid")
async def get_storage_grid():
    """Inventory grid dimensions for canvas overlay (must match storage_region constants)."""
    from lab_model.language.domain.storage_region import storage_grid_spec

    return storage_grid_spec()


@app.get("/api/lab-layout")
async def get_lab_layout():
    """Breadboard/table bounds + storage grid overlay (single source matching ``layout.json``)."""
    from lab_model.language.domain.storage_region import storage_grid_spec

    rt = require_backend(backend_registry, _active_backend_id(), init=False)
    with BackendSession(rt):
        doc = load_layout_document()
        enriched = dict(doc)
        paths = rt.paths
        manifest = rt.manifest
        enriched["lab_view_root"] = paths.root_dir
        enriched["storage_grid"] = storage_grid_spec()
        enriched["lab_manifest"] = manifest.as_dict()
        enriched["lab_mode"] = manifest.lab_mode
        enriched["communicator"] = manifest.communicator
        enriched["backend_id"] = rt.backend_id
        return enriched


@app.get("/api/component-library")
async def get_component_library():
    """Full component definitions (human-edited); broader than GET /api/catalog."""
    path = get_lab_view_paths().component_library_json
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


GUIDE_MIN_LENGTH_MM = 2.0


def _runtime_laser_doc() -> Dict[str, Any]:
    """Live laser overlay slice from the runtime.

    Laser lines are now *versioned configuration* carried on the runtime
    (seeded once from ``laser_lines.json`` by the communicator). Editing them
    makes the bench dirty like any other configuration change, so reads/writes
    flow through the runtime rather than the bundle file.
    """
    state = lab.get_lab_state() if lab is not None else {}
    doc = state.get("laser_lines")
    if not isinstance(doc, dict):
        doc = {"snap_line_id": None, "lines": []}
    return {
        "version": int(doc.get("version") or 1),
        "snap_line_id": doc.get("snap_line_id"),
        "lines": doc.get("lines") or [],
    }


def _laser_lines_response() -> Dict[str, Any]:
    out = _runtime_laser_doc()
    out["lab_mode"] = _lab_mode_for_active()
    out["schema_file"] = os.path.basename(_runtime_for_active().paths.laser_lines_json)
    return out


@app.get("/api/laser-line")
async def get_laser_line():
    """Single-line legacy coefficients (x = a*y + b, mm) from the runtime snap line."""
    return laser_line_coeffs_from_doc(_runtime_laser_doc(), _lab_mode_for_active())


@app.get("/api/laser-lines")
async def get_laser_lines():
    """All laser overlays carried on the live runtime (versioned configuration)."""
    return _laser_lines_response()


@app.patch("/api/laser-lines/{line_id}")
async def patch_laser_line(line_id: str, payload: Dict[str, Any] = Body(...)):
    """Update one line: ``enabled`` anytime; ``p1``/``p2`` only with ``confirm: true``.

    Mutates the runtime overlay (marks the bench dirty); ``laser_lines.json`` is
    only the initial seed and is no longer written here.
    """
    if not line_id_pattern().match(line_id or ""):
        raise HTTPException(status_code=400, detail="Invalid line id")
    doc = _runtime_laser_doc()
    lines = doc.get("lines") or []
    idx = next(
        (i for i, ln in enumerate(lines) if isinstance(ln, dict) and ln.get("id") == line_id),
        None,
    )
    if idx is None:
        raise HTTPException(status_code=404, detail=f"Unknown laser line: {line_id}")

    updates: Dict[str, Any] = {}
    wants_geo = any(k in payload for k in ("p1", "p2"))
    if wants_geo:
        if payload.get("confirm") is not True:
            raise HTTPException(
                status_code=400,
                detail="Set confirm: true to apply p1/p2 geometry changes.",
            )
        p1 = payload.get("p1")
        p2 = payload.get("p2")
        if not isinstance(p1, dict) or not isinstance(p2, dict):
            raise HTTPException(status_code=400, detail="p1 and p2 must be objects with numeric x, y")
        try:
            p1f = {"x": float(p1["x"]), "y": float(p1["y"])}
            p2f = {"x": float(p2["x"]), "y": float(p2["y"])}
        except (KeyError, TypeError, ValueError):
            raise HTTPException(status_code=400, detail="p1 and p2 require numeric x and y")
        if not two_points_define_line(p1f, p2f):
            raise HTTPException(
                status_code=422,
                detail="Invalid geometry: p1 and p2 must be two distinct points.",
            )
        updates["p1"] = p1f
        updates["p2"] = p2f

    if "enabled" in payload:
        en = payload["enabled"]
        if not isinstance(en, bool):
            raise HTTPException(status_code=400, detail="enabled must be a boolean")
        updates["enabled"] = en

    if "name" in payload and isinstance(payload["name"], str) and payload["name"].strip():
        updates["name"] = payload["name"].strip()[:120]

    if "color" in payload and isinstance(payload["color"], str) and payload["color"].strip():
        col = payload["color"].strip()
        if len(col) > 32:
            raise HTTPException(status_code=400, detail="color string too long")
        updates["color"] = col

    from lab_model.coordinator.state.runtime_manager import MutationKind

    def _mut(state: Dict[str, Any]) -> None:
        ll = state.setdefault("laser_lines", {"snap_line_id": None, "lines": []})
        for ln in ll.setdefault("lines", []):
            if isinstance(ln, dict) and ln.get("id") == line_id:
                ln.update(updates)
                break

    _lab_runtime_manager().mutate(
        _mut, kind=MutationKind.RECOVERY_PATCH, source=f"laser_patch:{line_id}"
    )
    if hasattr(lab, "_persist_state"):
        lab._persist_state()
    return _laser_lines_response()


# ---- Alignment guides (versioned pencil overlays) --------------------------


def _parse_guide_points(payload: Dict[str, Any]) -> Tuple[Dict[str, float], Dict[str, float]]:
    p1 = payload.get("p1")
    p2 = payload.get("p2")
    if not isinstance(p1, dict) or not isinstance(p2, dict):
        raise HTTPException(status_code=400, detail="p1 and p2 must be objects with numeric x, y")
    try:
        p1f = {"x": float(p1["x"]), "y": float(p1["y"])}
        p2f = {"x": float(p2["x"]), "y": float(p2["y"])}
    except (KeyError, TypeError, ValueError):
        raise HTTPException(status_code=400, detail="p1 and p2 require numeric x and y")
    if math.hypot(p2f["x"] - p1f["x"], p2f["y"] - p1f["y"]) < GUIDE_MIN_LENGTH_MM:
        raise HTTPException(status_code=422, detail="Guide is too short to register.")
    return p1f, p2f


def _runtime_guides() -> List[Dict[str, Any]]:
    state = lab.get_lab_state() if lab is not None else {}
    guides = state.get("alignment_guides")
    return guides if isinstance(guides, list) else []


def _mutate_guides(fn, *, source: str) -> None:
    from lab_model.coordinator.state.runtime_manager import MutationKind

    def _mut(state: Dict[str, Any]) -> None:
        guides = state.get("alignment_guides")
        if not isinstance(guides, list):
            guides = []
            state["alignment_guides"] = guides
        fn(guides)

    _lab_runtime_manager().mutate(_mut, kind=MutationKind.RECOVERY_PATCH, source=source)
    if hasattr(lab, "_persist_state"):
        lab._persist_state()


@app.get("/api/guides")
async def get_guides():
    return {"guides": _runtime_guides()}


@app.post("/api/guides")
async def add_guide(payload: Dict[str, Any] = Body(...)):
    p1f, p2f = _parse_guide_points(payload)
    guide = {"id": f"g_{uuid.uuid4().hex[:12]}", "p1": p1f, "p2": p2f}
    _mutate_guides(lambda g: g.append(guide), source="guide_add")
    return {"guide": guide, "guides": _runtime_guides()}


@app.patch("/api/guides/{guide_id}")
async def move_guide(guide_id: str, payload: Dict[str, Any] = Body(...)):
    p1f, p2f = _parse_guide_points(payload)
    found = any(
        isinstance(g, dict) and str(g.get("id")) == guide_id for g in _runtime_guides()
    )
    if not found:
        raise HTTPException(status_code=404, detail=f"Unknown guide: {guide_id}")

    def _apply(guides: List[Dict[str, Any]]) -> None:
        for g in guides:
            if isinstance(g, dict) and str(g.get("id")) == guide_id:
                g["p1"] = p1f
                g["p2"] = p2f
                break

    _mutate_guides(_apply, source=f"guide_move:{guide_id}")
    return {"guides": _runtime_guides()}


@app.delete("/api/guides/{guide_id}")
async def delete_guide(guide_id: str):
    found = any(
        isinstance(g, dict) and str(g.get("id")) == guide_id for g in _runtime_guides()
    )
    if not found:
        raise HTTPException(status_code=404, detail=f"Unknown guide: {guide_id}")

    def _apply(guides: List[Dict[str, Any]]) -> None:
        guides[:] = [
            g for g in guides if not (isinstance(g, dict) and str(g.get("id")) == guide_id)
        ]

    _mutate_guides(_apply, source=f"guide_delete:{guide_id}")
    return {"guides": _runtime_guides()}


@app.put("/api/guides")
async def replace_guides(payload: Dict[str, Any] = Body(...)):
    """Replace the full guide set (clear-all, or one-time localStorage import)."""
    from lab_model.coordinator.state.projections import normalize_alignment_guides

    incoming = normalize_alignment_guides(payload.get("guides"))

    def _apply(guides: List[Dict[str, Any]]) -> None:
        guides[:] = incoming

    _mutate_guides(_apply, source="guide_replace")
    return {"guides": _runtime_guides()}


@app.put("/api/overlays")
async def replace_overlays(payload: Dict[str, Any] = Body(...)):
    """Replace alignment guides and laser lines on the live bench (no motion).

    Used as the first visible step when applying a versioned configuration so
    lines match the target node before component reconcile primitives run.
    """
    from lab_model.coordinator.state.projections import (
        normalize_alignment_guides,
        normalize_laser_lines_doc,
    )
    from lab_model.coordinator.state.runtime_manager import MutationKind

    guides = normalize_alignment_guides(payload.get("alignment_guides"))
    laser = normalize_laser_lines_doc(payload.get("laser_lines") or {})

    def _mut(state: Dict[str, Any]) -> None:
        state["alignment_guides"] = guides
        state["laser_lines"] = laser

    _lab_runtime_manager().mutate(
        _mut, kind=MutationKind.PROJECTION_APPLY, source="overlay_apply"
    )
    if hasattr(lab, "_persist_state"):
        lab._persist_state()
    return {
        "alignment_guides": guides,
        "laser_lines": laser,
    }


def _enforce_ensemble_preflight(lab_comm, cmd) -> None:
    """Resolve all ensemble variable paths before accepting OPTIMIZING work."""
    if not isinstance(cmd, OptimizeBody):
        return
    params = cmd.parameters.model_dump()
    if params.get("mode") != "ensemble":
        return
    with lab_comm._state_lock:
        state = lab_comm.current_state
    is_real = not _active_backend_id().startswith("mock.")
    catalog_map = getattr(lab_comm, "catalog_map", None)
    try:
        preflight_ensemble(
            state,
            params,
            catalog_map=catalog_map,
            strict_real_objectives=is_real,
        )
    except EnsemblePreflightError as exc:
        raise HTTPException(status_code=400, detail=exc.as_dict()) from exc


def _enforce_holding_rules(cmd, state: Dict[str, Any]) -> None:
    """
    Reject commands that would be unsafe given the current HOLDING state.

    See ``new_primitives.md`` Â§6. BUSY/OPTIMIZING are already rejected upstream.
    """
    status = state.get("system_status") or "IDLE"
    holding = state.get("holding") or {}
    held = holding.get("tag_id") if isinstance(holding, dict) else None
    unconfirmed = bool(holding.get("requires_operator_confirm")) if isinstance(holding, dict) else False

    if unconfirmed:
        # Only CONFIRM_HOLDING_TAG is allowed when the gripper-closed boot flag is set.
        if not isinstance(cmd, ConfirmHoldingTagBody):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Gripper reports closed but held tag is unconfirmed. "
                    "Confirm the tag in the gripper (CONFIRM_HOLDING_TAG) before sending other commands."
                ),
            )
        return

    if isinstance(cmd, PickComponentBody):
        if status == "HOLDING":
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Already HOLDING {held or '<tag>'}. "
                    "Use PLACE_FROM_HOVER or HOVER to resolve before another PICK_COMPONENT."
                ),
            )
        return

    if isinstance(cmd, (HoverBody, PlaceFromHoverBody)):
        # HOVER / PLACE_FROM_HOVER manipulate an already-held part -> must be
        # in HOLDING and must target the held tag.
        if status != "HOLDING":
            raise HTTPException(
                status_code=409,
                detail=f"{cmd.action} requires HOLDING state; current status is {status}. "
                       "Call PICK_COMPONENT first.",
            )
        if held and cmd.target_id != held:
            raise HTTPException(
                status_code=409,
                detail=f"Currently holding {held}; cannot {cmd.action} on {cmd.target_id}.",
            )
        return

    if isinstance(cmd, ScanRotateInPlaceBody):
        # SCAN_ROTATE_IN_PLACE has the SAME user intent in both cases ("sweep
        # theta at constant rate") but dispatches to two different
        # ``lab_automation`` paths in RealLabCommunicator:
        #   - HOLDING  -> rotate the in-air held part (no pick/place).
        #   - IDLE     -> rotate the placed part in situ on the table.
        # In IDLE the lab backend is responsible for rejecting unsuitable
        # targets (off-table, in storage, etc.); we only enforce the
        # HOLDING tag-match rule here.
        if status == "HOLDING":
            if held and cmd.target_id != held:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Currently holding {held}; cannot {cmd.action} on {cmd.target_id}. "
                        "Place or hover the held part first."
                    ),
                )
        elif status != "IDLE":
            raise HTTPException(
                status_code=409,
                detail=f"{cmd.action} requires IDLE or HOLDING; current status is {status}.",
            )
        return

    if isinstance(cmd, ConfirmHoldingTagBody):
        if status != "HOLDING":
            raise HTTPException(
                status_code=409,
                detail="CONFIRM_HOLDING_TAG only valid while system_status is HOLDING.",
            )
        return

    # Everything else is a "normal" command and must not run while HOLDING.
    if status == "HOLDING":
        raise HTTPException(
            status_code=409,
            detail=(
                f"Cannot run {getattr(cmd, 'action', type(cmd).__name__)} while HOLDING "
                f"(held tag: {held or '<unknown>'}). Use PLACE_FROM_HOVER first."
            ),
        )


@app.get("/api/backends")
async def list_backends():
    """Catalog of registered backends with lease/queue status (no backend_id required)."""
    rows = []
    for spec in backend_registry.list_specs():
        rt = backend_registry.get_runtime(spec.backend_id, init=False)
        row = backend_registry.to_api_row(
            rt,
            lease_manager=session_lease_manager,
            job_hub=job_hub,
        )
        active_job = row.get("active_job_id")
        if active_job:
            found = job_hub.find_job(active_job)
            if found:
                _, mgr = found
                try:
                    row["active_job"] = mgr.get(active_job).to_api_dict()
                except JobNotFoundError:
                    row["active_job"] = None
        rows.append(row)
    return {"backends": rows, "schema_version": 1, "policy": _coordinator_policy()}


@app.get("/api/optimization/metrics")
async def list_optimization_metrics():
    """Registered ensemble objective metric ids (Phase E)."""
    return {"metrics": sorted(METRIC_REGISTRY.keys())}


@app.get("/api/kernels")
async def list_edge_kernels(request: Request, backend: Optional[str] = Query(None)):
    """Registered edge kernels for closed-loop jobs (catalog + optional session)."""
    backend_id = _active_backend_id()
    lease_id = _extract_lease_id({}, request)
    if lease_id:
        from lab_model.execution.optimization.kernels import session_store

        session_store.activate_lease_roots(backend_id, lease_id)
    try:
        from lab_model.coordinator.backends.lab_view_config import get_lab_view_paths_optional

        paths = get_lab_view_paths_optional()
        lv = str(paths.root_dir) if paths is not None else None
    except Exception:
        lv = None
    rows = list_kernels(backend=backend, lab_view_path=lv)
    return {
        "kernels": [k.to_api_dict() for k in rows],
        "backend_id": backend_id,
    }


@app.post("/api/kernels/session")
async def register_session_kernel(request: Request, body: Dict[str, Any] = Body(...)):
    """Register a TorchScript package under the active lease (session-scoped)."""
    import base64

    from lab_model.execution.optimization.kernels import session_store

    backend_id = str(body.get("backend_id") or _active_backend_id()).strip()
    lease_id = _extract_lease_id(body, request)
    if not lease_id:
        raise HTTPException(status_code=400, detail="X-CloudLabs-Lease required")
    try:
        session_lease_manager.validate_command_lease(
            backend_id=backend_id,
            lease_id=lease_id,
            require_when_locked=True,
        )
    except (LeaseConflictError, LeaseNotFoundError, LeaseExpiredError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    name = str(body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    b64 = body.get("artifact_b64") or body.get("artifact_base64")
    if not b64:
        raise HTTPException(status_code=400, detail="artifact_b64 is required")
    try:
        artifact = base64.b64decode(str(b64), validate=False)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"invalid artifact_b64: {exc}") from exc

    feature_names = body.get("feature_names")
    if feature_names is not None and not isinstance(feature_names, list):
        raise HTTPException(status_code=400, detail="feature_names must be a list")

    try:
        entry = session_store.register_package(
            backend_id=backend_id,
            lease_id=lease_id,
            name=name,
            artifact_bytes=artifact,
            label=str(body.get("label") or name),
            description=str(body.get("description") or ""),
            output_kind=str(body.get("output_kind") or "scalar"),
            feature_names=feature_names,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {"kernel": entry, "backend_id": backend_id, "lease_id": lease_id}


@app.get("/api/kernels/session")
async def list_session_kernels(request: Request, backend_id: Optional[str] = Query(None)):
    """List session packages for the active lease."""
    from lab_model.execution.optimization.kernels import session_store

    bid = str(backend_id or _active_backend_id()).strip()
    lease_id = _extract_lease_id({}, request)
    if not lease_id:
        raise HTTPException(status_code=400, detail="X-CloudLabs-Lease required")
    rows = session_store.list_lease_packages(bid, lease_id)
    return {"kernels": rows, "backend_id": bid, "lease_id": lease_id}


@app.delete("/api/kernels/session/{kernel_id}")
async def delete_session_kernel(
    kernel_id: str,
    request: Request,
    backend_id: Optional[str] = Query(None),
):
    """Delete one session package from the active lease."""
    from lab_model.execution.optimization.kernels import session_store

    bid = str(backend_id or _active_backend_id()).strip()
    lease_id = _extract_lease_id({}, request)
    if not lease_id:
        raise HTTPException(status_code=400, detail="X-CloudLabs-Lease required")
    ok = session_store.delete_package(bid, lease_id, kernel_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"session kernel {kernel_id!r} not found")
    return {"status": "deleted", "kernel_id": kernel_id}


@app.get("/api/kernels/session/{kernel_id}/artifact")
async def download_session_kernel_artifact(
    kernel_id: str,
    request: Request,
    backend_id: Optional[str] = Query(None),
):
    """Download session kernel ``.pt`` bytes (base64) for local authoring-time eval."""
    import base64

    from lab_model.execution.optimization.kernels import session_store

    bid = str(backend_id or _active_backend_id()).strip()
    lease_id = _extract_lease_id({}, request)
    if not lease_id:
        raise HTTPException(status_code=400, detail="X-CloudLabs-Lease required")
    try:
        data = session_store.read_artifact_bytes(bid, lease_id, kernel_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    rows = {
        r["id"]: r for r in session_store.list_lease_packages(bid, lease_id)
    }
    meta = rows.get(kernel_id) or {}
    return {
        "kernel_id": kernel_id,
        "artifact_b64": base64.b64encode(data).decode("ascii"),
        "digest": meta.get("digest"),
        "output_kind": meta.get("output_kind") or "scalar",
        "feature_names": meta.get("feature_names") or [],
    }


@app.post("/api/kernels/eval")
async def eval_kernel_on_edge(request: Request, body: Dict[str, Any] = Body(...)):
    """Compat shim: authoring probe via EVAL_KERNEL primitive (lease required).

    Prefer ``POST /api/command`` with ``action: EVAL_KERNEL``. This route
    remains so older SDK clients keep working; it builds the same primitive
    envelope (edge uses ``kind=primitive``, not a parallel ``kernel_eval``).
    """
    backend_id = str(body.get("backend_id") or _active_backend_id()).strip()
    lease_id = _extract_lease_id(body, request)
    if not lease_id:
        raise HTTPException(status_code=400, detail="X-CloudLabs-Lease required")
    try:
        session_lease_manager.validate_command_lease(
            backend_id=backend_id,
            lease_id=lease_id,
            require_when_locked=True,
        )
    except (LeaseConflictError, LeaseNotFoundError, LeaseExpiredError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    kernel_id = str(body.get("kernel_id") or "").strip()
    tag_id = str(body.get("tag_id") or "").strip()
    field = str(body.get("field") or "camera_image").strip() or "camera_image"
    if not kernel_id or not tag_id:
        raise HTTPException(status_code=400, detail="kernel_id and tag_id required")

    command = {
        "action": "EVAL_KERNEL",
        "target_id": tag_id,
        "parameters": {
            "kernel_id": kernel_id,
            "field": field,
            "lease_id": lease_id,
        },
        "lease_id": lease_id,
    }

    try:
        parse_command_payload(command)
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=validation_error_detail(e))

    # Phase 3: always EVAL_KERNEL via EdgeClient (poll / HTTP / in-process).
    # No southbound ``/kernel/probe`` â€” execute-only.
    client = resolve_edge_client(
        backend_id,
        lab=None,
        edge_config=edge_config_for_backend(backend_registry, backend_id),
    )
    if client.transport == EdgeTransport.IN_PROCESS:
        rt = require_backend(backend_registry, backend_id, init=True)
        if rt.lab is None:
            raise HTTPException(status_code=503, detail="lab not initialized")
        client = resolve_edge_client(
            backend_id,
            lab=rt.lab,
            edge_config=edge_config_for_backend(backend_registry, backend_id),
        )
        try:
            with BackendSession(rt):
                lab_inst = rt.lab
                lab_inst._command_lease_id = lease_id
                lab_inst._command_backend_id = backend_id
                edge_result = await client.execute_command(
                    command, timeout_s=60.0, lease_id=lease_id
                )
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    else:
        edge_result = await client.execute_command(
            command, timeout_s=60.0, lease_id=lease_id
        )

    if not edge_result.ok:
        raise HTTPException(
            status_code=400 if client.transport == EdgeTransport.IN_PROCESS else 502,
            detail=edge_result.error or "EVAL_KERNEL failed",
        )
    result = edge_result.result if isinstance(edge_result.result, dict) else {}
    if not result and edge_result.epoch_ms is None:
        raise HTTPException(status_code=500, detail="EVAL_KERNEL returned no result")
    out = {k: v for k, v in result.items() if k != "status"}
    if edge_result.epoch_ms is not None:
        out.setdefault("epoch_ms", edge_result.epoch_ms)
    if edge_result.latch_quality:
        out.setdefault("latch_quality", edge_result.latch_quality)
    out["edge_transport"] = edge_result.transport.value
    return out


@app.post("/api/optimization/compile")
async def optimization_compile(body: Dict[str, Any] = Body(...)):
    """
    Compile declarative objective graph â†’ runtime ``ObjectiveSpec`` JSON.

    Optional ``preflight: true`` validates compiled sources against live bench state.
    Optional ``parameters`` merges compiled objective into an ensemble payload and
    runs full ensemble preflight when ``preflight`` is set.
    """
    objective_in = body.get("graph") or body.get("objective")
    if objective_in is None and isinstance(body.get("parameters"), dict):
        params_obj = body["parameters"]
        objective_in = params_obj.get("objective")
    if objective_in is None:
        raise HTTPException(
            status_code=400,
            detail="graph, objective, or parameters.objective required",
        )

    try:
        compiled = compile_objective_payload(objective_in)
    except EnsemblePreflightError as exc:
        raise HTTPException(status_code=400, detail=exc.as_dict()) from exc

    result: Dict[str, Any] = {"ok": True, "objective": compiled}
    do_preflight = bool(body.get("preflight", False))

    if do_preflight:
        if lab is None:
            result["preflight"] = {
                "ok": False,
                "message": "lab not initialized",
            }
            result["ok"] = False
        else:
            with lab._state_lock:
                state = lab.current_state
            is_real = not _active_backend_id().startswith("mock.")
            catalog_map = getattr(lab, "catalog_map", None)
            try:
                if isinstance(body.get("parameters"), dict):
                    params = dict(body["parameters"])
                    params["objective"] = compiled
                    spec, _, x0 = preflight_ensemble(
                        state,
                        params,
                        catalog_map=catalog_map,
                        strict_real_objectives=is_real,
                    )
                    result["parameters"] = spec.model_dump()
                    result["x0"] = x0
                    result["preflight"] = {"ok": True}
                else:
                    preflight_objective_sources(
                        state,
                        ObjectiveSpec.model_validate(compiled),
                        catalog_map=catalog_map,
                        strict_real_objectives=is_real,
                    )
                    result["preflight"] = {"ok": True}
            except EnsemblePreflightError as exc:
                result["preflight"] = exc.as_dict()
                result["ok"] = False

    return result


@app.get("/api/optimization/capabilities")
async def optimization_capabilities():
    """Preferred optimize path + legacy deprecation flags (Step E)."""
    redirect = (os.environ.get("CLOUDLABS_LEGACY_OPTIMIZE_REDIRECT") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    return {
        "preferred_mode": "ensemble",
        "legacy_strategy_deprecated": True,
        "closed_loop_requires_ensemble": True,
        "legacy_redirect_enabled": redirect,
        "legacy_redirect_env": "CLOUDLABS_LEGACY_OPTIMIZE_REDIRECT",
        "sdk_entrypoints": ["run_optimize", "run_cobyla"],
        "ui_entrypoints": ["Twin Optimization â†’ Alignment session", "Operations job monitor"],
        "notes": (
            "Legacy NEWTON/COBYLA via component panel or command console remain for "
            "real-bench MJPEG/place-UI parity; new work should use ensemble jobs."
        ),
    }


@app.get("/api/jobs")
async def list_jobs(
    limit: int = Query(50, ge=1, le=200),
    status: Optional[str] = Query(None),
    backend_id: Optional[str] = Query(None),
):
    """List recent jobs (newest first), optionally filtered by backend."""
    allowed = {"queued", "running", "succeeded", "failed", "cancelled"}
    if status is not None and status not in allowed:
        raise HTTPException(status_code=400, detail=f"invalid status filter: {status!r}")
    if backend_id:
        mgr = job_hub.for_backend(backend_id.strip())
        records = mgr.list_jobs(limit=limit, status=status)  # type: ignore[arg-type]
        active = mgr.active_job_id()
    else:
        records = job_hub.list_all_jobs(limit=limit, status=status)
        active = None
    return {
        "jobs": [r.to_api_dict() for r in records],
        "active_job_id": active,
        "backend_id": backend_id,
    }


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    """Job status + progress telemetry."""
    found = job_hub.find_job(job_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"job {job_id!r} not found")
    _, mgr = found
    return mgr.get(job_id).to_api_dict()


@app.post("/api/jobs/submit")
async def submit_job(request: Request, body: Dict[str, Any] = Body(...)):
    """Submit a job (closed-loop OPTIMIZE or compiled DAG steps)."""
    mode = str(body.get("mode") or "").strip().lower()
    backend_id = str(body.get("backend_id") or "").strip()
    holder = str(body.get("holder") or "").strip()

    if not backend_id:
        raise HTTPException(status_code=400, detail="backend_id is required in job submit body")
    require_backend(backend_registry, backend_id, init=False)

    if mode not in {"closed_loop", "compiled_dag"}:
        raise HTTPException(
            status_code=400,
            detail="mode must be closed_loop or compiled_dag (imperative uses SDK lease directly)",
        )

    lease_id = _extract_lease_id(body, request)
    known_session: set = set()
    inline_packages = body.get("kernel_packages")
    if isinstance(inline_packages, list):
        for row in inline_packages:
            if isinstance(row, dict):
                kid = str(row.get("kernel_id") or row.get("id") or "").strip()
                if kid.startswith("session."):
                    known_session.add(kid)
    if lease_id:
        from lab_model.execution.optimization.kernels import session_store

        session_store.activate_lease_roots(backend_id, lease_id)
        for row in session_store.list_lease_packages(backend_id, lease_id):
            known_session.add(str(row["id"]))

    try:
        spec = validate_submit_spec(
            mode,
            body,
            known_session_ids=known_session or None,
        )  # type: ignore[arg-type]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    snapshot_ref = parse_snapshot_ref(body.get("snapshot"))
    mgr = job_hub.for_backend(backend_id)
    record = mgr.submit(
        backend_id=backend_id,
        mode=mode,  # type: ignore[arg-type]
        holder=holder or f"api:{uuid.uuid4().hex[:8]}",
        spec=spec,
        snapshot_ref=snapshot_ref,
    )

    # Stage session packages into job scratch.
    # Prefer inline kernel_packages (client exported before releasing lease) so
    # the job runner can acquire the backend without racing the imperative lease.
    inline_packages = body.get("kernel_packages")
    session_ids = list(spec.get("session_kernel_ids") or [])
    if not session_ids:
        session_ids = [
            k for k in (spec.get("kernels") or []) if str(k).startswith("session.")
        ]
    if inline_packages or session_ids:
        from lab_model.execution.optimization.kernels import session_store

        try:
            if isinstance(inline_packages, list) and inline_packages:
                audit = session_store.stage_packages_from_payload(
                    backend_id=backend_id,
                    job_id=record.job_id,
                    packages=inline_packages,
                )
            else:
                if not lease_id:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            "session kernels require kernel_packages in the submit body "
                            "or X-CloudLabs-Lease so packages can be staged"
                        ),
                    )
                audit = session_store.stage_packages_for_job(
                    backend_id=backend_id,
                    lease_id=lease_id,
                    job_id=record.job_id,
                    kernel_ids=session_ids,
                )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        record.spec["kernel_packages"] = audit
        mgr.update_progress(record.job_id, {"kernel_audit": audit})

    logger.info(
        "job submitted id=%s backend=%s mode=%s holder=%s",
        record.job_id,
        backend_id,
        mode,
        record.holder,
    )
    _kick_job_runner_for(backend_id)
    return record.to_api_dict()


@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str):
    """Cancel a queued or running job (best-effort for running)."""
    found = job_hub.find_job(job_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"job {job_id!r} not found")
    backend_id, mgr = found
    record = mgr.request_cancel(job_id)
    _kick_job_runner_for(backend_id)
    return record.to_api_dict()


@app.get("/api/catalog/pins")
async def list_catalog_pins():
    """Remote approved configuration pins (read-only catalog store)."""
    return {
        "pins": _catalog_pins_store().list_pins(),
        "backend_id": _active_backend_id(),
    }


@app.get("/api/catalog/publish-requests")
async def list_publish_requests(status: Optional[str] = Query(None)):
    """List publish requests (pending by default when status=pending)."""
    filter_status = status if status is not None else None
    rows = _catalog_pins_store().list_publish_requests(status=filter_status)
    return {"requests": rows}


@app.post("/api/catalog/publish-requests")
async def submit_publish_request(body: Dict[str, Any] = Body(...)):
    """Request promotion of a local commit to the remote catalog."""
    repo_id = str(body.get("repo_id") or "").strip()
    configuration_id = str(
        body.get("configuration_id") or body.get("commit") or ""
    ).strip()
    branch = str(body.get("branch") or "main").strip() or "main"
    message = str(body.get("message") or "").strip()
    requested_by = str(body.get("requested_by") or body.get("holder") or "api").strip()
    pin_id = body.get("pin_id") or body.get("proposed_pin_id")
    backend_id = str(body.get("backend_id") or _active_backend_id()).strip()

    try:
        record = _catalog_pins_store().submit_publish_request(
            repo_id=repo_id,
            configuration_id=configuration_id,
            branch=branch,
            message=message,
            requested_by=requested_by,
            pin_id=str(pin_id).strip() if pin_id else None,
            backend_id=backend_id,
            auto_approve=_mock_auto_approve_publish(),
            approved_by="mock:auto" if _mock_auto_approve_publish() else "owner",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return record


@app.post("/api/catalog/publish-requests/{request_id}/approve")
async def approve_publish_request(request_id: str, body: Dict[str, Any] = Body(default={})):
    """Owner approves a pending publish request (promotes to CatalogPin)."""
    approved_by = str(body.get("approved_by") or "owner").strip() or "owner"
    pin_id = body.get("pin_id")
    try:
        record = _catalog_pins_store().approve_publish_request(
            request_id,
            approved_by=approved_by,
            pin_id=str(pin_id).strip() if pin_id else None,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return record


@app.post("/api/catalog/publish-requests/{request_id}/reject")
async def reject_publish_request(request_id: str, body: Dict[str, Any] = Body(default={})):
    """Owner rejects a pending publish request."""
    rejected_by = str(body.get("rejected_by") or "owner").strip() or "owner"
    reason = str(body.get("reason") or body.get("message") or "").strip()
    try:
        record = _catalog_pins_store().reject_publish_request(
            request_id,
            rejected_by=rejected_by,
            reason=reason,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return record


@app.post("/api/edge/register")
async def edge_register(body: Dict[str, Any] = Body(...)):
    """Edge agent announces itself for a backend (outbound attach)."""
    backend_id = str(body.get("backend_id") or "").strip()
    if not backend_id:
        raise HTTPException(status_code=400, detail="backend_id required")
    require_backend(backend_registry, backend_id, init=False)
    try:
        rec = edge_agent_registry.register(
            backend_id=backend_id,
            label=str(body.get("label") or ""),
            agent_id=str(body.get("agent_id") or "").strip() or None,
            meta=body.get("meta") if isinstance(body.get("meta"), dict) else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    logger.info(
        "edge registered backend=%s agent_id=%s",
        backend_id,
        rec.agent_id,
    )
    return {"ok": True, "agent": rec.to_api_dict()}


@app.post("/api/edge/heartbeat")
async def edge_heartbeat(body: Dict[str, Any] = Body(...)):
    """Keep an edge agent registration alive; optional ``lab_state`` cache update."""
    agent_id = str(body.get("agent_id") or "").strip()
    if not agent_id:
        raise HTTPException(status_code=400, detail="agent_id required")
    lab_state = body.get("lab_state") if isinstance(body.get("lab_state"), dict) else None
    try:
        rec = edge_agent_registry.heartbeat(agent_id, lab_state=lab_state)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown agent {agent_id!r}") from exc
    return {"ok": True, "agent": rec.to_api_dict()}


@app.post("/api/edge/unregister")
async def edge_unregister(body: Dict[str, Any] = Body(...)):
    agent_id = str(body.get("agent_id") or "").strip()
    if not agent_id:
        raise HTTPException(status_code=400, detail="agent_id required")
    ok = edge_agent_registry.unregister(agent_id)
    return {"ok": ok}


@app.get("/api/edge/work")
async def edge_claim_work(
    backend_id: str = Query(...),
    agent_id: str = Query(...),
):
    """Claim the next queued job for an attached edge agent (or empty)."""
    backend_id = backend_id.strip()
    agent_id = agent_id.strip()
    rec = edge_agent_registry.get_for_backend(backend_id)
    if rec is None or rec.agent_id != agent_id:
        raise HTTPException(
            status_code=409,
            detail="edge agent not attached for this backend (register + heartbeat first)",
        )
    try:
        edge_agent_registry.heartbeat(agent_id)
    except KeyError:
        pass
    mgr = job_hub.for_backend(backend_id)
    job = mgr.claim_next_queued()
    if job is None:
        return {"job": None}
    return {"job": job.to_api_dict()}


@app.post("/api/edge/jobs/{job_id}/progress")
async def edge_job_progress(job_id: str, body: Dict[str, Any] = Body(...)):
    """Edge pushes closed-loop telemetry into the coordinator job record."""
    found = job_hub.find_job(job_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"job {job_id!r} not found")
    _, mgr = found
    patch = body.get("progress") if isinstance(body.get("progress"), dict) else body
    if not isinstance(patch, dict):
        raise HTTPException(status_code=400, detail="progress object required")
    try:
        record = mgr.update_progress(job_id, dict(patch))
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True, "job": record.to_api_dict()}


@app.post("/api/edge/jobs/{job_id}/complete")
async def edge_job_complete(job_id: str, body: Dict[str, Any] = Body(...)):
    """Edge reports terminal job status + optional result payload."""
    found = job_hub.find_job(job_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"job {job_id!r} not found")
    _, mgr = found
    status = str(body.get("status") or "").strip().lower()
    if status not in {"succeeded", "failed", "cancelled"}:
        raise HTTPException(
            status_code=400,
            detail="status must be succeeded|failed|cancelled",
        )
    result = body.get("result") if isinstance(body.get("result"), dict) else None
    error = body.get("error")
    try:
        record = mgr.complete(
            job_id,
            status=status,  # type: ignore[arg-type]
            result=result,
            error=str(error) if error else None,
        )
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "job": record.to_api_dict()}


@app.get("/api/edge/commands")
async def edge_poll_commands(
    backend_id: str = Query(...),
    agent_id: str = Query(...),
):
    """Claim the next pending imperative/eval command for an attached edge."""
    backend_id = backend_id.strip()
    agent_id = agent_id.strip()
    rec = edge_agent_registry.get_for_backend(backend_id)
    if rec is None or rec.agent_id != agent_id:
        raise HTTPException(
            status_code=409,
            detail="edge agent not attached for this backend (register + heartbeat first)",
        )
    try:
        edge_agent_registry.heartbeat(agent_id)
    except KeyError:
        pass
    cmd = edge_command_queue.poll(backend_id)
    if cmd is None:
        return {"command": None}
    return {"command": cmd.to_api_dict()}


@app.post("/api/edge/commands/{command_id}/complete")
async def edge_complete_command(command_id: str, body: Dict[str, Any] = Body(...)):
    """Edge reports result (or error) for a proxied command."""
    error = body.get("error")
    result = body.get("result") if isinstance(body.get("result"), dict) else None
    try:
        edge_command_queue.complete(
            command_id,
            result=result,
            error=str(error) if error else None,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail=f"unknown command {command_id!r}",
        ) from exc
    return {"ok": True}


@app.post("/api/jobs/lease/acquire")
async def acquire_session_lease(body: Dict[str, Any] = Body(...)):
    """Acquire an exclusive session lease on a backend (Phase B SDK)."""
    backend_id = str(body.get("backend_id") or "").strip()
    holder = str(body.get("holder") or "").strip()
    mode = str(body.get("mode") or "imperative").strip().lower()
    snapshot_ref = body.get("snapshot_ref")
    ttl_seconds = body.get("ttl_seconds")

    if not backend_id:
        raise HTTPException(status_code=400, detail="backend_id is required")
    if not holder:
        raise HTTPException(status_code=400, detail="holder is required")
    if mode not in {"imperative", "compiled_dag", "closed_loop"}:
        raise HTTPException(status_code=400, detail=f"unsupported mode: {mode!r}")

    require_backend(backend_registry, backend_id, init=False)

    try:
        record = session_lease_manager.acquire(
            backend_id=backend_id,
            holder=holder,
            mode=mode,  # type: ignore[arg-type]
            snapshot_ref=str(snapshot_ref) if snapshot_ref else None,
            ttl_seconds=int(ttl_seconds) if ttl_seconds is not None else None,
        )
    except LeaseConflictError as exc:
        return _lease_conflict_response(exc)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    logger.info(
        "session lease acquired backend=%s holder=%s lease_id=%s",
        backend_id,
        holder,
        record.lease_id,
    )
    return lease_record_to_api_dict(record)


@app.post("/api/jobs/lease/release")
async def release_session_lease(body: Dict[str, Any] = Body(...)):
    """Release a session lease (idempotent)."""
    lease_id = str(body.get("lease_id") or "").strip()
    if not lease_id:
        raise HTTPException(status_code=400, detail="lease_id is required")

    released = session_lease_manager.release(lease_id)
    if released is None:
        raise HTTPException(status_code=404, detail=f"lease {lease_id!r} not found")

    try:
        from lab_model.execution.optimization.kernels import session_store

        session_store.delete_lease_scratch(released.backend_id, released.lease_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("session kernel scratch cleanup failed: %s", exc)

    logger.info(
        "session lease released backend=%s holder=%s lease_id=%s",
        released.backend_id,
        released.holder,
        released.lease_id,
    )
    return {"status": "released", "lease": lease_record_to_api_dict(released)}


@app.post("/api/jobs/lease/heartbeat")
async def heartbeat_session_lease(body: Dict[str, Any] = Body(...)):
    """Extend a session lease TTL."""
    lease_id = str(body.get("lease_id") or "").strip()
    if not lease_id:
        raise HTTPException(status_code=400, detail="lease_id is required")
    extend_seconds = body.get("extend_seconds")

    try:
        record = session_lease_manager.heartbeat(
            lease_id,
            extend_seconds=int(extend_seconds) if extend_seconds is not None else None,
        )
    except LeaseNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except LeaseExpiredError as exc:
        raise HTTPException(status_code=410, detail=str(exc)) from exc

    return lease_record_to_api_dict(record)


@app.post("/api/command")
async def receive_command(
    payload: Dict[str, Any],
    background_tasks: BackgroundTasks,
    request: Request,
):
    print(f"Received Command: {payload}")

    backend_id = _active_backend_id()
    lease_id = _extract_lease_id(payload, request)
    require_lease = _command_lease_required()
    if require_lease and not lease_id:
        raise HTTPException(
            status_code=400,
            detail=(
                "X-CloudLabs-Lease required â€” acquire via Twin Take control "
                "or SDK connect()/acquire_lease()"
            ),
        )
    try:
        session_lease_manager.validate_command_lease(
            backend_id=backend_id,
            lease_id=lease_id,
            require_when_locked=require_lease,
        )
    except LeaseConflictError as exc:
        return _lease_conflict_response(exc)
    except LeaseNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except LeaseExpiredError as exc:
        raise HTTPException(status_code=410, detail=str(exc)) from exc

    # Phase 3: poll-attached or HTTP edge.base_url â€” same EdgeClient southbound.
    _edge_client = _edge_client_for(backend_id)
    if _edge_client.transport != EdgeTransport.IN_PROCESS:
        try:
            cmd = parse_command_payload(payload)
        except ValidationError as e:
            raise HTTPException(status_code=400, detail=validation_error_detail(e))
        edge_result = await _southbound_execute(
            payload if isinstance(payload, dict) else {"action": cmd.action},
            backend_id=backend_id,
            timeout_s=180.0,
            lease_id=lease_id,
        )
        out = edge_result.as_api_dict()
        out["edge_transport"] = edge_result.transport.value
        out.setdefault("message", f"{cmd.action} completed")
        return out

    if lab is None:
        raise HTTPException(status_code=503, detail="Lab Communicator not initialized")

    state = lab.get_lab_state()
    current_status = state.get("system_status")
    if current_status == "BUSY" or current_status == "OPTIMIZING":
        raise HTTPException(status_code=409, detail=f"System is {current_status}. Please wait.")

    try:
        cmd = parse_command_payload(payload)
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=validation_error_detail(e))

    _enforce_holding_rules(cmd, state)
    _enforce_ensemble_preflight(lab, cmd)
    if runtime_manager is not None and not runtime_manager.supports_primitive(cmd.action):
        raise HTTPException(
            status_code=409,
            detail=f"{cmd.action} is unavailable in {runtime_manager.mode} mode",
        )

    # Sync primitives that return a payload (not background-accepted).
    lab._command_lease_id = lease_id
    lab._command_backend_id = backend_id

    if isinstance(cmd, RecordMeasurablesBody):
        if runtime_manager is None:
            await execute_validated_command(lab, cmd)
            meas = fetch_read_primitive(
                lab,
                PrimitiveId.GET_MEASURABLES,
                cmd.target_id,
            )
            return {"status": "ok", "measurables": meas}
        try:
            target_lab, token = runtime_manager.reserve_operation()
        except Exception as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        try:
            target_lab._command_lease_id = lease_id
            target_lab._command_backend_id = backend_id
            await execute_validated_command(target_lab, cmd)
            meas = fetch_read_primitive(
                target_lab,
                PrimitiveId.GET_MEASURABLES,
                cmd.target_id,
            )
        finally:
            runtime_manager.release_operation(token)
        return {"status": "ok", "measurables": meas}

    if isinstance(cmd, EvalKernelBody):
        try:
            if runtime_manager is None:
                result = await execute_validated_command(lab, cmd)
            else:
                try:
                    target_lab, token = runtime_manager.reserve_operation()
                except Exception as exc:
                    raise HTTPException(status_code=409, detail=str(exc)) from exc
                try:
                    target_lab._command_lease_id = lease_id
                    target_lab._command_backend_id = backend_id
                    result = await execute_validated_command(target_lab, cmd)
                finally:
                    runtime_manager.release_operation(token)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not isinstance(result, dict):
            raise HTTPException(status_code=500, detail="EVAL_KERNEL returned no result")
        return {"status": "ok", **result}

    if runtime_manager is None:
        return schedule_validated_command(lab, cmd, background_tasks)

    try:
        target_lab, token = runtime_manager.reserve_operation()
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    reserved_tasks = _ReservedBackgroundTasks(
        background_tasks,
        runtime_manager,
        token,
    )
    try:
        response = schedule_validated_command(target_lab, cmd, reserved_tasks)
    except Exception:
        runtime_manager.release_operation(token)
        raise
    if not reserved_tasks.scheduled:
        runtime_manager.release_operation(token)
    return response

# --------------------------------------------------------------------------
# Lab-wide ``/api/table-cam/*`` HTTP surface removed in Phase 9d.
#
# Table camera capture, preview, stream, connect/disconnect, and exposure/gain
# are served per catalog tag via ``/api/components/{tag_id}/telemetry/*`` and
# component commands (``RECORD_MEASURABLES``, tunables). ``LabCommunicator``
# table_cam_* methods remain for those routes.
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# Cobyla reference image â€” HTTP surface removed in Phase 9a.
#
# The four ``/api/cobyla-reference-image*`` routes (GET, POST, GET /status,
# DELETE) were the legacy side-channel for the COBYLA strategy's reference
# ndarray. They were deleted per :doc:`universal_component_architecture` Â§9a.
#
# Resolution of open question Q3: under the universal-component model, the
# reference image is "the latest recorded ``measurables.camera_image`` on
# the relevant camera component" (see Â§13.2). The operator workflow is now:
#
#   1. ``RECORD_MEASURABLES`` on the camera component (shipped in Phase 4).
#   2. The optimizer reads the freshest ``measurables.camera_image`` from
#      that camera at OPTIMIZE time.
#
# Step (2) â€” the optimizer-side migration â€” completed in Phase 9d:
# ``load_cobyla_reference_bgr_from_state`` reads ``measurables.camera_image``
# on the catalog camera tag at OPTIMIZE time.
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# Video feed â€” HTTP surface removed in Phase 9c.
#
# The legacy ``GET /api/video-feed/{status,stream}`` lab-wide routes
# backed the deprecated top-row LIVE FEED pane. That UI was removed;
# ceiling / gripper MJPEG is now accessed through each camera
# component's ``telemetry.stream`` widget in the symmetric panel
# (``GET /api/components/{tag_id}/telemetry/stream``).
#
# ``LabCommunicator.get_video_stream`` / ``get_video_feed_status`` remain
# on the real backend for internal / future overhead-camera wiring.
# --------------------------------------------------------------------------


# --- Recipe Endpoints ---

@app.get("/api/recipes")
async def list_recipes():
    recipes = []
    if os.path.exists(_RECIPES_DIR()):
        for f in os.listdir(_RECIPES_DIR()):
            if f.endswith(".json") and not f.endswith("_golden.json"):
                with open(os.path.join(_RECIPES_DIR(), f), "r") as file:
                    try:
                        data = json.load(file)
                        golden_path = os.path.join(_RECIPES_DIR(), f.replace(".json", "_golden.json"))
                        data["has_golden"] = os.path.exists(golden_path)
                        recipes.append(data)
                    except:
                        pass
    return recipes

@app.post("/api/recipes")
async def save_recipe(recipe: Recipe):
    file_path = os.path.join(_RECIPES_DIR(), f"{recipe.id}.json")
    with open(file_path, "w") as f:
        f.write(recipe.model_dump_json(indent=2))
    return {"status": "success", "message": f"Recipe {recipe.id} saved"}

@app.post("/api/recipes/{recipe_id}/play")
async def play_recipe(recipe_id: str, background_tasks: BackgroundTasks):
    if runtime_manager is not None and runtime_manager.mode == "mujoco":
        raise HTTPException(
            status_code=409,
            detail="Recipes are unavailable in MuJoCo v1",
        )
    file_path = os.path.join(_RECIPES_DIR(), f"{recipe_id}.json")
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Recipe not found")
    
    with open(file_path, "r") as f:
        data = json.load(f)
        recipe = Recipe(**data)
        
    state = lab.get_lab_state()
    if state.get("system_status") != "IDLE" and state.get("system_status") is not None:
         raise HTTPException(status_code=409, detail="System is busy")

    if runtime_manager is None:
        background_tasks.add_task(execute_recipe, recipe)
    else:
        try:
            target_lab, token = runtime_manager.reserve_operation()
        except Exception as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        background_tasks.add_task(
            _run_reserved_background_task,
            runtime_manager,
            token,
            execute_recipe,
            (recipe, target_lab),
            {},
        )
    return {"status": "accepted", "message": f"Recipe {recipe.name} started"}

@app.get("/api/recipes/{recipe_id}/golden")
async def get_golden_state(recipe_id: str):
    file_path = os.path.join(_RECIPES_DIR(), f"{recipe_id}_golden.json")
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Golden state not found")
    
    with open(file_path, "r") as f:
        return json.load(f)

@app.get("/api/recipes/{recipe_id}/compare")
async def compare_golden_state(recipe_id: str):
    # 1. Load Golden State
    golden_path = os.path.join(_RECIPES_DIR(), f"{recipe_id}_golden.json")
    if not os.path.exists(golden_path):
        raise HTTPException(status_code=404, detail="Golden state not found")
    
    with open(golden_path, "r") as f:
        golden = json.load(f)
        
    # 2. Get Current Lab State
    current = lab.get_lab_state()
    
    # 3. Compare
    report = {
        "recipe_id": recipe_id,
        "timestamp": datetime.now().isoformat(),
        "status": "MATCH", # MATCH, DRIFT, MISSING
        "details": []
    }
    
    golden_comps = golden.get("components", {})
    current_comps = current.get("components", {})
    
    total_drift = 0.0
    
    for comp_id, g_comp in golden_comps.items():
        entry = {
            "component_id": comp_id,
            "status": "OK",
            "drift_mm": 0.0
        }
        
        if comp_id not in current_comps:
            entry["status"] = "MISSING"
            report["status"] = "DRIFT"
        else:
            c_comp = current_comps[comp_id]
            g_pose = (g_comp.get("measurables") or {}).get("pose") or {}
            c_pose = (c_comp.get("measurables") or {}).get("pose") or {}

            dx = g_pose.get("x", 0) - c_pose.get("x", 0)
            dy = g_pose.get("y", 0) - c_pose.get("y", 0)
            # Include z in the drift when BOTH sides have it -- required by
            # new_primitives.md Stage 7 (HOVER / PICK produce z-bearing poses).
            # Older goldens predating the in-air primitives don't store z;
            # in that case we fall back to 2D drift so historical recipes
            # keep comparing against current state without spurious DRIFT.
            if "z" in g_pose and "z" in c_pose:
                dz = float(g_pose.get("z", 0)) - float(c_pose.get("z", 0))
                dist = (dx*dx + dy*dy + dz*dz) ** 0.5
                entry["drift_axes"] = "xyz"
            else:
                dist = (dx*dx + dy*dy) ** 0.5
                entry["drift_axes"] = "xy"

            entry["drift_mm"] = round(dist, 4)
            
            if dist > 0.1: # Tolerance 0.1mm
                entry["status"] = "DRIFTED"
                report["status"] = "DRIFT"
                
            total_drift += dist
            
        report["details"].append(entry)
        
    # Check for extra components in Lab not in Golden
    for comp_id in current_comps:
        if comp_id not in golden_comps:
            report["details"].append({
                "component_id": comp_id,
                "status": "EXTRA",
                "drift_mm": 0.0
            })
            report["status"] = "DRIFT"

    # Compare ``holding`` if the golden has it (backwards compat: old goldens
    # predating the in-air primitives just skip this block).
    if "holding" in golden:
        g_hold = golden.get("holding") or {}
        c_hold = current.get("holding") or {}
        g_tag = g_hold.get("tag_id")
        c_tag = c_hold.get("tag_id")
        if g_tag != c_tag:
            report["status"] = "DRIFT"
            report["details"].append({
                "component_id": "<holding>",
                "status": "HOLDING_MISMATCH",
                "golden_tag": g_tag,
                "current_tag": c_tag,
                "drift_mm": 0.0,
            })
        elif g_tag is not None:
            # Same tag held -- compare the in-air pose so PICK/HOVER recipes
            # are reproducible end-to-end.
            gp = g_hold.get("nominal_pose") or {}
            cp = c_hold.get("nominal_pose") or {}
            dx = float(gp.get("x", 0)) - float(cp.get("x", 0))
            dy = float(gp.get("y", 0)) - float(cp.get("y", 0))
            dz = float(gp.get("z", 0)) - float(cp.get("z", 0))
            hold_dist = (dx*dx + dy*dy + dz*dz) ** 0.5
            if hold_dist > 0.1:
                report["status"] = "DRIFT"
                report["details"].append({
                    "component_id": "<holding>",
                    "status": "HOLDING_DRIFTED",
                    "tag": g_tag,
                    "drift_mm": round(hold_dist, 4),
                })
            total_drift += hold_dist

    report["total_drift_mm"] = round(total_drift, 4)
    return report

@app.get("/api/debug/ghost-state")
async def get_ghost_state():
    """
    Constructs the 'Ghost State' (Tier 2) from the Lab State (Tier 1).
    Derived from each component's **tunables** (nominal pose, placement, storage intent).
    """
    state = lab.get_lab_state()
    ghost_state = {}
    
    if "components" in state:
        for name, comp in state["components"].items():
            tun = comp.get("tunables") or {}
            ghost_state[name] = {
                "nominal_pose": tun.get("nominal_pose"),
                "placement": tun.get("placement"),
                "storage": tun.get("storage"),
                "presence": tun.get("presence"),
            }
    return ghost_state

@app.get("/api/debug/golden-states")
async def list_golden_states():
    """Returns a map of recipe_id -> golden_state content"""
    golden_states = {}
    if os.path.exists(_RECIPES_DIR()):
        for f in os.listdir(_RECIPES_DIR()):
            if f.endswith("_golden.json"):
                recipe_id = f.replace("_golden.json", "")
                with open(os.path.join(_RECIPES_DIR(), f), "r") as file:
                    try:
                        golden_states[recipe_id] = json.load(file)
                    except:
                        pass
    return golden_states

@app.get("/snapshots")
async def read_snapshots_redirect():
    """Snapshots live under Wiki â†’ Backends."""
    return RedirectResponse(url="/wiki#backends", status_code=307)


@app.get("/catalog")
async def read_catalog_redirect():
    """Legacy Catalog URL â†’ Wiki Backends hub."""
    return RedirectResponse(url="/wiki#backends", status_code=307)


@app.get("/wiki")
async def read_wiki():
    """In-app Wiki: Learn curriculum + live Capabilities (components / kernels)."""
    path = os.path.join(frontend_path, "wiki.html")
    with open(path, "r", encoding="utf-8") as f:
        html = f.read()
    return Response(content=html, media_type="text/html", headers={
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
        "Expires": "0",
    })


@app.get("/operations")
async def read_operations():
    path = os.path.join(frontend_path, "operations.html")
    with open(path, "r", encoding="utf-8") as f:
        html = f.read()
    return Response(content=html, media_type="text/html", headers={
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
        "Expires": "0",
    })


@app.get("/debug")
async def read_debug():
    path = os.path.join(frontend_path, "debug.html")
    with open(path, "r", encoding="utf-8") as f:
        html = f.read()
    return Response(content=html, media_type="text/html", headers={
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
        "Expires": "0",
    })

if __name__ == "__main__":
    import uvicorn

    # Optional: LOG_LEVEL=DEBUG shows per-poll lab-state logs above.
    _lvl = getattr(logging, (os.getenv("LOG_LEVEL") or "INFO").upper(), logging.INFO)
    logging.basicConfig(level=_lvl, format="%(levelname)s %(name)s: %(message)s")

    # Access log prints every HTTP line (e.g. GET /api/lab-state twice per second). Off unless:
    #   UVICORN_ACCESS_LOG=1
    # Or run: uvicorn main:app --reload --no-access-log
    _access = (os.getenv("UVICORN_ACCESS_LOG") or "").strip().lower() in ("1", "true", "yes")
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        access_log=_access,
        log_level=(os.getenv("UVICORN_LOG_LEVEL") or "info").lower(),
    )
