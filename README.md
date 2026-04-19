# Optical Digital Twin — Robotic Lab Interface

## Project overview

This repository is a **digital twin** for an autonomous optics lab: a browser-based workspace where you lay out experiments (mirrors, lenses, cameras, filters, etc.), send **move** and **optimize** commands to a backend, and optionally drive a physical **xArm6** setup through a `lab_automation` integration.

The UI separates **what you intend** (ghost / nominal poses on the canvas) from **what the lab reports** (solid geometry from polled state), including recipe replay and **golden** snapshots for drift checks.

**Design reference:** **`model.md`** explains **return vs observe** for tunables/measurables; **`primitives.md`** lists HTTP primitives and routes. Implementation detail: **`backend/lab_primitives/README.md`**.

---

## Why two worlds? `lab_automation`, experiment manager, and this repository

If you have spent time in both places, it can feel as though you are **writing the same functions twice**: on one side, the **`lab_automation`** tree gives you an **experiment manager** built from modular optical components; on the other, **`LabCommunicator`** in this repo exposes **move**, **optimize**, and **motor** actions that ultimately call into that same stack. That overlap is real—and **deliberate**.

**Start from the physical lab.** In **`lab_automation`**, components are represented in a modular way: mirrors, stages, cameras, and so on. The **experiment manager** gathers those pieces into something you can program: a set of **procedures** for building experimental plans. That power comes with **everything the hardware demands**: defining and finding components, bringing up cameras, speaking the robot’s native language, and handling failures at the level of serial ports, joint commands, and OpenCV arrays. Those APIs are **hardware-bound**: they are the truth of *this* arm, *this* camera driver, *this* wiring.

**This project adds a second layer on top.** When you start the digital twin in real mode, **`RealLabCommunicator`** does the heavy setup once—scanning the table, wiring the experiment object, aligning conventions—so that the **browser** (and anything else speaking HTTP) does not have to repeat that ceremony. From the outside, the contract is intentionally narrow: **`POST /api/command`** carries **intent** in a small vocabulary—move a tagged part to a pose, run an optimization strategy, jog a motor, refresh poses from the camera. The UI thinks in **lab frame** coordinates (what you see on the table and on camera overlays); the communicator is responsible for mapping that intent onto whatever **robot-frame** calls the experiment manager expects, including details that would change if you swapped hardware.

So the two layers live in **different domains**:

| Layer | What it knows | What it is for |
|--------|----------------|----------------|
| **Experiment manager** (`lab_automation`) | How to talk to the xArm6, how to interpret image arrays, how to sequence low-level motor and placement calls | **Hardware-accurate** experimental scripts: the full power—and full fragility—of the real system |
| **Lab communicator + HTTP API** (this repo) | What a human or an automation client **wants** to happen next, in a **stable, safe payload** | **Intent**: the same mental model whether the backend is mock or real |

If tomorrow you replace the xArm6 with a UR10, **reimplement or reconfigure the experiment manager and the robot drivers**—the commands at that level **will** change. But the **UI**, the **Command Console** (see **`coding_on_the_ui.md`** and **`frontend/js/command-console.js`**), and the **JSON** shape of **`/api/command`** can stay the same: they are not tied to a particular vendor pose format. The apparent redundancy between “experiment manager functions” and “communicator functions” is an **abstraction boundary**. You pay a thin translation layer so that everything above it—canvas, recipes, future LLM-driven plans—does not get rewritten when the bench changes.

Historically, this repository began as **“cloud-labs”** in the sense of **a remote-facing UI** for the lab. The direction now is broader: treat the stack as a **durable language for running experiments**—commands, sequences, and eventually richer scripting—while keeping the **robot and vision specifics** fenced behind the communicator. That separation is what lets you iterate on **how people and tools ask for work** without constantly revisiting **how the arm moves**.

In **real** mode, **`RealLabCommunicator`** maps **lab-frame** intent (what the UI sends) onto **robot-frame** and experiment-manager calls inside **`lab_automation`**—details live in **`lab_communicator/real.py`**.

---

## What you see in the app

