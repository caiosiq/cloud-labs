from fastapi import FastAPI, HTTPException, BackgroundTasks, Request, Query, Body
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse, RedirectResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware
from pydantic import BaseModel, ValidationError
import json
import os
import re
import asyncio
import time
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple
import io
import logging

from lab_primitives import (
    ConfirmHoldingTagBody,
    HoverBody,
    MoveComponentBody,
    ObserveMeasurablesBody,
    PickComponentBody,
    PlaceFromHoverBody,
    PrimitiveId,
    ScanRotateInPlaceBody,
    execute_validated_command,
    fetch_read_primitive,
    parse_command_payload,
    schedule_validated_command,
    validation_error_detail,
)

logger = logging.getLogger(__name__)

# Load .env from project root (parent of backend/) so LAB_MODE and LAB_AUTOMATION_PATH are set
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_env_path = os.path.join(_project_root, ".env")
if os.path.exists(_env_path):
    from dotenv import load_dotenv
    load_dotenv(_env_path)
    print(f"[CONFIG] Loaded .env from {_env_path}")
else:
    print(f"[CONFIG] No .env at {_env_path}")

# Resolve LAB_AUTOMATION_PATH relative to project root so it works from backend/ cwd
_lab_path = os.getenv("LAB_AUTOMATION_PATH")
if _lab_path:
    _lab_path_abs = os.path.abspath(os.path.join(_project_root, _lab_path))
    os.environ["LAB_AUTOMATION_PATH"] = _lab_path_abs
    if not os.path.exists(_lab_path_abs):
        print(f"[CONFIG] Warning: LAB_AUTOMATION_PATH resolved to {_lab_path_abs} (path does not exist)")

# Import the new communicator
# from lab_communicator import MockLabCommunicator

app = FastAPI()

# Constants
SCHEMAS_DIR = os.path.join(os.path.dirname(__file__), "..", "schemas")
RECIPES_DIR = os.path.join(os.path.dirname(__file__), "..", "recipes")
STATES_DIR = os.path.join(os.path.dirname(__file__), "..", "states")

# Initialize Communicator
LAB_MODE = (os.getenv("LAB_MODE") or "MOCK").upper()
print(f"LAB_MODE: {LAB_MODE}")

if LAB_MODE == "REAL":
    try:
        from lab_communicator.real import RealLabCommunicator
        print(">>> STARTING IN REAL LAB MODE <<<")
        lab = RealLabCommunicator()
    except ImportError as e:
        print(f"CRITICAL ERROR: Failed to import RealLabCommunicator: {e}")
        print("Falling back to Mock Mode...")
        from lab_communicator.mock import MockLabCommunicator
        lab = MockLabCommunicator()
    except Exception as e:
        print(f"CRITICAL ERROR: Failed to initialize Real Lab: {e}")
        print("Falling back to Mock Mode...")
        from lab_communicator.mock import MockLabCommunicator
        lab = MockLabCommunicator()
else:
    print(">>> STARTING IN MOCK MODE <<<")
    try:
        from lab_communicator.mock import MockLabCommunicator
        lab = MockLabCommunicator()
    except Exception as e:
        print(f"CRITICAL ERROR: Failed to initialize Mock Lab Communicator: {e}")
        lab = None

# Ensure recipes directory exists
if not os.path.exists(RECIPES_DIR):
    os.makedirs(RECIPES_DIR)

# Ensure states directory exists
if not os.path.exists(STATES_DIR):
    os.makedirs(STATES_DIR)

# --- Laser line overlays (per LAB_MODE JSON under schemas/) ---
_LASER_LINES_FILES = {"REAL": "laser_lines.real.json", "MOCK": "laser_lines.mock.json"}
_LINE_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def _laser_lines_json_path() -> str:
    fn = _LASER_LINES_FILES.get(LAB_MODE, _LASER_LINES_FILES["MOCK"])
    return os.path.join(SCHEMAS_DIR, fn)


def _load_laser_lines_doc() -> Dict[str, Any]:
    path = _laser_lines_json_path()
    if not os.path.exists(path):
        return {"version": 1, "snap_line_id": None, "lines": []}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _atomic_write_json(path: str, data: Any) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def _two_points_to_ab(
    p1: Dict[str, Any], p2: Dict[str, Any]
) -> Optional[Tuple[float, float]]:
    """Return (a, b) for x = a*y + b in lab mm, or vertical (0, x0). None if degenerate."""
    try:
        x1 = float(p1["x"])
        y1 = float(p1["y"])
        x2 = float(p2["x"])
        y2 = float(p2["y"])
    except (TypeError, KeyError, ValueError):
        return None
    if abs(x2 - x1) < 1e-9 and abs(y2 - y1) < 1e-9:
        return None
    if abs(x2 - x1) < 1e-9:
        return 0.0, x1
    if abs(y2 - y1) < 1e-9:
        return None
    a = (x2 - x1) / (y2 - y1)
    b = x1 - a * y1
    return float(a), float(b)


