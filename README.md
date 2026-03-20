# Optical Digital Twin — Robotic Lab Interface

## Project overview

This repository is a **digital twin** for an autonomous optics lab: a browser-based workspace where you lay out experiments (mirrors, lenses, cameras, filters, etc.), send **move** and **optimize** commands to a backend, and optionally drive a physical **xArm6** setup through a `lab_automation` integration.

The UI separates **what you intend** (ghost / nominal poses on the canvas) from **what the lab reports** (solid geometry from polled state), including recipe replay and **golden** snapshots for drift checks.

---

## What you see in the app

- **Dark, lab-style UI** (Inter typography, Material Icons): main table in the center, **left** sidebar for placed components and selection, **right** sidebar for live/overhead-style video, table-camera capture, recipes, and activity log.
- **2D breadboard canvas** (HTML5 Canvas): metric coordinates (~±500 mm), grid, robot-base **danger zone**, and a **laser path** overlay (mock coefficients by default; in real mode from `laser_line_fit.npy` at the repo root when present).
- **Solid vs ghost**: placed components draw twice—opaque **physical** pose and semi-transparent **intent** pose with a dashed “drift” segment when they differ.
- **Interaction**: select a part, edit X/Y/rotation, **move** (with collision checks and confirm for non-recording moves), drag on canvas with optional **snap toward the laser line**, wheel to rotate while dragging, and **optimization** strategies (e.g. Newton / Cobyla) from the context panel. **Motor jog** controls appear for catalog entries that declare `motor_ids`.
- **Recipes**: record MOVE/OPTIMIZE steps, save under `recipes/`, play back via the API; successful runs can emit a `{recipe_id}_golden.json` reference.
- **Saved layouts**: **Save / Load lab state** writes JSON under `states/` (mock-friendly; useful for repeatable demos).
- **Debug page** at `/debug` for deeper inspection (ghost derivation, golden listing, etc.).

---

## Architecture

### Four tiers of state (conceptual)

1. **Tier 1 — Lab state (physical truth)**  
   Authoritative snapshot from the communicator: component poses, `system_status` (`IDLE`, `BUSY`, `OPTIMIZING`, …), timestamps, and per-component **`intent`** (nominal vs optimized metadata) when present.

2. **Tier 2 — Ghost / intent (what the UI plans)**  
   Client-side **ghost** poses track targets; the backend can embed **`intent`** on each component (`nominal_pose`, `placement_strategy`, `last_optimized_pose`, `is_optimized`). Debug: `GET /api/debug/ghost-state` derives a ghost view from stored intent.

3. **Tier 3 — Recipe (procedure)**  
   JSON sequences of steps (`MOVE_COMPONENT`, `OPTIMIZE`, `PLACE`, `REMOVE`, …) stored in `recipes/{id}.json`, played asynchronously by the server.

4. **Tier 4 — Golden state (reference)**  
   After a successful recipe run, a snapshot may be saved as `recipes/{id}_golden.json` for drift comparison via `GET /api/recipes/{id}/compare`.

### Runtime stack

| Layer | Technology |
|--------|------------|
| **Frontend** | Static HTML/CSS/JS (no SPA framework); canvas rendering, `fetch` polling |
| **Backend** | FastAPI (`backend/main.py`), serves `/` and `/debug` with no-cache headers and version-busted `app.js` |
| **Static assets** | Mounted at `/static` → `frontend/` |
| **Hardware** | **LabCommunicator** abstraction: `MockLabCommunicator` \| `RealLabCommunicator` |

### Command–query style

- **Query**: browser polls **`GET /api/lab-state`** (~every 500 ms) to refresh solids, status, and intent-driven ghost sync after commands finish.
- **Command**: **`POST /api/command`** with actions such as `MOVE_COMPONENT`, `MOVE_MOTOR`, `OPTIMIZE`. Successful accepts return **HTTP 200** with `"status": "accepted"` in the JSON body; **`409`** if the lab reports `BUSY` / `OPTIMIZING`.
- **Placement request**: **`POST /api/components`** queues `add_component_to_state` (mock vs real behavior lives in the communicator).

### Real lab mode

When `LAB_MODE=REAL` and `lab_automation` imports succeed, **`RealLabCommunicator`** wraps **`OpticalExperiment`**, initializes the robot, scans/populates state, and can expose MJPEG streams and table-camera PNG capture. If imports or initialization fail, the server **falls back to mock** with a log message.

---

## Repository layout

