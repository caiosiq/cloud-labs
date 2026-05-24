from fastapi import FastAPI, HTTPException, BackgroundTasks, Request, Query, Body
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse, RedirectResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware
from pydantic import BaseModel, Field, ValidationError
import json
import os
import re
import asyncio
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple
import io
import logging
import functools

logger = logging.getLogger(__name__)

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
    two_points_to_ab,
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
    EndLiveFeedBody,
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


RECIPES_DIR = get_lab_view_paths().recipes_dir
STATES_DIR = get_lab_view_paths().states_dir

# Initialize communicator from lab_manifest.json inside LAB_VIEW_PATH
_manifest = get_lab_manifest()
LAB_MODE = _manifest.lab_mode
COMMUNICATOR_ID = _manifest.communicator
print(f"LAB_MODE: {LAB_MODE} (communicator={COMMUNICATOR_ID!r})")

lab = None
try:
    lab = create_communicator(COMMUNICATOR_ID)
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
    yield
    # Phase 8 teardown: stop the TELEOP stale-lease sweeper thread (if it
    # ever started) before persisting the session checkpoint, so the
    # checkpoint reflects a quiesced state instead of one mid-sweep.
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

class StateName(BaseModel):
    name: str


class SessionReconcileApplyBody(BaseModel):
    tag_ids: List[str]


class RefreshPoseBody(BaseModel):
    """Optional tag ids whose full component rows are left unchanged after a scan."""

    preserve_tag_ids: List[str] = Field(default_factory=list)


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

async def execute_recipe(recipe: Recipe):
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
        await execute_validated_command(lab, cmd)

        await asyncio.sleep(0.5)
        
    print(f"[RECIPE] Recipe {recipe.name} complete. Saving Golden State...")

    # Save Golden State using Lab State. We capture ``holding`` alongside
    # ``components`` so recipes that end mid-HOLDING (e.g. PICK with no
    # PLACE_FROM_HOVER) are reproducible, and so the compare endpoint can
    # flag an unexpected held tag on replay. Older goldens predating this
    # field are still valid and compare fine -- see ``compare_golden_state``.
    state = lab.get_lab_state()
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

@app.post("/api/components")
async def add_component(payload: Dict[str, Any], background_tasks: BackgroundTasks):
    """
    Adds a component: `placement_mode` is `breadboard` (default) or `storage` (mock: packed in Q3).
    Real lab still expects a physical place + rescan unless using mock.
    """
    background_tasks.add_task(lab.add_component_to_state, payload)
    mode = (payload.get("placement_mode") or "breadboard").lower()
    return {"status": "accepted", "message": f"Request submitted ({mode}): {payload.get('name')}"}

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
    from lab_model.catalog.schema import resolve_cam_id_for_tag  # noqa: PLC0415

    cam_id = resolve_cam_id_for_tag(catalog_row)
    if cam_id is None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Component {tag_id!r} declares a camera telemetry channel but no "
                f"underlying cam_id could be resolved. Add ``cam_id`` to the catalog row "
                f"or rename ``id`` to follow the ``cam_gripper_N`` convention."
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
    """Per-component single-frame telemetry preview (Phase 6 / §13.2 ``preview`` channel).

    Single JPEG/PNG response suitable for ``JPEGPoll`` widgets. Cheaper
    than the MJPEG stream when the UI only wants a periodic snapshot
    (e.g. a context-panel thumbnail polled at ``default_fps`` from the
    catalog). Delegates to ``lab.capture_table_cam(cam_id, exposure)``.
    """
    from lab_model.catalog.schema import resolve_telemetry_stream_backend

    catalog_row, _desc = _telemetry_lookup(tag_id, "preview")
    if resolve_telemetry_stream_backend(catalog_row) == "overhead":
        raise HTTPException(
            status_code=501,
            detail=(
                f"telemetry.preview is not supported for overhead camera {tag_id!r}; "
                f"use telemetry.stream (MJPEG) instead."
            ),
        )
    cam_id = _resolve_cam_id_or_400(catalog_row, tag_id)
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
    """High-rate live pose for TeleOp (in-memory; not in lab_state JSON)."""
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
            state = {**state, "lab_mode": LAB_MODE}
        return JSONResponse(content=state)
    except Exception as e:
        logger.exception("GET /api/lab-state failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to read Lab State: {str(e)}")