- **Dark, lab-style UI** (Inter typography, Material Icons): main table in the center, **left** sidebar for placed components and selection, **right** sidebar for live/overhead-style video, table-camera capture, recipes, and activity log.
- **2D breadboard canvas** (HTML5 Canvas): metric coordinates (~±500 mm), grid, robot-base **danger zone**, and a **laser path** overlay (mock coefficients by default; in real mode from `laser_line_fit.npy` at the repo root when present).
- **Solid vs ghost**: placed components draw twice—opaque **physical** pose and semi-transparent **intent** pose with a dashed “drift” segment when they differ.
- **Interaction**: select a part; **move**, **optimize**, **motors**, and **observe** live in a **floating panel** docked to the **top-right of the table** (over the canvas). The **left sidebar** lists components plus **Refresh Pose** and **Save / Load state**. Drag on canvas with optional **snap toward the laser line**; wheel to rotate while dragging. **Motor jog** appears for catalog entries that declare `motor_ids`.
- **Recipes**: record MOVE/OPTIMIZE steps, save under `recipes/`, play back via the API; successful runs can emit a `{recipe_id}_golden.json` reference.
- **Saved layouts**: **Save / Load lab state** writes JSON under `states/` (mock-friendly; useful for repeatable demos).
- **Command Console** (bottom of the main page): typed shorthand for moves, optimize, and related actions; backed by ES modules (`command-parse.js`, `command-api.js`, `command-complete.js`, …) and wired through **`window.__commandConsoleDeps`** (see **Frontend code layout** below).
- **Debug page** at `/debug` for deeper inspection (ghost derivation, golden listing, etc.).

---

## Frontend code layout

There is **no bundler or SPA framework**: the browser loads **native ES modules** from `/static` (the `frontend/` tree). Styling remains mostly **inline in `index.html`**; behavior is split between a **main app bundle** and the **Command Console** stack.

### Load order

`index.html` registers two module scripts **in this order**:

1. **`/static/js/main.js`** — runs the lab UI and defines **`window.__commandConsoleDeps`** before the console needs it.
2. **`/static/js/command-console.js`** — shell, history, tab completion; reads **`__commandConsoleDeps`** for `sendCommand`, `log`, `render`, lab state, ghost state, and collision checks.

The root route rewrites **`js/main.js?v=…`** in the served HTML with a startup timestamp so refreshes pick up changes while **`NoCacheMiddleware`** still applies **`no-store`** on `/static` in development.

### Module map

| File | Role |
|------|------|
| **`js/main.js`** | Entry: imports **`bootstrap.js`**. |
| **`js/bootstrap.js`** | Side-effect import of **`app-main.js`** (starts the app). |
| **`js/app-main.js`** | Main UI: DOM hooks, **`fetch`** polling, canvas draw/interaction, sidebars (components, context, recipes, library), modals, video / table-cam / Cobyla UI, **`init()`**, and assignment of **`window.__commandConsoleDeps`**. |
| **`js/config.js`** | Canvas size, lab bounds (mm), derived scale, breadboard grid offset, **`POLLING_INTERVAL`**. |
| **`js/state/store.js`** | Single mutable **`store`** object: `labState`, `ghostState`, `catalogMap`, selection, optimization flags, recipe recording buffers, table-cam selection, etc. |
| **`js/canvas/coordinates.js`** | **`mmToPx`** / **`pxToMm`** for the lab frame (origin at table center, +Y up on screen). |
| **`js/command-console.js`** | Console UI loop; depends on **`command-parse.js`**, **`command-api.js`**, **`command-complete.js`**. |

Larger slices of logic (dedicated API client, separate render module) can be peeled out of **`app-main.js`** over time.

---

## Architecture

### Four tiers of state (conceptual)

1. **Tier 1 — Lab state (physical truth)**  
   Authoritative snapshot from the communicator: `system_status` (`IDLE`, `BUSY`, `OPTIMIZING`, …), timestamps, and per-component **`measurables`** (measured pose, optimization score, optional camera image ref) plus **`tunables`** (nominal pose, storage slot intent, placement mode). Shapes and helpers are documented in **`lab_model`** (see **`backend/lab_model/README.md`**).

2. **Tier 2 — Ghost / intent (what the UI plans)**  
   Client-side **ghost** poses track targets; the backend stores commanded values under **`tunables`** (`nominal_pose`, `placement.mode`, `storage`, `presence`). Debug: `GET /api/debug/ghost-state` exposes tunables-derived intent. **`GET /api/components/{tag_id}/tunables`** and **`/measurables`** return slices for one tag.