```
optics-digital-twin/
├── .env                      # Optional: LAB_MODE, LAB_AUTOMATION_PATH (loaded from repo root)
├── backend/
│   ├── main.py               # FastAPI app, REST routes, static mount, recipe executor
│   └── lab_communicator/
│       ├── base.py           # LabCommunicator interface
│       ├── mock.py           # Simulated lab (delays, noise, local JSON state)
│       └── real.py           # Adapter for external lab_automation package
├── frontend/                 # index.html, app.js, styles, mock video SVG, debug.html
├── schemas/                  # JSON contracts & reference data
│   ├── component_catalog.json
│   ├── mock_lab_state.json   # Seed / reference for mock
│   └── …                     # e.g. client_payload, strategies examples
├── recipes/                  # Saved recipes + optional *_golden.json
├── states/                   # User-saved lab state snapshots (API)
├── requirements.txt          # Python dependencies (install from repo root)
├── ROADMAP.md
├── laser_line_fit.npy        # Optional: real-mode laser line (x = a*y + b)
└── Camera_Images/            # Optimization frames may be read/watched here (real workflows)
```

The `backend-simple/` folder holds small lab-related Python snippets with **relative imports** meant for use inside a larger **`lab_automation`** tree; it is **not** the FastAPI entrypoint.

---

## Main HTTP API (short reference)

| Method | Path | Role |
|--------|------|------|
| GET | `/` | Main UI |
| GET | `/debug` | Debugger / visualizer |
| GET | `/api/catalog` | Component catalog |
| GET | `/api/lab-state` | Current lab JSON |
| POST | `/api/lab-state/refresh` | Trigger rescan / mock reload (background if supported) |
| POST | `/api/components` | Request add-to-lab (catalog item payload) |
| POST | `/api/command` | Move / motor / optimize |
| GET | `/api/laser-line` | Laser line coefficients `{ a, b, source, … }` |
| GET | `/api/video-feed/status` | Stream availability + source URL |
| GET | `/api/video-feed/stream` | MJPEG (real) or static mock SVG |
| GET | `/api/optimization-feed/stream` | Optimization MJPEG when supported |
| GET | `/api/table-cam/capture?cam_id=1\|2` | Single PNG (real) |
| GET/POST | `/api/recipes`, `/api/recipes/{id}/play`, `/api/recipes/{id}/golden`, `/api/recipes/{id}/compare` | Recipe CRUD, play, golden, drift report |
| GET/POST | `/api/states`, `/api/states/save`, `/api/states/load` | List / save / load snapshots in `states/` |
| GET | `/api/debug/ghost-state`, `/api/debug/golden-states` | Debug aggregates |

---

## Setup and run

### 1. Install dependencies

From the **repository root**:

```bash
pip install -r requirements.txt
```

Mock mode only needs the **FastAPI** stack (`fastapi`, `uvicorn`, `pydantic`, `python-dotenv`, `numpy` for laser file loading in real paths). Full `requirements.txt` also lists vision/robot packages used when you point `LAB_AUTOMATION_PATH` at a real `lab_automation` checkout.

### 2. Configuration (optional)

Create or edit **`.env`** in the **project root** (same folder as `requirements.txt`). Example:

```env
LAB_MODE=MOCK
# Path to the folder that contains the lab_automation package (parent is added to sys.path)
LAB_AUTOMATION_PATH=../lab_automation
```

`backend/main.py` resolves `LAB_AUTOMATION_PATH` to an absolute path relative to the project root. **`LAB_MODE`** defaults to **`MOCK`** if unset.

**Console noise (polling):** the UI hits `/api/lab-state` about twice per second. Those messages are no longer printed at info level. If you start the app with **`python main.py`**, Uvicorn **access** logging (every `GET … HTTP/1.1` line) is **off** by default; set **`UVICORN_ACCESS_LOG=1`** in `.env` to turn it back on. To see per-poll debug lines from our handler, set **`LOG_LEVEL=DEBUG`**. If you use **`uvicorn main:app`** directly, add **`--no-access-log`** unless you want the access log.

### 3. Start the server

```bash
cd backend
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

- **Main UI:** http://localhost:8000/  
- **Debug:** http://localhost:8000/debug  

### 4. Real lab mode

Set `LAB_MODE=REAL` and a valid `LAB_AUTOMATION_PATH` so `from lab_automation...` imports work. Expect robot/camera initialization, optional recorder subprocesses, and live streams when hardware is available. If initialization fails, the process **logs the error and stays in mock**.

---

## Typical workflow

1. **Add parts** — Open the catalog, **Request** items; in mock this updates state quickly; in real lab this ties to your automation policy.
2. **Place and align** — Drag on the canvas or use the context panel; confirm moves; run **Optimize** with strategy parameters.
3. **Record a recipe** — Toggle record, perform actions, save; play back from the sidebar.
4. **Drift / golden** — After a good run, a golden file may exist; use **Debug** or `GET /api/recipes/{id}/compare` to compare poses to the current lab state.
5. **Snapshots** — Use **Save / Load state** to persist JSON under `states/`.

---

## Extending the inventory

1. Tag physical parts (e.g. ArUco) consistently with your vision stack.
2. Add or edit **`schemas/component_catalog.json`** (`tag_id`, type, size, optional `motor_ids`, properties).
3. Restart the backend so **`GET /api/catalog`** picks up changes.

See **`ROADMAP.md`** for planned features (auto drift correction, richer simulation, etc.).