def _schedule_pose_refresh(
    background_tasks: BackgroundTasks,
    preserve_tag_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    if lab is None:
        raise HTTPException(status_code=500, detail="Lab Communicator not initialized")
    fn = getattr(lab, "refresh_pose_from_camera", None)
    if callable(fn):
        plist = list(preserve_tag_ids or [])
        background_tasks.add_task(functools.partial(fn, preserve_tag_ids=plist))
        return {
            "status": "accepted",
            "message": "Pose refresh from camera started (updates measurables.pose)",
        }
    return {
        "status": "ok",
        "message": "Pose refresh not supported for this lab backend",
    }


@app.post("/api/lab-state/refresh-pose")
async def refresh_lab_pose_from_camera(
    background_tasks: BackgroundTasks,
    payload: Optional[RefreshPoseBody] = Body(None),
):
    """
    Re-localize component poses from the overhead / table camera (real: full scan; mock: simulated noise).
    """
    plist = []
    if payload is not None:
        plist = list(payload.preserve_tag_ids or [])
    cur = getattr(lab, "get_lab_state", lambda: {})
    try:
        st = cur()
    except Exception:  # noqa: BLE001
        st = {}
    if isinstance(st, dict):
        cs = st.get("system_status")
        if cs in ("BUSY", "OPTIMIZING"):
            raise HTTPException(status_code=409, detail=f"System is {cs}. Please wait.")
    return _schedule_pose_refresh(background_tasks, plist)


@app.post("/api/lab-state/refresh")
async def refresh_lab_state_legacy(
    background_tasks: BackgroundTasks,
    payload: Optional[RefreshPoseBody] = Body(None),
):
    """Deprecated: use ``POST /api/lab-state/refresh-pose`` (same behavior)."""
    plist = []
    if payload is not None:
        plist = list(payload.preserve_tag_ids or [])
    return _schedule_pose_refresh(background_tasks, plist)


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


@app.get("/api/states")
async def list_saved_states():
    if not os.path.exists(STATES_DIR):
        return []
    files = [f for f in os.listdir(STATES_DIR) if f.endswith(".json")]
    # Return names without extension
    return sorted([os.path.splitext(f)[0] for f in files])

@app.post("/api/states/save")
async def save_lab_state(payload: StateName):
    if lab is None:
        raise HTTPException(status_code=500, detail="Lab Communicator not initialized")

    if hasattr(lab, "get_lab_state"):
        st = lab.get_lab_state() or {}
        if st.get("system_status") in ("BUSY", "OPTIMIZING"):
            raise HTTPException(status_code=409, detail=f"System is {st.get('system_status')}. Please wait.")

    import re
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="State name is required")
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", name)
    if not safe:
        raise HTTPException(status_code=400, detail="Invalid state name")

    file_path = os.path.join(STATES_DIR, f"{safe}.json")
    state = lab.get_lab_state()
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    return {"status": "success", "name": safe, "path": file_path}

@app.post("/api/states/load")
async def load_lab_state(payload: StateName):
    if lab is None:
        raise HTTPException(status_code=500, detail="Lab Communicator not initialized")

    if hasattr(lab, "get_lab_state"):
        st = lab.get_lab_state() or {}
        if st.get("system_status") in ("BUSY", "OPTIMIZING"):
            raise HTTPException(status_code=409, detail=f"System is {st.get('system_status')}. Please wait.")

    import re
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="State name is required")
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", name)
    file_path = os.path.join(STATES_DIR, f"{safe}.json")
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail=f"State not found: {safe}")

    with open(file_path, "r", encoding="utf-8") as f:
        state = json.load(f)

    if hasattr(lab, "set_lab_state"):
        lab.set_lab_state(state)

    # Return merged lab state (e.g. catalog parts not in the file are preserved on load).
    out_state = lab.get_lab_state() if hasattr(lab, "get_lab_state") else state
    return {"status": "success", "name": safe, "state": out_state}

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