3. **Tier 3 — Recipe (procedure)**  
   JSON sequences of steps (`MOVE_COMPONENT`, `OPTIMIZE`, `PLACE`, `REMOVE`, …) stored in `recipes/{id}.json`, played asynchronously by the server.

4. **Tier 4 — Golden state (reference)**  
   After a successful recipe run, a snapshot may be saved as `recipes/{id}_golden.json` for drift comparison via `GET /api/recipes/{id}/compare`.

### Runtime stack

| Layer | Technology |
|--------|------------|
| **Frontend** | Static HTML/CSS; **ES modules** (`js/main.js` → `app-main.js`); canvas; `fetch` polling; Command Console modules |
| **Backend** | FastAPI (`backend/main.py`), serves `/` and `/debug` with no-cache headers and version-busted `js/main.js` |
| **Static assets** | Mounted at `/static` → `frontend/` |
| **Hardware** | **LabCommunicator** abstraction: `MockLabCommunicator` \| `RealLabCommunicator` |

### Backend packages (how `main.py` is organized)

| Package | Role |
|---------|------|
| **`lab_model`** | **Domain model** shared by mock and real: **tunables vs measurables** helpers (`component_model.py`), **storage quadrant Q3** geometry and layout checks (`storage_region.py`), **software-tracked motor angles** on disk (`motor_rotation_store` → `schemas/mock_motor_rotations.json` / `real_motor_rotations.json`). Does **not** talk to hardware. See **`backend/lab_model/README.md`**. |
| **`lab_primitives`** | **HTTP-facing command contract**: `PrimitiveId`, **Pydantic** bodies for `POST /api/command`, **`PRIMITIVE_REGISTRY`**, **`dispatch`** (`parse_command_payload`, `execute_validated_command`, `schedule_validated_command`), **read primitives** for **`GET /api/components/{tag}/tunables`** and **`.../measurables`**, and **macros** that compose atomic steps (today: `MOTOR_SEND_HOME` → tracked angle + `MOVE_MOTOR`). Design narrative: **`primitives.md`**. Package overview + roadmap: **`backend/lab_primitives/README.md`**, **`backend/lab_primitives/ROADMAP.md`**. |
| **`lab_communicator`** | **`LabCommunicator`** interface and **mock** / **real** implementations: lab state JSON, robot/vision/`lab_automation` integration. |

**`main.py`** delegates command validation and scheduling to **`lab_primitives`** (same path for the recipe executor). Tunables/measurables per tag use **`fetch_read_primitive`** so reads stay aligned with the primitive vocabulary.

### Command–query style

- **Query**: browser polls **`GET /api/lab-state`** (~every 500 ms) to refresh solids, status, and **`lab_mode`**. Ghost sync: after commands finish (or while **`OPTIMIZING`** in real mode—see **Newton optimization in real mode** below); **`tunables.nominal_pose`** drives the ghost overlay when present. Per-tag slices: **`GET /api/components/{tag_id}/tunables`** and **`.../measurables`** (saved state only; **`lab_primitives`** **return** primitives).
- **Observe**: **`POST /api/components/{tag_id}/measurables/observe`** (or **`POST /api/command`** with **`OBSERVE_MEASURABLES`**) refreshes that tag’s measurables (e.g. camera capture → **`camera_image`**). Same **`409`** guard as commands when the system is **`BUSY`** / **`OPTIMIZING`**. See **`model.md`**.
- **Command**: **`POST /api/command`** with JSON `{ "action", "target_id", "parameters" }`. Bodies are **validated** by **`lab_primitives`** (Pydantic); actions include `MOVE_COMPONENT`, `MOVE_MOTOR`, `OPTIMIZE`, `STORE_COMPONENT`, …. Successful accepts return **HTTP 200** with `"status": "accepted"`; **`409`** if the lab reports `BUSY` / `OPTIMIZING`; **`400`/`422`** on invalid payloads.
- **Placement request**: **`POST /api/components`** queues `add_component_to_state` (mock vs real behavior lives in the communicator).

### Real lab mode

When `LAB_MODE=REAL` and `lab_automation` imports succeed, **`RealLabCommunicator`** wraps **`OpticalExperiment`**, initializes the robot, scans/populates state, and can expose MJPEG streams and table-camera PNG capture. If imports or initialization fail, the server **falls back to mock** with a log message.

**`LAB_AUTOMATION_PATH`:** set this to the filesystem path of the **`lab_automation` package directory itself** (the folder that contains `__init__.py` for that package). The backend adds that folder’s **parent** to `sys.path` so `import lab_automation` works. A path that stops at the parent of `lab_automation` is wrong.

