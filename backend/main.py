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


# Load .env from project root (parent of backend/) — only LAB_VIEW_PATH is required there.
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_env_path = os.path.join(_project_root, ".env")
if os.path.exists(_env_path):
    from dotenv import load_dotenv
    load_dotenv(_env_path)
    print(f"[CONFIG] Loaded .env from {_env_path}")
else:
    print(f"[CONFIG] No .env at {_env_path}")

from lab_communicator.shared.lab_view_config import (
    bootstrap_lab_view,
    get_lab_manifest,
    get_lab_view_paths,
    laser_line_coeffs_from_doc,
    line_id_pattern,
    load_layout_document,
    read_laser_lines_doc,
    two_points_define_line,
    write_laser_lines_doc,
)
from lab_communicator.shared.communicator_factory import create_communicator
from lab_model import motor_rotation_store as motor_rot

bootstrap_lab_view(_project_root)
motor_rot.configure(get_lab_view_paths().motor_rotations_json)

from lab_model.primitives import (
    ConfirmHoldingTagBody,
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


from lab_model.optimization.errors import EnsemblePreflightError
from lab_model.optimization.preflight import preflight_ensemble

RECIPES_DIR = get_lab_view_paths().recipes_dir
CONTROL_DIR = get_lab_view_paths().control_dir

# Initialize communicator from lab_manifest.json inside LAB_VIEW_PATH
_manifest = get_lab_manifest()
LAB_MODE = _manifest.lab_mode
COMMUNICATOR_ID = _manifest.communicator
print(f"LAB_MODE: {LAB_MODE} (communicator={COMMUNICATOR_ID!r})")

lab = None
runtime_manager = None
try:
    lab = create_communicator(COMMUNICATOR_ID)
    if COMMUNICATOR_ID == "mock":
        from lab_communicator.runtime_mode import RuntimeLabProxy

        runtime_manager = RuntimeLabProxy(lab)
        lab = runtime_manager
    print(f">>> STARTING WITH {COMMUNICATOR_ID.upper()} COMMUNICATOR <<<")
except ImportError as e:
    print(f"CRITICAL ERROR: Failed to import communicator {COMMUNICATOR_ID!r}: {e}")
    if COMMUNICATOR_ID != "mock":
        print("Falling back to mock communicator...")
        try:
            lab = create_communicator("mock")
            LAB_MODE = "MOCK"
        except Exception as e2:
            print(f"CRITICAL ERROR: Mock fallback failed: {e2}")
except Exception as e:
    print(f"CRITICAL ERROR: Failed to initialize communicator {COMMUNICATOR_ID!r}: {e}")
    if COMMUNICATOR_ID != "mock":
        print("Falling back to mock communicator...")
        try:
            lab = create_communicator("mock")
            LAB_MODE = "MOCK"
        except Exception as e2:
            print(f"CRITICAL ERROR: Mock fallback failed: {e2}")


def _persist_session_checkpoint_on_shutdown() -> None:
    try:
        if lab is None:
            return
        saver = getattr(lab, "save_session_checkpoint_if_enabled", None)
        if callable(saver):
            saver()
    except Exception as e:  # noqa: BLE001
        logger.warning("Shutdown session checkpoint save failed: %s", e)


@asynccontextmanager
async def _app_lifespan(_: FastAPI):
    _install_windows_connection_reset_handler()
    yield
    # Phase 8 teardown: stop the TELEOP stale-lease sweeper thread (if it
    # ever started) before persisting the session checkpoint, so the
    # checkpoint reflects a quiesced state instead of one mid-sweep.
    try:
        if lab is not None:
            shutdown = getattr(lab, "shutdown_lab_processes", None)
            if callable(shutdown):
                shutdown()
    except Exception:
        pass
    try:
        if lab is not None:
            lab.stop_teleop_sweeper()
    except Exception:
        pass
    _persist_session_checkpoint_on_shutdown()


app = FastAPI(lifespan=_app_lifespan)

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
    # When true, the frontend has already driven the reconcile primitives one at
    # a time through /api/command (for step-by-step visibility). The endpoint
    # then only *records* the result — projection + pointer + bench claim — and
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
    # state. Pop needs no snapshot — the server already holds the stash.
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
    from lab_communicator.shared.session_checkpoint import (
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
        from lab_communicator.shared.lab_view_config import get_lab_manifest

        resp["manifest"] = {
            "session_checkpoint": bool(get_lab_manifest().session_checkpoint),
            "session_reconciliation": get_lab_manifest().as_dict().get(
                "session_reconciliation"
            ),
        }
    except Exception:
        resp["manifest"] = None
    print(
        f"[session-reconcile] enabled={resp['enabled']} skipped={resp.get('skipped_reason')!r} "
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
    
    golden_path = os.path.join(RECIPES_DIR, f"{recipe.id}_golden.json")
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
        if path == "/" or path == "/debug" or path.startswith("/static"):
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


@app.get("/")
async def read_index():
    path = os.path.join(frontend_path, "index.html")
    html = _read_index_html(path)
    return Response(content=html, media_type="text/html", headers={
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
        "Expires": "0",
    })

@app.get("/api/platform/registries")
async def get_platform_registries():
    """Tunable/measurable plugins and primitive metadata (for UI tooling)."""
    from lab_model.platform import export_platform_registries

    return export_platform_registries()


@app.get("/api/catalog")
async def get_component_catalog():
    """
    Tags listed in ``active_catalog.json`` merged with rows from ``component_library.json``
    under ``LAB_VIEW_PATH``. Always re-read from disk — see ``lab.get_catalog()``.
    """
    if lab is None:
        return []
    try:
        return lab.get_catalog()
    except Exception as e:
        logger.exception("GET /api/catalog failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to read catalog: {e}")


def _locked_runtime_mode_info() -> Dict[str, Any]:
    physical_armed = (
        COMMUNICATOR_ID == "real"
        and lab is not None
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


@app.get("/api/runtime-mode")
async def get_runtime_mode():
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
        from lab_communicator.runtime_mode import RuntimeModeError

        if isinstance(exc, RuntimeModeError):
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        logger.exception("Runtime mode switch failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


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
    from lab_model.catalog.active_catalog_store import list_active_catalog_tags
    from lab_model.catalog.bundle import library_by_tag

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
    from lab_model.catalog.bundle import library_by_tag

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


@app.post("/api/components/{tag_id}/measurables/record")
async def post_component_record_measurables(tag_id: str):
    """Record fresh measurables for one component (camera capture, etc.). Primitive: ``RECORD_MEASURABLES``."""
    if lab is None:
        raise HTTPException(status_code=503, detail="Lab not initialized")
    state = lab.get_lab_state()
    current_status = state.get("system_status")
    if current_status == "BUSY" or current_status == "OPTIMIZING":
        raise HTTPException(status_code=409, detail=f"System is {current_status}. Please wait.")
    await lab.record_measurables_for_tag(tag_id)
    try:
        meas = fetch_read_primitive(lab, PrimitiveId.GET_MEASURABLES, tag_id)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=validation_error_detail(e))
    return {"status": "ok", "measurables": meas}


# ---------------------------------------------------------------------------
# Phase 6 — per-component telemetry routes
#
# Catalog entries declare their telemetry channels (e.g. ``stream``,
# ``preview``) with direct URLs that include a ``{tag_id}`` token (see
# universal_component_architecture.md §13.2 and §16.6). These routes
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
    from lab_model.catalog.schema import telemetry_channel  # noqa: PLC0415

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
    from lab_model.catalog.schema import (  # noqa: PLC0415
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
    """Per-component MJPEG telemetry stream (Phase 6 / §13.2 ``stream`` channel).

    Delegates to ``lab.get_table_cam_stream(cam_id, fps)`` for OPTICAL_CAMERA
    tags. Returns ``multipart/x-mixed-replace`` MJPEG for catalog-driven
    clients (Phase 6 / §13.2).
    """
    from lab_model.catalog.schema import resolve_telemetry_stream_backend

    catalog_row, _desc = _telemetry_lookup(tag_id, "stream")
    from lab_model.domain.component import is_live_feed_active

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
    from lab_model.catalog.schema import (
        live_feed_channel,
        resolve_telemetry_stream_backend,
    )
    from lab_model.domain.component import is_live_feed_active

    catalog_row = _catalog_row_or_404(tag_id)
    # JPEGPoll declares ``live_feed.stream`` with url ``.../telemetry/preview`` —
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
    if LAB_MODE == "REAL" and hasattr(lab, "get_optimization_stream"):
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
# Phase 8 — per-component TELEOP (universal_component_architecture.md §16.5)
#
# Three routes drive a single component's "in-air manual mode":
#
#   POST /api/components/{tag_id}/teleop/start
#       Acquire the per-component TELEOP lease. Nulls the component's
#       measurables (Golden Rule §3.2) and stamps a TTL timestamp so the
#       LabCommunicator's stale-lease sweeper can recover from a browser
#       crash without an explicit END_TELEOP.
#   POST /api/components/{tag_id}/teleop/end
#       Release the lease (idempotent — UI fires this on page unload).
#   POST /api/components/{tag_id}/telemetry/jog
#       One absolute jog frame: nominal_pose and/or nominal_motor_positions.
#       Frames are absolute, not deltas, so frame loss is self-healing.
#
# All three reuse the standard dispatch pipeline (``execute_validated_command``)
# so logging, validation, and per-primitive bookkeeping match the rest of
# the API. We return a synchronous 200 from each — these primitives are
# cheap (state mutations, no hardware blocking calls in Phase 8a) and the
# operator needs the ack before sending the next frame.
# ---------------------------------------------------------------------------


def _refuse_teleop_if_lab_down(tag_id: str) -> Dict[str, Any]:
    """Pre-flight check shared by all three TELEOP routes.

    Returns the catalog row on success; raises 404/503 on failure.
    Centralizes the "is the lab booted and does the tag exist?" check so
    each route stays one-statement-thin.
    """
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
    Nulls measurables per §3.2 Golden Rule. Refusals (BUSY/OPTIMIZING with
    the strict-quiet manifest knob, another component already teleoped,
    stored part) bubble up as 409 ``Conflict``.
    """
    _refuse_teleop_if_lab_down(tag_id)
    try:
        cmd = StartTeleopBody(action="START_TELEOP", target_id=tag_id)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=validation_error_detail(e))
    try:
        await execute_validated_command(lab, cmd)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {
        "status": "ok",
        "message": f"TELEOP started for {tag_id}",
        "tunables": fetch_read_primitive(lab, PrimitiveId.GET_TUNABLES, tag_id),
        "telemetry": lab.return_telemetry_for_tag(tag_id),
    }


@app.post("/api/components/{tag_id}/teleop/end")
async def post_component_teleop_end(tag_id: str):
    """Release the per-component TELEOP lease for ``tag_id`` (idempotent).

    Always returns 200 — ending an already-released session is a success.
    This is the typical browser-unload path; the UI fires END on
    ``beforeunload`` and the server may or may not have already swept the
    stale lease.
    """
    _refuse_teleop_if_lab_down(tag_id)
    cmd = EndTeleopBody(action="END_TELEOP", target_id=tag_id)
    try:
        await execute_validated_command(lab, cmd)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {
        "status": "ok",
        "message": f"TELEOP ended for {tag_id}",
        "tunables": fetch_read_primitive(lab, PrimitiveId.GET_TUNABLES, tag_id),
        "telemetry": lab.return_telemetry_for_tag(tag_id),
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

    - 404 — unknown tag.
    - 409 — tag is not in TELEOP (must START first).
    - 422 — malformed body.
    - 503 — lab not initialized.
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

    cmd = TeleopJogBody(action="TELEOP_JOG", target_id=tag_id, parameters=params)
    try:
        await execute_validated_command(lab, cmd)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {
        "status": "ok",
        "frame_id": params.frame_id,
        "telemetry": lab.return_telemetry_for_tag(tag_id),
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

    cmd = TeleopGotoBody(action="TELEOP_GOTO", target_id=tag_id, parameters=params)
    try:
        await execute_validated_command(lab, cmd)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {
        "status": "ok",
        "telemetry": lab.return_telemetry_for_tag(tag_id),
    }


@app.get("/api/components/{tag_id}/telemetry/live-pose")
async def get_component_telemetry_live_pose(tag_id: str):
    """High-rate live pose for TeleOp (in-memory; not in lab_state JSON).

    Deprecated hot path — prefer ``WS /api/components/{tag_id}/teleop/session``.
    Kept for debug clients and HTTP fallback.
    """
    _refuse_teleop_if_lab_down(tag_id)
    from lab_model.domain.component import is_teleop_ready

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
    """Duplex TeleOp session: server-push pose @ ~50 Hz; client ``goto`` / ``ping``."""
    if lab is None:
        await websocket.close(code=1013, reason="Lab not initialized")
        return
    if runtime_manager is not None and runtime_manager.mode == "mujoco":
        await websocket.close(code=4403, reason="TeleOp is unavailable in MuJoCo v1")
        return
    catalog_row = (lab.catalog_map or {}).get(tag_id)
    if not isinstance(catalog_row, dict):
        await websocket.close(code=4404, reason=f"Unknown tag {tag_id!r}")
        return
    from lab_model.orchestration.teleop_session_ws import run_teleop_session_websocket

    await run_teleop_session_websocket(websocket, lab, tag_id)


@app.post("/api/components/{tag_id}/telemetry/live-feed/start")
async def post_component_live_feed_start(tag_id: str, channel: str = "stream"):
    """Connect and start live feed (``START_LIVE_FEED``)."""
    _refuse_teleop_if_lab_down(tag_id)
    cmd = StartLiveFeedBody(action="START_LIVE_FEED", target_id=tag_id, channel=channel)
    try:
        await execute_validated_command(lab, cmd)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {
        "status": "ok",
        "message": f"Live feed started for {tag_id}",
        "telemetry": lab.return_telemetry_for_tag(tag_id),
    }


@app.post("/api/components/{tag_id}/telemetry/live-feed/end")
async def post_component_live_feed_end(tag_id: str, channel: str = "all"):
    """Stop and disconnect live feed (``END_LIVE_FEED``)."""
    _refuse_teleop_if_lab_down(tag_id)
    cmd = EndLiveFeedBody(action="END_LIVE_FEED", target_id=tag_id, channel=channel)
    await execute_validated_command(lab, cmd)
    return {
        "status": "ok",
        "message": f"Live feed ended for {tag_id}",
        "telemetry": lab.return_telemetry_for_tag(tag_id),
    }


@app.get("/api/components/{tag_id}/telemetry")
async def get_component_telemetry(tag_id: str):
    """Return saved telemetry session state (teleop + live_feed)."""
    if lab is None:
        raise HTTPException(status_code=503, detail="Lab not initialized")
    return {"status": "ok", "telemetry": lab.return_telemetry_for_tag(tag_id)}


@app.get("/api/components/{tag_id}/camera-image")
async def get_component_camera_image(tag_id: str):
    """Stream the PNG referenced by ``measurables.camera_image.path`` for one tag.

    Returns **404** when no image is currently recorded (e.g. after a motion
    nulled measurables per Phase 3 of ``universal_component_architecture.md``
    — the operator must POST to ``.../measurables/record`` to regenerate it).
    Path is read from saved lab state, not from the request, so there is no
    user-controlled path traversal vector; the on-disk file is still checked
    for existence + supported format as defense-in-depth.
    """
    if lab is None:
        raise HTTPException(status_code=503, detail="Lab not initialized")
    state = lab.get_lab_state()
    entry = (state.get("components") or {}).get(tag_id)
    if not isinstance(entry, dict):
        raise HTTPException(status_code=404, detail=f"Unknown tag {tag_id}")
    from lab_model.domain.component import get_measurables  # noqa: PLC0415

    ci = get_measurables(entry).get("camera_image")
    if not isinstance(ci, dict):
        raise HTTPException(status_code=404, detail="No camera image recorded")
    path = ci.get("path")
    if not isinstance(path, str) or not path:
        raise HTTPException(status_code=404, detail="No camera image path")
    abs_path = os.path.abspath(path)
    if not os.path.isfile(abs_path):
        raise HTTPException(status_code=404, detail="Camera image file missing")
    fmt = str(ci.get("format") or "png").lower()
    media_by_fmt = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}
    if fmt not in media_by_fmt:
        raise HTTPException(status_code=400, detail=f"Unsupported camera image format {fmt!r}")
    return FileResponse(abs_path, media_type=media_by_fmt[fmt])


@app.get("/api/lab-state")
async def get_lab_state():
    # Polled every ~500ms from the UI — use debug to avoid flooding the console (see LOG_LEVEL).
    logger.debug("GET /api/lab-state")
    if lab is None:
        logger.error("GET /api/lab-state: lab communicator not initialized")
        raise HTTPException(status_code=500, detail="Lab Communicator failed to initialize. Check server logs.")
    try:
        state = lab.get_lab_state()
        logger.debug("GET /api/lab-state: ok")
        if isinstance(state, dict):
            active_runtime = (
                runtime_manager.mode.upper()
                if runtime_manager is not None
                else LAB_MODE
            )
            state = {
                **state,
                "lab_mode": active_runtime,
                "runtime_mode": active_runtime.lower(),
            }
        return JSONResponse(content=state)
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
    from lab_communicator.shared.session_checkpoint import reconciliation_thresholds_from_manifest
    from lab_model.state.pose_refresh_offers import build_pose_refresh_offers
    from lab_model.state.pose_refresh_selection import normalize_tag_id_list

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
            "Applying refresh runs a camera scan and updates measurables.pose "
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
    from lab_model.state.control_manager import ControlManager

    safe = (repo_id or "default").strip() or "default"
    if safe not in _control_managers:
        _control_managers[safe] = ControlManager(CONTROL_DIR, safe)
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
    from lab_model.state.control_manager import read_bench_origin, repo_owns_bench

    safe = (repo_id or "default").strip() or "default"
    owns = repo_owns_bench(CONTROL_DIR, safe)
    if _CONTROL_DEBUG:
        origin = read_bench_origin(CONTROL_DIR)
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
    from lab_model.state.control_manager import write_bench_origin

    safe = (repo_id or "default").strip() or "default"
    _control_log("claim_bench", repo=safe, configuration_id=configuration_id)
    write_bench_origin(CONTROL_DIR, safe, configuration_id)


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
    from lab_model.state.control_manager import list_control_repos

    return {"repos": list_control_repos(CONTROL_DIR)}


@app.post("/api/control/backfill-lines")
async def control_backfill_lines(payload: Dict[str, Any] = Body(default={})):
    """One-time, idempotent migration: inject the currently-drawn alignment
    overlays (guides + laser lines) into every commit across every repo that
    predates line-versioning. Only fills documents missing the keys.

    Guides come from the request body (the browser's localStorage import) when
    provided, otherwise from the live runtime; laser lines come from the runtime
    (seeded from the lab bundle).
    """
    from lab_model.state.control_manager import list_control_repos
    from lab_model.state.projections import (
        normalize_alignment_guides,
        normalize_laser_lines_doc,
    )

    runtime = lab.get_lab_state() if lab is not None else {}
    if isinstance(payload.get("alignment_guides"), list):
        guides = normalize_alignment_guides(payload.get("alignment_guides"))
    else:
        guides = normalize_alignment_guides(runtime.get("alignment_guides"))
    laser = normalize_laser_lines_doc(runtime.get("laser_lines"))

    repos = list_control_repos(CONTROL_DIR)
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
    from lab_model.state.control_manager import list_control_repos

    repos = list_control_repos(CONTROL_DIR)
    updated = 0
    for repo in repos:
        mgr = _get_control_manager(repo["repo_id"])
        updated += mgr.backfill_optimization_metadata()
    return {"repos": len(repos), "updated": updated}


@app.post("/api/control/repos")
async def control_create_repo(payload: ControlCreateRepoBody):
    from lab_model.state.control_manager import create_control_repo, validate_repo_id

    try:
        validate_repo_id(payload.repo_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        repo = create_control_repo(
            CONTROL_DIR,
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
    from lab_model.catalog.bundle import active_tag_ids, library_by_tag
    from lab_model.catalog.catalog_hash import compute_active_catalog_hash

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
    from lab_model.catalog.bundle import active_tag_ids, library_by_tag
    from lab_model.catalog.catalog_hash import compute_active_catalog_hash

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
async def control_checkout(repo_id: str, payload: ControlCheckoutBody):
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
        # bench (git reset --soft). No reconcile plan, no projection — the
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
    # "adopt" (handled above) only repoints the HEAD — both leave the bench
    # untouched, so neither is gated. (In particular, an unadopted repo reads as
    # dirty-vs-empty, so gating preview here would block you from ever clicking a
    # node to Set it.)
    if mode == "hard":
        owns_bench = _repo_owns_bench(repo_id)
        applied_id = mgr.get_applied().get("configuration_id") if owns_bench else None
        is_dirty = mgr.runtime_is_dirty(runtime, owns_bench=owns_bench)
        _control_log(
            "checkout:hard-guard",
            repo=repo_id,
            target=payload.configuration_id,
            owns_bench=owns_bench,
            applied=applied_id,
            dirty=is_dirty,
            blocked=(is_dirty and payload.configuration_id != applied_id),
        )
        if is_dirty and payload.configuration_id != applied_id:
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
        from lab_model.catalog.bundle import active_tag_ids, library_by_tag
        from lab_model.catalog.catalog_hash import compute_active_catalog_hash
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
        # the state-machine separation — preview can no longer contaminate the
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

        from lab_model.state.reconcile_executor import (
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

    from lab_model.state.projections import (
        EMPTY_CONFIGURATION,
        extract_configuration,
        extract_configuration_metadata,
    )

    # Stash base: the applied node when this repo owns the bench, otherwise the
    # shared empty state (a foreign bench is uncommitted work on top of empty,
    # so stashing clears the table back to empty — every part returns to
    # storage — leaving a clean slate to check out this repo's nodes).
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

    from lab_model.state.reconcile_executor import (
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
        # the stash snapshot on the bench (which is dirty-vs-applied by design —
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

    from lab_model.state.reconcile_executor import (
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
    from lab_model.domain.storage_region import analyze_layout_issues

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
    from lab_model.domain.storage_region import storage_grid_spec

    return storage_grid_spec()


@app.get("/api/lab-layout")
async def get_lab_layout():
    """Breadboard/table bounds + storage grid overlay (single source matching ``layout.json``)."""
    from lab_model.domain.storage_region import storage_grid_spec

    doc = load_layout_document()
    enriched = dict(doc)
    paths = get_lab_view_paths()
    manifest = get_lab_manifest()
    enriched["lab_view_root"] = paths.root_dir
    enriched["storage_grid"] = storage_grid_spec()
    enriched["lab_manifest"] = manifest.as_dict()
    enriched["lab_mode"] = manifest.lab_mode
    enriched["communicator"] = manifest.communicator
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
    out["lab_mode"] = LAB_MODE
    out["schema_file"] = os.path.basename(get_lab_view_paths().laser_lines_json)
    return out


@app.get("/api/laser-line")
async def get_laser_line():
    """Single-line legacy coefficients (x = a*y + b, mm) from the runtime snap line."""
    return laser_line_coeffs_from_doc(_runtime_laser_doc(), LAB_MODE)


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

    from lab_model.state.runtime_manager import MutationKind

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
    from lab_model.state.runtime_manager import MutationKind

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
    from lab_model.state.projections import normalize_alignment_guides

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
    from lab_model.state.projections import (
        normalize_alignment_guides,
        normalize_laser_lines_doc,
    )
    from lab_model.state.runtime_manager import MutationKind

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
    try:
        preflight_ensemble(state, params)
    except EnsemblePreflightError as exc:
        raise HTTPException(status_code=400, detail=exc.as_dict()) from exc


def _enforce_holding_rules(cmd, state: Dict[str, Any]) -> None:
    """
    Reject commands that would be unsafe given the current HOLDING state.

    See ``new_primitives.md`` §6. BUSY/OPTIMIZING are already rejected upstream.
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


@app.post("/api/command")
async def receive_command(payload: Dict[str, Any], background_tasks: BackgroundTasks):
    print(f"Received Command: {payload}")

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
            await execute_validated_command(target_lab, cmd)
            meas = fetch_read_primitive(
                target_lab,
                PrimitiveId.GET_MEASURABLES,
                cmd.target_id,
            )
        finally:
            runtime_manager.release_operation(token)
        return {"status": "ok", "measurables": meas}

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
# Cobyla reference image — HTTP surface removed in Phase 9a.
#
# The four ``/api/cobyla-reference-image*`` routes (GET, POST, GET /status,
# DELETE) were the legacy side-channel for the COBYLA strategy's reference
# ndarray. They were deleted per :doc:`universal_component_architecture` §9a.
#
# Resolution of open question Q3: under the universal-component model, the
# reference image is "the latest recorded ``measurables.camera_image`` on
# the relevant camera component" (see §13.2). The operator workflow is now:
#
#   1. ``RECORD_MEASURABLES`` on the camera component (shipped in Phase 4).
#   2. The optimizer reads the freshest ``measurables.camera_image`` from
#      that camera at OPTIMIZE time.
#
# Step (2) — the optimizer-side migration — completed in Phase 9d:
# ``load_cobyla_reference_bgr_from_state`` reads ``measurables.camera_image``
# on the catalog camera tag at OPTIMIZE time.
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# Video feed — HTTP surface removed in Phase 9c.
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
    if os.path.exists(RECIPES_DIR):
        for f in os.listdir(RECIPES_DIR):
            if f.endswith(".json") and not f.endswith("_golden.json"):
                with open(os.path.join(RECIPES_DIR, f), "r") as file:
                    try:
                        data = json.load(file)
                        golden_path = os.path.join(RECIPES_DIR, f.replace(".json", "_golden.json"))
                        data["has_golden"] = os.path.exists(golden_path)
                        recipes.append(data)
                    except:
                        pass
    return recipes

@app.post("/api/recipes")
async def save_recipe(recipe: Recipe):
    file_path = os.path.join(RECIPES_DIR, f"{recipe.id}.json")
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
    file_path = os.path.join(RECIPES_DIR, f"{recipe_id}.json")
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
    file_path = os.path.join(RECIPES_DIR, f"{recipe_id}_golden.json")
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Golden state not found")
    
    with open(file_path, "r") as f:
        return json.load(f)

@app.get("/api/recipes/{recipe_id}/compare")
async def compare_golden_state(recipe_id: str):
    # 1. Load Golden State
    golden_path = os.path.join(RECIPES_DIR, f"{recipe_id}_golden.json")
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
    if os.path.exists(RECIPES_DIR):
        for f in os.listdir(RECIPES_DIR):
            if f.endswith("_golden.json"):
                recipe_id = f.replace("_golden.json", "")
                with open(os.path.join(RECIPES_DIR, f), "r") as file:
                    try:
                        golden_states[recipe_id] = json.load(file)
                    except:
                        pass
    return golden_states

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
