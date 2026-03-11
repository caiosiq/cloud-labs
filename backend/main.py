from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse, RedirectResponse, Response
from pydantic import BaseModel
import json
import os
import asyncio
from datetime import datetime
from typing import Dict, Any, List, Optional
import io

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

# --- Recipe Executor (Uses Communicator) ---

async def execute_recipe(recipe: Recipe):
    print(f"[RECIPE] Starting recipe: {recipe.name}")
    
    for step in recipe.steps:
        print(f"[RECIPE] Executing Step {step.step}: {step.action}")
        
        target = step.component or step.target
        
        if step.action == "PLACE" or step.action == "MOVE_COMPONENT":
            await lab.move_component(target, step.parameters)
        elif step.action == "OPTIMIZE":
            strategy = step.parameters.get("strategy", "NEWTON")
            await lab.optimize_component(target, strategy, step.parameters)
        elif step.action == "REMOVE":
            await lab.remove_component(target)
            
        await asyncio.sleep(0.5)
        
    print(f"[RECIPE] Recipe {recipe.name} complete. Saving Golden State...")
    
    # Save Golden State using Lab State
    state = lab.get_lab_state()
    golden_state = {
        "recipe_id": recipe.id,
        "timestamp": datetime.now().isoformat(),
        "components": state.get("components", {}),
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

app.mount("/static", StaticFiles(directory=frontend_path), name="static")

@app.get("/")
async def read_index():
    return FileResponse(os.path.join(frontend_path, "index.html"))

@app.get("/api/catalog")
async def get_component_catalog():
    catalog_path = os.path.join(SCHEMAS_DIR, "component_catalog.json")
    if not os.path.exists(catalog_path):
        return []
    with open(catalog_path, "r") as f:
        return json.load(f)

@app.post("/api/components")
async def add_component(payload: Dict[str, Any], background_tasks: BackgroundTasks):
    """
    Simulates "Request to Place": adds a component to the lab state.
    In a real lab, this would notify an operator or robot.
    In Mock mode, it directly updates the state.
    """
    background_tasks.add_task(lab.add_component_to_state, payload)
    return {"status": "accepted", "message": f"Request to place {payload.get('name')} submitted."}

@app.get("/api/lab-state")
async def get_lab_state():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Request: GET /api/lab-state")
    if lab is None:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Response: 500 Lab Communicator Not Initialized")
        raise HTTPException(status_code=500, detail="Lab Communicator failed to initialize. Check server logs.")
    try:
        state = lab.get_lab_state()
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Response: 200 OK (State sent)") 
        return JSONResponse(content=state)
    except Exception as e:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Response: 500 Failed to read state: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to read Lab State: {str(e)}")

@app.get("/api/laser-line")
async def get_laser_line():
    """Laser path in lab coords: x = a*y + b (mm). Mock: fixed params; Real: from laser_line_fit.npy."""
    if LAB_MODE != "REAL" or lab is None:
        return {"a": 0.0, "b": 0.0, "source": "mock"}
    path = os.path.join(_project_root, "laser_line_fit.npy")
    if not os.path.exists(path):
        return {"a": 0.0, "b": 0.0, "source": "real", "loaded": False}
    try:
        import numpy as np
        data = np.load(path)
        a, b = float(data[0]), float(data[1])
        return {"a": a, "b": b, "source": "real", "loaded": True}
    except Exception as e:
        print(f"[CONFIG] Failed to load laser_line_fit.npy: {e}")
        return {"a": 0.0, "b": 0.0, "source": "real", "loaded": False}

@app.post("/api/command")
async def receive_command(payload: Dict[str, Any], background_tasks: BackgroundTasks):
    print(f"Received Command: {payload}")
    
    action = payload.get("action")
    target_id = payload.get("target_id")
    params = payload.get("parameters", {})
    
    state = lab.get_lab_state()
    current_status = state.get("system_status")
    if current_status == "BUSY" or current_status == "OPTIMIZING":
         raise HTTPException(status_code=409, detail=f"System is {current_status}. Please wait.")

    if action == "MOVE_COMPONENT":
        # Extract type if present in parameters
        # params already has it if sent by frontend
        background_tasks.add_task(lab.move_component, target_id, params)
        return {"status": "accepted", "message": f"Robot dispatched to move {target_id}"}

    elif action == "MOVE_MOTOR":
        motor_id = params.get("motor_id")
        distance = params.get("distance")
        if motor_id is None or distance is None:
             raise HTTPException(status_code=400, detail="MOVE_MOTOR requires 'motor_id' and 'distance'")
        background_tasks.add_task(lab.move_motor, target_id, motor_id, distance)
        return {"status": "accepted", "message": f"Motor {motor_id} on {target_id} moving by {distance}"}
    
    elif action == "OPTIMIZE":
        strategy = params.get("strategy", "NEWTON")
        background_tasks.add_task(lab.optimize_component, target_id, strategy, params)
        return {"status": "accepted", "message": f"Optimization ({strategy}) started for {target_id}"}
    
    elif action == "SCAN":
        return {"status": "accepted", "message": "Scan started"}
    
    else:
        raise HTTPException(status_code=400, detail="Unknown action")

# --- Video Feed Endpoints ---

@app.get("/api/table-cam/capture")
async def table_cam_capture(cam_id: int = 1):
    """Capture one image from table recorder camera (1 or 2). Real lab only; on-demand (no stream)."""
    if lab is None:
        raise HTTPException(status_code=503, detail="Lab not initialized")
    if not hasattr(lab, "capture_table_cam"):
        raise HTTPException(status_code=503, detail="Table cam capture not available (real lab only)")
    if cam_id not in (1, 2):
        raise HTTPException(status_code=400, detail="cam_id must be 1 or 2")
    data = lab.capture_table_cam(cam_id)
    if data is None:
        raise HTTPException(status_code=503, detail="Capture failed or table cams not available")
    return Response(content=data, media_type="image/png")

@app.get("/api/video-feed/status")
async def get_video_status():
    """Check if the live feed is available"""
    return lab.get_video_feed_status()

@app.get("/api/video-feed/stream")
async def get_video_stream():
    """
    Returns a mock image or real stream
    """
    # print(f"[{datetime.now().strftime('%H:%M:%S')}] Request: GET /api/video-feed/stream") # Optional: uncomment to log video requests
    
    if LAB_MODE == "REAL":
        return StreamingResponse(
            lab.get_video_stream(), 
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
            g_pose = g_comp.get("pose", {})
            c_pose = c_comp.get("pose", {})
            
            dx = g_pose.get("x", 0) - c_pose.get("x", 0)
            dy = g_pose.get("y", 0) - c_pose.get("y", 0)
            dist = (dx*dx + dy*dy)**0.5
            
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
            
    report["total_drift_mm"] = round(total_drift, 4)
    return report

@app.get("/api/debug/ghost-state")
async def get_ghost_state():
    """
    Constructs the 'Ghost State' (Tier 2) from the Lab State (Tier 1).
    In a real app, this might be stored separately, but here it's derived
    from the 'intent' fields in the lab state.
    """
    state = lab.get_lab_state()
    ghost_state = {}
    
    if "components" in state:
        for name, comp in state["components"].items():
            if "intent" in comp:
                ghost_state[name] = comp["intent"]
            else:
                # Fallback if no intent exists
                ghost_state[name] = {
                    "nominal_pose": comp.get("pose"),
                    "placement_strategy": "UNKNOWN",
                    "is_optimized": False
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
    return FileResponse(os.path.join(frontend_path, "debug.html"))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