@app.get("/api/laser-line")
async def get_laser_line():
    """Single-line legacy coefficients (x = a*y + b, mm) from lab_view laser_lines.json snap line."""
    doc = read_laser_lines_doc()
    return laser_line_coeffs_from_doc(doc, LAB_MODE)


@app.get("/api/laser-lines")
async def get_laser_lines():
    """All laser overlays defined in lab_view laser_lines.json."""
    doc = read_laser_lines_doc()
    out = dict(doc)
    out["lab_mode"] = LAB_MODE
    out["schema_file"] = os.path.basename(get_lab_view_paths().laser_lines_json)
    return out


@app.patch("/api/laser-lines/{line_id}")
async def patch_laser_line(line_id: str, payload: Dict[str, Any] = Body(...)):
    """Update one line: ``enabled`` anytime; ``p1``/``p2`` only with ``confirm: true``."""
    if not line_id_pattern().match(line_id or ""):
        raise HTTPException(status_code=400, detail="Invalid line id")
    doc = read_laser_lines_doc()
    lines = doc.get("lines")
    if not isinstance(lines, list):
        lines = []
        doc["lines"] = lines
    idx = next(
        (i for i, ln in enumerate(lines) if isinstance(ln, dict) and ln.get("id") == line_id),
        None,
    )
    if idx is None:
        raise HTTPException(status_code=404, detail=f"Unknown laser line: {line_id}")

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
        if two_points_to_ab(p1f, p2f) is None:
            raise HTTPException(
                status_code=422,
                detail="Invalid geometry: coincident points or unsupported horizontal line.",
            )
        lines[idx]["p1"] = p1f
        lines[idx]["p2"] = p2f

    if "enabled" in payload:
        en = payload["enabled"]
        if not isinstance(en, bool):
            raise HTTPException(status_code=400, detail="enabled must be a boolean")
        lines[idx]["enabled"] = en

    if "name" in payload and isinstance(payload["name"], str) and payload["name"].strip():
        lines[idx]["name"] = payload["name"].strip()[:120]

    if "color" in payload and isinstance(payload["color"], str) and payload["color"].strip():
        col = payload["color"].strip()
        if len(col) > 32:
            raise HTTPException(status_code=400, detail="color string too long")
        lines[idx]["color"] = col

    doc["version"] = max(1, int(doc.get("version") or 1))
    write_laser_lines_doc(doc)
    out = dict(read_laser_lines_doc())
    out["lab_mode"] = LAB_MODE
    out["schema_file"] = os.path.basename(get_lab_view_paths().laser_lines_json)
    return out


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

    state = lab.get_lab_state()
    current_status = state.get("system_status")
    if current_status == "BUSY" or current_status == "OPTIMIZING":
        raise HTTPException(status_code=409, detail=f"System is {current_status}. Please wait.")

    try:
        cmd = parse_command_payload(payload)
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=validation_error_detail(e))

    _enforce_holding_rules(cmd, state)

    if isinstance(cmd, RecordMeasurablesBody):
        await execute_validated_command(lab, cmd)
        meas = fetch_read_primitive(lab, PrimitiveId.GET_MEASURABLES, cmd.target_id)
        return {"status": "ok", "measurables": meas}

    return schedule_validated_command(lab, cmd, background_tasks)

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
    file_path = os.path.join(RECIPES_DIR, f"{recipe_id}.json")
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Recipe not found")
    
    with open(file_path, "r") as f:
        data = json.load(f)
        recipe = Recipe(**data)
        
    state = lab.get_lab_state()
    if state.get("system_status") != "IDLE" and state.get("system_status") is not None:
         raise HTTPException(status_code=409, detail="System is busy")

    background_tasks.add_task(execute_recipe, recipe)
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