def _laser_line_coeffs_from_doc(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Legacy single-line coefficients for GET /api/laser-line (snap reference)."""
    lines = doc.get("lines") or []
    snap_id = doc.get("snap_line_id")
    by_id = {
        ln["id"]: ln
        for ln in lines
        if isinstance(ln, dict) and isinstance(ln.get("id"), str)
    }
    chosen = None
    if snap_id and snap_id in by_id:
        cand = by_id[snap_id]
        if cand.get("enabled", True):
            chosen = cand
    if chosen is None:
        for ln in lines:
            if not isinstance(ln, dict):
                continue
            if ln.get("enabled", True) and ln.get("p1") and ln.get("p2"):
                chosen = ln
                break
    if chosen is None:
        return {
            "a": 0.0,
            "b": 0.0,
            "source": "schema",
            "loaded": False,
            "lab_mode": LAB_MODE,
        }
    ab = _two_points_to_ab(chosen["p1"], chosen["p2"])
    if ab is None:
        return {
            "a": 0.0,
            "b": 0.0,
            "source": "schema",
            "loaded": False,
            "lab_mode": LAB_MODE,
            "snap_line_id": chosen.get("id"),
        }
    a, b = ab
    return {
        "a": a,
        "b": b,
        "source": "schema",
        "loaded": True,
        "lab_mode": LAB_MODE,
        "snap_line_id": chosen.get("id"),
    }


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

@app.get("/api/catalog")
async def get_component_catalog():
    """
    Return the component catalog for the *currently running* lab mode.

    Mock and real labs load from *different* catalog files
    (``component_catalog.mock.json`` vs ``component_catalog.real.json``)
    so UI demos in mock mode can showcase parts the real table may not have
    physically installed yet. Resolved via ``lab.get_catalog()``.
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


@app.post("/api/components/{tag_id}/measurables/observe")
async def post_component_observe_measurables(tag_id: str):
    """Refresh measurables for one component (camera capture, etc.). Primitive: ``OBSERVE_MEASURABLES``."""
    if lab is None:
        raise HTTPException(status_code=503, detail="Lab not initialized")
    state = lab.get_lab_state()
    current_status = state.get("system_status")
    if current_status == "BUSY" or current_status == "OPTIMIZING":
        raise HTTPException(status_code=409, detail=f"System is {current_status}. Please wait.")
    await lab.observe_measurables_for_tag(tag_id)
    try:
        meas = fetch_read_primitive(lab, PrimitiveId.GET_MEASURABLES, tag_id)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=validation_error_detail(e))
    return {"status": "ok", "measurables": meas}


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

def _schedule_pose_refresh(background_tasks: BackgroundTasks) -> Dict[str, Any]:
    if lab is None:
        raise HTTPException(status_code=500, detail="Lab Communicator not initialized")
    fn = getattr(lab, "refresh_pose_from_camera", None)
    if fn is None and hasattr(lab, "refresh_state"):
        fn = lab.refresh_state
    if callable(fn):
        background_tasks.add_task(fn)
        return {
            "status": "accepted",
            "message": "Pose refresh from camera started (updates measurables.pose)",
        }
    return {
        "status": "ok",
        "message": "Pose refresh not supported for this lab backend",
    }


@app.post("/api/lab-state/refresh-pose")
async def refresh_lab_pose_from_camera(background_tasks: BackgroundTasks):
    """
    Re-localize component poses from the overhead / table camera (real: full scan; mock: simulated noise).
    """
    return _schedule_pose_refresh(background_tasks)


@app.post("/api/lab-state/refresh")
async def refresh_lab_state_legacy(background_tasks: BackgroundTasks):
    """Deprecated: use ``POST /api/lab-state/refresh-pose`` (same behavior)."""
    return _schedule_pose_refresh(background_tasks)

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
    from lab_model.storage_region import analyze_layout_issues

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
    from lab_model.storage_region import storage_grid_spec

    return storage_grid_spec()


@app.get("/api/laser-line")
async def get_laser_line():
    """Single-line legacy coefficients (x = a*y + b, mm) from schemas laser_lines.*.json snap line."""
    doc = _load_laser_lines_doc()
    return _laser_line_coeffs_from_doc(doc)


@app.get("/api/laser-lines")
async def get_laser_lines():
    """All laser overlays for the current LAB_MODE (schemas/laser_lines.{real|mock}.json)."""
    doc = _load_laser_lines_doc()
    out = dict(doc)
    out["lab_mode"] = LAB_MODE
    out["schema_file"] = os.path.basename(_laser_lines_json_path())
    return out