Each **`GET /api/lab-state`** response includes **`lab_mode`**: `"MOCK"` or `"REAL"` (for UI behavior such as mock-only overlays).

---

## Newton optimization in real mode (ghost vs solid)

This is easy to misunderstand because **two different poses** drive the canvas, and **when** the UI reads them changes between normal operation and optimization.

### What the canvas shows

| Layer | Source in API | Meaning |
|--------|----------------|--------|
| **Solid** (opaque) | `components[id].measurables.pose` | Best current model of **where the part is on the table** (after a completed move / place). |
| **Ghost** (semi-transparent) | Client **`ghostState`**, synced from **`components[id].tunables.nominal_pose`** when applicable | **Target / intent** pose—where you are asking the system to put the part, or where the optimizer is **heading** on the next sub-step. |

Normally the frontend only resyncs ghost from the server when a command **finishes** (or you force refresh), so the dashed “drift” line is stable while something is running.

During **`system_status === "OPTIMIZING"`** (real Newton runs), the client **also** refreshes ghost from **`tunables.nominal_pose` on every poll** (~500 ms). That way you can see the **planned** sub-target move ahead of or separate from the **solid** pose.

### What the backend does during Newton (`RealLabCommunicator`)

Optimization runs in a **worker thread** (`asyncio.to_thread`), while **`GET /api/lab-state`** is served on the main event loop. To avoid torn reads, **`get_lab_state()`** returns a **deep copy** of the JSON-safe dict under a **lock**; all updates to `current_state` use the same lock.

For **`OPTIMIZE`** with strategy **`NEWTON`** only, the communicator **temporarily wraps** your lab’s **`place_component_wo_home_specific_xy_cloudlab`** method on **`OpticalExperiment`**:

1. **Before** each call: update **`tunables.nominal_pose`** only (**`ghost`** phase)—UI can show where the strategy is about to place the part.
2. **After** the call **succeeds** (no exception): update **`measurables.pose`**, set **`tunables.presence`** to breadboard (not storage), and align **`tunables.nominal_pose`** with that pose (**`physical`** phase)—solid catches up.

For both phases, **X/Y** come from the place call’s **`target_x` / `target_y`**. **Rotation** on the canvas is **not** taken from the robot’s **`angle`** argument (that vector does not match the UI’s top-down `rotation` field and produced wrong values such as ~29° when the table pose was ~270°). Instead, **`rotation`** (and any existing **`roll` / `pitch` / `yaw`** on the component) are **carried forward** from the current lab-state **`measurables.pose`** so only the table translation updates step to step.

The original unwrapped method is restored in a **`finally`** block so manual **`MOVE_COMPONENT`** paths are not left patched.

**Important:** this hook only fires for places that go through **`place_component_wo_home_specific_xy_cloudlab`**. If your **`NewtonPlacementStrategy_cloudlab`** uses a different `_cloudlab` mover, you need to emit the same two phases yourself (see below).

### Optional: `progress_callback` on `NewtonPlacementStrategy_cloudlab` (lab_automation only)

If the constructor of **`NewtonPlacementStrategy_cloudlab`** accepts an optional **`progress_callback`**, cloud-labs will pass a function with signature:

`(phase, component, target_x, target_y, angle=None, step=None)`  

where **`phase`** is **`"ghost"`** or **`"physical"`**, matching the semantics above. **`angle`** may still be passed for your own logging; **cloud-labs ignores it for pose** and only uses **`target_x` / `target_y`** plus the stored UI rotation. Implement this **only** on the `_cloudlab` class so shared non-cloudlab strategies stay unchanged.

Copy/paste guidance and call-site examples live in:

**`backend/lab_communicator/newton_cloudlab_progress_example.py`**

### Optimization step counter and table-cam label

`optimization_step` in lab state is advanced from a background watcher. In **REAL** mode each optimization run uses a **dedicated subfolder** under **`Camera_Images/`** (name like `opt_<YYYYMMDD_HHMMSS>_<NEWTON|COBYLA>`). The communicator watches **that folder** for the current run so successive optimizations do not overwrite PNGs. Lab state also exposes **`optimization_run_dir`** (folder basename) while optimizing. Strategies in **`lab_automation`** must accept an **`output_dir`** (or alias—see **`update_lab.md`**) and write frames there; filenames should still include a **`stepNN`** pattern (e.g. `test_step02.png`) when possible so the step index is unambiguous.