@app.patch("/api/laser-lines/{line_id}")
async def patch_laser_line(line_id: str, payload: Dict[str, Any] = Body(...)):
    """Update one line: ``enabled`` anytime; ``p1``/``p2`` only with ``confirm: true``."""
    if not _LINE_ID_RE.match(line_id or ""):
        raise HTTPException(status_code=400, detail="Invalid line id")
    doc = _load_laser_lines_doc()
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
        if _two_points_to_ab(p1f, p2f) is None:
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
    _atomic_write_json(_laser_lines_json_path(), doc)
    out = dict(_load_laser_lines_doc())
    out["lab_mode"] = LAB_MODE
    out["schema_file"] = os.path.basename(_laser_lines_json_path())
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

    if isinstance(cmd, ObserveMeasurablesBody):
        await execute_validated_command(lab, cmd)
        meas = fetch_read_primitive(lab, PrimitiveId.GET_MEASURABLES, cmd.target_id)
        return {"status": "ok", "measurables": meas}

    return schedule_validated_command(lab, cmd, background_tasks)

# --- Video Feed Endpoints ---

@app.get("/api/table-cam/capture")
async def table_cam_capture(
    cam_id: int = 1,
    exposure: float = Query(
        0.2,
        ge=0.001,
        le=30.0,
        description="Exposure (seconds) for both video and capture; passed to table-cam pipeline.",
    ),
):
    """Capture one PNG from table recorder camera (1 or 2). Real: hardware; MOCK: synthetic image for UI/testing."""
    if lab is None:
        raise HTTPException(status_code=503, detail="Lab not initialized")
    if not hasattr(lab, "capture_table_cam"):
        raise HTTPException(status_code=503, detail="Table cam capture not available (real lab only)")
    if cam_id not in (1, 2):
        raise HTTPException(status_code=400, detail="cam_id must be 1 or 2")
    data = lab.capture_table_cam(cam_id, exposure=float(exposure))
    if data is None:
        raise HTTPException(status_code=503, detail="Capture failed or table cams not available")
    return Response(content=data, media_type="image/png")


@app.get("/api/cobyla-reference-image")
async def cobyla_reference_image_get():
    """Return the stored Cobyla reference as PNG (for UI preview and download)."""
    if lab is None:
        raise HTTPException(status_code=503, detail="Lab not initialized")
    if not hasattr(lab, "get_cobyla_reference_png_bytes"):
        raise HTTPException(status_code=503, detail="Cobyla reference preview not available")
    data = lab.get_cobyla_reference_png_bytes()
    if not data:
        raise HTTPException(status_code=404, detail="No Cobyla reference set")
    return Response(content=data, media_type="image/png")


@app.post("/api/cobyla-reference-image")
async def cobyla_reference_image_upload(request: Request):
    """
    Store a PNG as CobylaAlignmentStrategy.reference_image (BGR ndarray, same class of image as table-cam capture).
    Body: raw PNG bytes, Content-Type image/png recommended.
    """
    if lab is None:
        raise HTTPException(status_code=503, detail="Lab not initialized")
    if not hasattr(lab, "set_cobyla_reference_from_png_bytes"):
        raise HTTPException(status_code=503, detail="Cobyla reference storage not available")
    body = await request.body()
    ok, msg = lab.set_cobyla_reference_from_png_bytes(body)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"status": "success", "message": msg}


@app.get("/api/cobyla-reference-image/status")
async def cobyla_reference_image_status():
    if lab is None:
        raise HTTPException(status_code=503, detail="Lab not initialized")
    if not hasattr(lab, "get_cobyla_reference_status"):
        raise HTTPException(status_code=503, detail="Cobyla reference status not available")
    out = dict(lab.get_cobyla_reference_status())
    out["lab_mode"] = LAB_MODE
    return out


@app.delete("/api/cobyla-reference-image")
async def cobyla_reference_image_clear():
    if lab is None:
        raise HTTPException(status_code=503, detail="Lab not initialized")
    if hasattr(lab, "clear_cobyla_reference"):
        lab.clear_cobyla_reference()
    return {"status": "success", "message": "Cobyla reference cleared"}


@app.get("/api/video-feed/status")
async def get_video_status():
    """Check if the live feed is available"""
    return lab.get_video_feed_status()

@app.get("/api/video-feed/stream")
async def get_video_stream(fps: int = 10):
    """
    Returns a mock image or real stream
    """
    # print(f"[{datetime.now().strftime('%H:%M:%S')}] Request: GET /api/video-feed/stream") # Optional: uncomment to log video requests
    
    if LAB_MODE == "REAL":
        return StreamingResponse(
            lab.get_video_stream(fps=fps), 
            media_type="multipart/x-mixed-replace; boundary=frame"
        )
    else:
        # Serve the SVG directly instead of redirecting
        return FileResponse(os.path.join(frontend_path, "mock_feed.svg"))


@app.get("/api/optimization-feed/stream")
async def get_optimization_feed_stream(fps: int = 5):
    """
    Returns an MJPEG stream of the optimization images.
    """
    if LAB_MODE == "REAL" and hasattr(lab, "get_optimization_stream"):
        return StreamingResponse(
            lab.get_optimization_stream(fps=fps), 
            media_type="multipart/x-mixed-replace; boundary=frame"
        )
    else:
        # Serve the SVG directly instead of redirecting
        return FileResponse(os.path.join(frontend_path, "mock_feed.svg"))


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