### Mock-only: “beam intensity” plot

The canvas plot labeled **Optimization Metric (Beam Intensity)** is **synthetic** and is shown only when **`lab_mode === "MOCK"`**. Real mode relies on the optimization MJPEG feed and table camera, not that metric.

---

## Repository layout

```
cloud-labs/                   # repository root (historically also called optics-digital-twin in docs)
├── .env                      # Optional: LAB_MODE, LAB_AUTOMATION_PATH (loaded from repo root)
├── primitives.md             # Primitive vocabulary, tunables/measurables context, macro design notes
├── model.md                  # Observation vs saved state; get vs return measurables (design; see lab_model/)
├── backend/
│   ├── main.py               # FastAPI app; delegates /api/command + recipe steps to lab_primitives
│   ├── lab_model/            # Domain: tunables/measurables, storage Q3 geometry, motor rotation JSON
│   │   ├── README.md         # Conceptual overview (tunables vs measurables)
│   │   ├── component_model.py
│   │   ├── storage_region.py
│   │   └── motor_rotation_store.py
│   ├── lab_primitives/       # PrimitiveId, Pydantic schemas, registry, dispatch, read primitives, macros
│   │   ├── README.md         # Package overview (what runs on each HTTP path)
│   │   ├── ROADMAP.md        # Next steps (tests, more macros, Protocol, …)
│   │   ├── ids.py            # PrimitiveId, PrimitiveKind, READ_/MACRO_ primitive id sets
│   │   ├── schemas.py        # Validated POST /api/command bodies (discriminated by action)
│   │   ├── registry.py       # PRIMITIVE_REGISTRY (metadata + handler names)
│   │   └── dispatch.py       # parse_command_payload, execute_validated_command, schedule_validated_command, fetch_read_primitive
│   ├── lab_communicator/
│   │   ├── base.py           # LabCommunicator interface
│   │   ├── mock.py           # Simulated lab (delays, noise, local JSON state)
│   │   ├── real.py           # Adapter for external lab_automation package
│   │   └── newton_cloudlab_progress_example.py  # Paste guide for optional Newton UI callback in lab_automation
├── frontend/
│   ├── index.html            # layout + inline styles; script: js/main.js then command-console.js
│   ├── debug.html
│   ├── mock_feed.svg         # mock video placeholder when no live stream
│   └── js/
│       ├── main.js           # entry (imports bootstrap)
│       ├── bootstrap.js      # loads app-main
│       ├── app-main.js       # lab UI, canvas, init, __commandConsoleDeps
│       ├── config.js         # geometry + POLLING_INTERVAL
│       ├── state/store.js    # client-side mutable store
│       ├── canvas/coordinates.js
│       ├── command-console.js
│       ├── command-parse.js
│       ├── command-api.js
│       └── command-complete.js
├── schemas/                  # JSON contracts & reference data
│   ├── component_catalog.real.json  # Real lab inventory (physical parts on the table)
│   ├── component_catalog.mock.json  # Mock-only catalog (richer, for UI demos)
│   ├── mock_lab_state.json   # Seed / reference for mock
│   └── …                     # e.g. client_payload, strategies examples
├── recipes/                  # Saved recipes + optional *_golden.json
├── states/                   # User-saved lab state snapshots (API)
├── requirements.txt          # Python dependencies (install from repo root)
├── ROADMAP.md
├── laser_line_fit.npy        # Real-mode laser overlay: coefficients x = a*y + b (mm); see below
├── scripts/
│   └── generate_laser_line_fit.py  # Regenerate laser_line_fit.npy after retuning the physical laser
└── Camera_Images/            # Optimization frames may be read/watched here (real workflows)
```

**Laser line (`laser_line_fit.npy`):** In **`LAB_MODE=REAL`**, `GET /api/laser-line` loads **`[a, b]`** from this file so the UI draws the red dashed path and snap-to-line behavior. Coordinates are **lab mm** with **origin at table center**; the breadboard grid in the UI is **25 mm** between holes. The **grid dots** use **`BREADBOARD_GRID_OFFSET_X_MM`** in **`frontend/js/config.js`**: a **−¼ inch** base plus an extra fine-tune (e.g. **−7.4 mm** total when the arm-measured vertical beam is at **`b ≈ 392.6`**) so dots track the real hole columns—**component poses** are unchanged. Set **`b`** to the arm-measured **x** of the beam for a vertical line (**`a = 0`**). After editing **`laser_line_fit.npy`**, use **Refresh Pose** (or reload) to refetch coefficients.

The `backend-simple/` folder holds small lab-related Python snippets with **relative imports** meant for use inside a larger **`lab_automation`** tree; it is **not** the FastAPI entrypoint.

---

## Main HTTP API (short reference)

| Method | Path | Role |
|--------|------|------|
| GET | `/` | Main UI |
| GET | `/debug` | Debugger / visualizer |
| GET | `/api/catalog` | Component catalog |
| GET | `/api/lab-state` | Current lab JSON (includes **`lab_mode`**: `MOCK` \| `REAL`) |
| GET | `/api/components/{tag_id}/tunables` | Commanded **tunables** for one component |
| GET | `/api/components/{tag_id}/measurables` | Lab-reported **measurables** for one component |
| POST | `/api/lab-state/refresh-pose` | Re-localize **measurables.pose** from overhead / table camera (real: full scan; mock: simulated). Alias: `POST /api/lab-state/refresh` |
| POST | `/api/components` | Request add-to-lab (catalog item payload) |
| POST | `/api/command` | Move / motor / optimize / store / … — body validated by **`lab_primitives`** (`parse_command_payload` → `schedule_validated_command`) |
| GET | `/api/laser-line` | Laser line coefficients `{ a, b, source, … }` |
| GET | `/api/video-feed/status` | Stream availability + source URL |
| GET | `/api/video-feed/stream` | MJPEG (real) or static mock SVG |
| GET | `/api/optimization-feed/stream` | Optimization MJPEG when supported |
| GET | `/api/table-cam/capture?cam_id=1\|2` | Single PNG — real hardware in **`LAB_MODE=REAL`**; synthetic PNG in **`MOCK`** (UI / Cobyla-ref testing) |
| GET | `/api/cobyla-reference-image` | PNG bytes of the stored reference (**404** if none); used by the UI red “Cobyla reference” preview and **Save ref to file** |
| POST | `/api/cobyla-reference-image` | Body: PNG bytes → stored as **`CobylaAlignmentStrategy.reference_image`** (BGR) for the next COBYLA run |
| GET | `/api/cobyla-reference-image/status` | Whether a reference is set (+ size); includes **`lab_mode`** |
| DELETE | `/api/cobyla-reference-image` | Clear stored reference |
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
# Absolute or repo-relative path to the lab_automation *package directory* (the folder named lab_automation)
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
2. **Place and align** — Drag on the canvas or use the **floating component controls**; confirm moves; run **Optimize** with strategy parameters. For **Cobyla**, use **Latest capture** for new table-cam frames, then **Set Cobyla reference** to store that frame as the server reference (shown in the separate **red-bordered** preview so you can keep capturing). **Save ref to file** / **Load ref from file** reuses a PNG for testing without recapturing on hardware.
3. **Record a recipe** — Toggle record, perform actions, save; play back from the sidebar.
4. **Drift / golden** — After a good run, a golden file may exist; use **Debug** or `GET /api/recipes/{id}/compare` to compare poses to the current lab state.
5. **Snapshots** — Use **Save / Load state** to persist JSON under `states/`.

---

## Extending the inventory

Mock and real modes load **different** catalog files so UI demos in mock mode can showcase parts the real table may not have yet. `GET /api/catalog` automatically serves the catalog for the **currently running** mode (resolved via `lab.get_catalog()`).

1. Tag physical parts (e.g. ArUco) consistently with your vision stack.
2. Add or edit the appropriate catalog:
   - **Real lab:** **`schemas/component_catalog.real.json`** — the physical inventory on the table (`tag_id`, type, size, optional `motor_ids`, properties). Keep this in sync with what is literally tagged on the breadboard.
   - **Mock / UI demos:** **`schemas/component_catalog.mock.json`** — freely extend with parts you want to showcase in mock mode.
3. `GET /api/catalog` is re-read from disk on every call, so edits are picked up without restart; restart the backend only if you changed `LAB_MODE`.

See **`ROADMAP.md`** (repo-wide) and **`backend/lab_primitives/ROADMAP.md`** (primitive layer: tests, macros, tooling).
