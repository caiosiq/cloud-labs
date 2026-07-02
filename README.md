# Optical Digital Twin — Robotic Lab Interface

## Project overview

This repository is a **digital twin** for an autonomous optics lab: a browser-based workspace where you lay out experiments (mirrors, lenses, cameras, filters, etc.), send **move** and **optimize** commands to a backend, and optionally drive a physical **xArm6** setup through a `lab_automation` integration.

The UI separates **what you intend** (ghost / nominal poses on the canvas) from **what the lab reports** (solid geometry from polled state), including recipe replay and **golden** snapshots for drift checks.

**Design reference:** **`backend/lab_model/ARCHITECTURE.md`** (Universal Component map), **`backend/lab_model/README.md`** (StateControl + Telemetry), **`docs/primitive_ui_contract.md`** (read-only panels vs primitives), **`docs/CONTROL_RUNTIME_AND_VERSIONING.md`** (RuntimeManager, ControlManager, configuration VC — planned). Lab backends and **`LAB_VIEW_PATH`**: **`backend/lab_communicator/README.md`**.

---

## Why two worlds? `lab_automation`, experiment manager, and this repository

> **Naming:** **`ControlManager`** (cloud-labs, planned) = configuration version history.  
> **`OpticalExperiment`** / *experiment manager* (`lab_automation`) = robot, cameras, and hardware procedures.  
> See [`docs/CONTROL_RUNTIME_AND_VERSIONING.md`](docs/CONTROL_RUNTIME_AND_VERSIONING.md).

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

In **real** mode, **`RealLabCommunicator`** maps **lab-frame** intent (what the UI sends) onto **robot-frame** and experiment-manager calls inside **`lab_automation`**—details live in **`lab_communicator/real/`** (the `communicator.py` checklist + the `primitives.py` API-call list; full architectural rationale in **`lab_communicator/README.md`**).

---

## What you see in the app

- **Dark, lab-style UI** (Inter typography, Material Icons): main table in the center, **left** sidebar for placed components and selection, **right** sidebar for live/overhead-style video, table-camera capture, recipes, and activity log.
- **2D breadboard canvas** (HTML5 Canvas): metric coordinates (~±500 mm by default), grid, robot-base **danger zone**, and **laser overlays** from **`laser_lines.json`** inside **`LAB_VIEW_PATH`** (`GET /api/laser-line`, `GET /api/laser-lines`). Geometry is aligned with the backend via **`GET /api/lab-layout`** before **`app-main.js`** loads.
- **Solid vs ghost**: placed components draw twice—opaque **physical** pose and semi-transparent **intent** pose with a dashed “drift” segment when they differ.
- **Interaction**: select a part; **move**, **optimize**, **motors**, **TeleOp**, **live feed**, and **record** live in a **floating component panel** (top-right of the table). The panel has **read-only STATE CONTROL + TELEMETRY** sections and a **PRIMITIVES** section for all writes (see **`docs/primitive_ui_contract.md`**). The **left sidebar** lists components plus **Refresh Pose** and **Configuration** (save commits, branch graph). Drag on canvas with optional **snap toward the laser line**; wheel to rotate while dragging. **Motor jog** appears for catalog entries that declare motor primitives.
- **Recipes**: record MOVE/OPTIMIZE steps; files live under **`{LAB_VIEW_PATH}/recipes/`** (see **`LAB_VIEW_PATH`** in `.env`). Successful runs can emit a **`{recipe_id}_golden.json`** reference beside the recipe JSON.
- **Configuration version control**: **Save configuration** commits tunable intent to **`{LAB_VIEW_PATH}/control/`** (see **`docs/CONTROL_RUNTIME_AND_VERSIONING.md`**).
- **Command Console** (bottom of the main page): typed shorthand for moves, optimize, and related actions; backed by ES modules (`command-parse.js`, `command-api.js`, `command-complete.js`, …) and wired through **`window.__commandConsoleDeps`** (see **Frontend code layout** below).
- **Debug page** at `/debug` for deeper inspection (ghost derivation, golden listing, etc.).

---

## Frontend code layout

There is **no bundler or SPA framework**: the browser loads **native ES modules** from `/static` (the `frontend/` tree). Styling remains mostly **inline in `index.html`**; behavior is split between a **main app bundle** and the **Command Console** stack.

### Load order

`index.html` registers two module scripts **in this order**:

1. **`/static/js/main.js`** — thin entry that imports **`bootstrap.js`** only.
2. **`/static/js/command-console.js`** — shell, history, tab completion; reads **`__commandConsoleDeps`** (assigned later by **`app-main.js`**) for `sendCommand`, `log`, `render`, lab state, ghost state, and collision checks.

**`bootstrap.js`** (imported by **`main.js`**) fetches **`GET /api/lab-layout`**, applies it to **`config.js`**, then dynamically imports **`app-main.js`** so the canvas matches **`layout.json`** before any drawing runs.

The root route rewrites **`js/main.js?v=…`** in the served HTML with a startup timestamp so refreshes pick up changes while **`NoCacheMiddleware`** still applies **`no-store`** on `/static` in development.

### Module map

| File | Role |
|------|------|
| **`js/main.js`** | Entry: **`import './bootstrap.js'`** only. |
| **`js/bootstrap.js`** | **`fetch('/api/lab-layout')`** → **`applyLabLayoutFromApiDoc`** → dynamic **`import('./app-main.js')`**; shows an error banner if layout load fails (check **`LAB_VIEW_PATH`**). |
| **`js/app-main.js`** | Main UI: DOM hooks, **`fetch`** polling, canvas draw/interaction, sidebars (components, context, recipes, library), modals, optimization preview, **`init()`**, and assignment of **`window.__commandConsoleDeps`**. |
| **`js/config.js`** | Canvas size; mutable lab bounds, danger radius, and breadboard grid fields set from **`/api/lab-layout`** (reasonable defaults until applied); **`POLLING_INTERVAL`**. |
| **`js/state/store.js`** | Single mutable **`store`** object: `labState`, `ghostState`, `catalogMap`, selection, optimization flags, recipe recording buffers, etc. |
| **`js/canvas/coordinates.js`** | **`mmToPx`** / **`pxToMm`** for the lab frame (origin at table center, +Y up on screen). |
| **`js/command-console.js`** | Console UI loop; depends on **`command-parse.js`**, **`command-api.js`**, **`command-complete.js`**. |

Larger slices of logic (dedicated API client, separate render module) can be peeled out of **`app-main.js`** over time.

---

## Architecture

### State tiers (conceptual)

1. **Tier 1 — Lab state (physical truth)**  
   Authoritative snapshot from the communicator: `system_status` (`IDLE`, `BUSY`, `OPTIMIZING`, `TELEOP`, …), timestamps, and per-component **`statecontrol`** (tunables + measurables) plus **`telemetry`** (TeleOp lease, live-feed session). Shapes and helpers: **`backend/lab_model/README.md`**, **`backend/lab_model/ARCHITECTURE.md`**.

2. **Tier 2 — Ghost / intent (what the UI plans)**  
   Client-side **ghost** poses track targets; the backend stores commanded values under **`statecontrol.tunables`** (`nominal_pose`, `placement.mode`, `storage`, `presence`). Debug: `GET /api/debug/ghost-state`. Per-tag slices: **`GET /api/components/{tag_id}/tunables`**, **`/measurables`**, **`/telemetry`**.

3. **Tier 3 — Recipe (procedure)**  
   JSON sequences of primitive steps stored under **`{LAB_VIEW_PATH}/recipes/{id}.json`**, played asynchronously by the server.

4. **Tier 4 — Golden state (reference)**  
   After a successful recipe run, a snapshot may be saved as **`{LAB_VIEW_PATH}/recipes/{id}_golden.json`** for drift comparison via `GET /api/recipes/{id}/compare`.

### Runtime stack

| Layer | Technology |
|--------|------------|
| **Frontend** | Static HTML/CSS; **ES modules** (`js/main.js` → **`bootstrap.js`** → **`app-main.js`**); canvas; `fetch` polling; Command Console modules |
| **Backend** | FastAPI (`backend/main.py`), serves `/` and `/debug` with no-cache headers and version-busted `js/main.js` |
| **Static assets** | Mounted at `/static` → `frontend/` |
| **Hardware** | **LabCommunicator** abstraction: `MockLabCommunicator` \| `RealLabCommunicator` |

### Backend packages (how `main.py` is organized)

| Package | Role |
|---------|------|
| **`lab_model`** | **Lab platform core** (semantics, no hardware): domain, **`statecontrol` + `telemetry`**, orchestration, **`lab_model/primitives/`**, tunables/measurables/telemetry plugins, catalog, state. See **`backend/lab_model/ARCHITECTURE.md`**. |
| **`lab_communicator`** | **Hardware/file bridge**: **`LabCommunicator`** (`base.py`) + **`mock/`** / **`real/`** backends. Paths from **`LAB_VIEW_PATH`** (`lab_communicator/shared/lab_view_config.py`). **Add a backend:** `python scripts/create_lab_communicator.py <name> --with-lab-view` — see **`backend/lab_communicator/README.md`**. |

**`main.py`** delegates command validation and scheduling to **`lab_model.primitives`** (same path for the recipe executor). Tunables/measurables per tag use **`fetch_read_primitive`** so reads stay aligned with the primitive vocabulary.

### Lab deployment bundle (`LAB_VIEW_PATH`)

Startup **requires** **`LAB_VIEW_PATH`** in `.env` (the only lab-selection env var): a directory whose **`lab_manifest.json`** names the communicator (`mock` / `real`) and optional **`lab_automation_path`**, plus JSON for geometry (`layout.json`), lasers, catalog, motors, table-cam preview tuning, recipes, and saved states. **`bootstrap_lab_view()`** runs in `main.py` **before** the communicator is constructed—swap benches by changing **`LAB_VIEW_PATH`** only.

Mandatory files and scaffolding instructions are spelled out in **`backend/lab_communicator/README.md`** (section 0). Quick path to a new backend package **and** a starter bundle:

```bash
python scripts/create_lab_communicator.py my_backend --with-lab-view
```

Register the new id in **`lab_communicator/shared/communicator_factory.py`**, set **`communicator`** in the bundle’s **`lab_manifest.json`**, and point **`LAB_VIEW_PATH`** at that `lab_view/` tree.

### Command–query style

- **Query**: browser polls **`GET /api/lab-state`** (~every 500 ms) to refresh solids, status, and **`lab_mode`**. Ghost sync: after commands finish (or while **`OPTIMIZING`** in real mode—see **Newton optimization in real mode** below); **`tunables.nominal_pose`** drives the ghost overlay when present. Per-tag slices: **`GET /api/components/{tag_id}/tunables`** and **`.../measurables`** (saved state; **`GET_TUNABLES`** / **`GET_MEASURABLES`** read primitives).
- **Record**: **`POST /api/components/{tag_id}/measurables/record`** (or **`POST /api/command`** with **`RECORD_MEASURABLES`**) takes a fresh measurement for that tag (e.g. camera capture → **`camera_image`**). Same **`409`** guard as commands when the system is **`BUSY`** / **`OPTIMIZING`**. Conceptual notes: **`backend/lab_model/README.md`**.
- **Command**: **`POST /api/command`** with JSON `{ "action", "target_id", "parameters" }`. Bodies are **validated** by **`lab_model.primitives`** (Pydantic); actions include `MOVE_COMPONENT`, `MOVE_MOTOR`, `OPTIMIZE`, `STORE_COMPONENT`, …. Successful accepts return **HTTP 200** with `"status": "accepted"`; **`409`** if the lab reports `BUSY` / `OPTIMIZING`; **`400`/`422`** on invalid payloads.
- **Placement request**: **`POST /api/components`** queues `add_component_to_state` (mock vs real behavior lives in the communicator).

### Real lab mode

When the bundle’s **`lab_manifest.json`** sets **`communicator": "real"`** and `lab_automation` imports succeed, **`RealLabCommunicator`** wraps **`OpticalExperiment`**, initializes the robot, scans/populates state, and can expose MJPEG streams and table-camera PNG capture. If imports or initialization fail, the server **falls back to mock** with a log message.

**`lab_automation_path`:** in **`lab_manifest.json`**, set a project-relative path to the **`lab_automation` package directory** (the folder that contains that package’s `__init__.py`). The backend adds its **parent** to `sys.path` so `import lab_automation` works.

Each **`GET /api/lab-state`** response includes **`lab_mode`**: `"MOCK"` or `"REAL"` (for UI behavior such as mock-only overlays).

### Session checkpoint and UI reconciliation

After a **graceful backend shutdown** (or **`POST /api/session-reconciliation/save`**), the server can write **`session_last_lab_state.json`** next to the other lab-view JSON files. On the next UI load, when the lab is **`IDLE`** and measured poses still match the checkpoint within tolerance, the client offers to **restore tunables and measurables** from that snapshot (hardware is not commanded).

| Control | Location |
|--------|-----------|
| Enable/disable | **`lab_manifest.json`** → **`session_checkpoint`** (default **`true`**) |
| Pose tolerances | **`session_reconciliation`** → **`position_mm`**, **`yaw_deg`** (real bundle defaults **8 mm / 10°**; mock **2 mm / 5°**) |
| Stale warning | **`session_reconciliation.stale_warning_hours`** (default **168**) |
| Env overrides | Optional **`SESSION_CHECKPOINT`**, **`SESSION_REC_THRESH_MM`**, **`SESSION_REC_THRESH_DEG`**, **`SESSION_CHECKPOINT_WARN_HOURS`** |

**API:** **`GET /api/session-reconciliation/offers`** (called once per page load when status becomes **`IDLE`**), **`POST /api/session-reconciliation/apply`** with `{ "tag_ids": [...] }`, **`POST /api/session-reconciliation/save`** to flush a checkpoint without restarting.

Previously **mock** defaulted this feature **on** and **real** **off**; both communicators now read the same flag from **`lab_manifest.json`**. Ensure **`"session_checkpoint": true`** in your real bundle (the default template includes it).

### Table cameras (CAM1 / CAM2) and table-top camera

Per-component **`OPTICAL_CAMERA`** tags declare capabilities in **`component_library.json`**. The UI uses **read-only TELEMETRY** for session state and **PRIMITIVES** for actions:

| Need | Where |
|------|--------|
| Live MJPEG | **START LIVE FEED** primitive → TELEMETRY shows stream when `live_feed.stream.live` → `GET /api/components/{tag_id}/telemetry/stream` |
| Still PNG + optimizer reference | **RECORD MEASURABLES** → `statecontrol.measurables.camera_image` → `GET /api/components/{tag_id}/camera-image` |
| Exposure intent | **SET EXPOSURE** primitive (grouped under MOVE COMPONENT in the panel) |
| TeleOp (gripper cameras) | **START TELEOP** / **TELEOP JOG** primitives when declared |
| Optimizer live view | Right sidebar **Optimization preview** during `OPTIMIZE` → `GET /api/components/{tag_id}/telemetry/optimization-stream` |

Gripper cameras (`cam_gripper_1` / `cam_gripper_2`) map to table cams via the catalog id convention. Table-top camera (`tag_99`, `stream_source: overhead`) uses the overhead mock/real stream backend.

**`table_cam_preview.json`** in **`LAB_VIEW_PATH`** still tunes recorder JPEG scale/quality for real benches; the UI no longer exposes a separate **`preview`** telemetry channel (stream only).

Lab-wide **`/api/table-cam/*`** routes were removed in Phase 9d.

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

Copy/paste guidance and the call-site logic live in **`backend/lab_communicator/real/optimization.py`** and **`backend/lab_communicator/real/primitives.py`** (`primitive_optimize_component` is where the strategy is built and `_cloudlab_progress_callback` is wired in).

### Optimization step counter and optimization preview

`optimization_step` in lab state is advanced from a background watcher. In **REAL** mode each optimization run uses a **dedicated subfolder** under **`Camera_Images/`** (name like `opt_<YYYYMMDD_HHMMSS>_<NEWTON|COBYLA>`). The communicator watches **that folder** for the current run so successive optimizations do not overwrite PNGs. Lab state also exposes **`optimization_run_dir`** (folder basename) while optimizing. Strategies in **`lab_automation`** must accept an **`output_dir`** (or alias—see **`update_lab.md`**) and write frames there; filenames should still include a **`stepNN`** pattern (e.g. `test_step02.png`) when possible so the step index is unambiguous.

### Mock-only: “beam intensity” plot

The canvas plot labeled **Optimization Metric (Beam Intensity)** is **synthetic** and is shown only when **`lab_mode === "MOCK"`**. Real mode relies on the optimization MJPEG feed and table camera, not that metric.

---

## Repository layout

```
cloud-labs/                   # repository root (historically also called optics-digital-twin in docs)
├── .env                      # LAB_VIEW_PATH (required) — communicator + lab_automation live in that bundle
├── backend/
│   ├── main.py               # FastAPI app; bootstrap_lab_view → communicator; delegates /api/command to lab_model.primitives
│   ├── lab_model/            # Platform semantics (no hardware I/O)
│   │   ├── domain/           # component shapes, holding, storage grid, motor JSON
│   │   ├── primitives/       # PrimitiveId, schemas, registry, dispatch, macros
│   │   ├── tunables/         # @register_tunable plugins
│   │   ├── measurables/      # @register_measurable plugins
│   │   ├── catalog/          # component_library validation + merge
│   │   └── state/            # commits, refusals, snapshot, placement UI
│   ├── lab_communicator/     # LabCommunicator bridge + mock/ + real/
│   │   ├── README.md
│   │   ├── base.py
│   │   ├── shared/           # lab_view_config, factory, session_checkpoint, storage_intent, util
│   │   ├── mock/
│   │   │   ├── communicator.py
│   │   │   ├── primitives.py
│   │   │   ├── persistence.py
│   │   │   └── lab_view/    # Default MOCK bundle when LAB_VIEW_PATH points here
│   │   └── real/
│   │       ├── communicator.py
│   │       ├── primitives.py
│   │       ├── coordinate_frames.py
│   │       ├── video.py / gripper.py / scan.py / optimization.py
│   │       └── lab_view/default/   # REAL starter bundle (+ optional states/, …)
├── frontend/
│   ├── index.html
│   ├── debug.html
│   ├── mock_feed.svg
│   └── js/
│       ├── main.js
│       ├── bootstrap.js
│       ├── app-main.js
│       ├── config.js
│       ├── state/store.js
│       ├── canvas/coordinates.js
│       ├── command-console.js
│       ├── command-parse.js
│       ├── command-api.js
│       └── command-complete.js
├── schemas/                  # Reference / example JSON only; authoritative lab data lives under LAB_VIEW_PATH
│   └── …                     # e.g. client_payload.json, strategies.json, recipe_example.json
├── requirements.txt
├── ROADMAP.md
├── scripts/
│   ├── create_lab_communicator.py  # Scaffold a new lab_communicator backend (+ optional lab_view)
│   └── generate_laser_line_fit.py  # Legacy npy helper (not used by FastAPI laser routes)
└── Camera_Images/            # Optimization frames (real workflows)
```

**Laser overlays:** `GET /api/laser-line` (single-line **`a`,`b`** snapshot) and `GET /api/laser-lines` read **`laser_lines.json`** inside **`LAB_VIEW_PATH`**. Update with **`PATCH /api/laser-lines/{line_id}`** or by editing that file; reload the UI to refetch. Coordinates are **lab mm** with **origin at table center**. Breadboard spacing and offsets are driven by **`layout.json`** / **`GET /api/lab-layout`** and applied client-side via **`frontend/js/config.js`**.

The `backend-simple/` folder holds small lab-related Python snippets with **relative imports** meant for use inside a larger **`lab_automation`** tree; it is **not** the FastAPI entrypoint.

---

## Main HTTP API (short reference)

| Method | Path | Role |
|--------|------|------|
| GET | `/` | Main UI |
| GET | `/debug` | Debugger / visualizer |
| GET | `/api/catalog` | Component catalog (**`component_library.json` ∩ `active_catalog.json`** order for the running mode) |
| GET | `/api/lab-layout` | **`layout.json`** plus derived fields — used by the UI before first paint |
| GET | `/api/component-library` | Full component library rows (optional / tooling) |
| GET | `/api/lab-state` | Current lab JSON (includes **`lab_mode`**: `MOCK` \| `REAL`) |
| GET | `/api/components/{tag_id}/tunables` | Commanded **tunables** for one component |
| GET | `/api/components/{tag_id}/measurables` | Lab-reported **measurables** for one component |
| POST | `/api/lab-state/refresh-pose` | Re-localize **measurables.pose** from overhead / table camera (real: full scan; mock: simulated). Alias: `POST /api/lab-state/refresh` |
| POST | `/api/components` | Request add-to-lab (catalog item payload) |
| POST | `/api/command` | Move / motor / optimize / store / … — body validated by **`lab_model.primitives`** (`parse_command_payload` → `schedule_validated_command`) |
| GET | `/api/laser-line` | Legacy single-line coefficients `{ a, b, source, … }` |
| GET | `/api/laser-lines` | All overlays from **`laser_lines.json`** |
| PATCH | `/api/laser-lines/{line_id}` | Adjust a line in **`laser_lines.json`** |
| GET | `/api/components/{tag_id}/telemetry` | Saved teleop + live_feed session state |
| GET | `/api/components/{tag_id}/telemetry/stream` | Per-component MJPEG (requires live feed on) |
| POST | `/api/components/{tag_id}/teleop/start` | Acquire TeleOp lease (`START_TELEOP`) |
| POST | `/api/components/{tag_id}/teleop/end` | Release TeleOp lease |
| POST | `/api/components/{tag_id}/telemetry/jog` | TeleOp jog frame (`TELEOP_JOG`) |
| POST | `/api/components/{tag_id}/telemetry/live-feed/start` | Start live feed |
| POST | `/api/components/{tag_id}/telemetry/live-feed/end` | End live feed |
| POST | `/api/components/{tag_id}/measurables/record` | Record measurables (`RECORD_MEASURABLES`) |
| GET | `/api/components/{tag_id}/camera-image` | PNG from `measurables.camera_image.path` |
| GET | `/api/components/{tag_id}/telemetry/optimization-stream` | Optimizer iteration MJPEG during `OPTIMIZE` |
| GET | `/api/session-reconciliation/offers` | Tags eligible to restore from last checkpoint |
| POST | `/api/session-reconciliation/apply` | Apply checkpoint tunables/measurables for given tag ids |
| POST | `/api/session-reconciliation/save` | Write checkpoint now |
| GET/POST | `/api/recipes`, `/api/recipes/{id}/play`, `/api/recipes/{id}/golden`, `/api/recipes/{id}/compare` | Recipe CRUD, play, golden, drift report |
| GET/POST | `/api/control/{repo_id}/…` | Configuration commits, branch graph, soft/hard checkout |
| GET | `/api/debug/ghost-state`, `/api/debug/golden-states` | Debug aggregates |

---

## Setup and run

### 1. Install dependencies

From the **repository root**:

```bash
pip install -r requirements.txt
```

Mock mode only needs the **FastAPI** stack (`fastapi`, `uvicorn`, `pydantic`, `python-dotenv`; `numpy` only if other code paths load array files). Full `requirements.txt` also lists vision/robot packages used when the lab view manifest points at a real `lab_automation` checkout.

### 2. Configuration

Create or edit **`.env`** in the **project root** (same folder as `requirements.txt`). Example:

```env
# Only lab-selection knob — everything else is in that bundle’s lab_manifest.json
LAB_VIEW_PATH=backend/lab_communicator/mock/lab_view
```

Example **`lab_manifest.json`** for a real bench (inside the bundle pointed to by `LAB_VIEW_PATH`):

```json
{
  "version": 1,
  "communicator": "real",
  "lab_automation_path": "../lab_automation",
  "session_checkpoint": true,
  "session_reconciliation": {
    "position_mm": 8,
    "yaw_deg": 10,
    "stale_warning_hours": 168
  }
}
```

`backend/main.py` resolves `LAB_VIEW_PATH` relative to the project root, loads **`lab_manifest.json`**, and starts the named communicator. If **`LAB_VIEW_PATH`** is missing or invalid, startup **exits** with a clear error.

**Console noise (polling):** the UI hits `/api/lab-state` about twice per second. Those messages are no longer printed at info level. If you start the app with **`python main.py`**, Uvicorn **access** logging (every `GET … HTTP/1.1` line) is **off** by default; set **`UVICORN_ACCESS_LOG=1`** in `.env` to turn it back on. To see per-poll debug lines from our handler, set **`LOG_LEVEL=DEBUG`**. If you use **`uvicorn main:app`** directly, add **`--no-access-log`** unless you want the access log.

### 3. Start the server

```bash
cd backend
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

- **Main UI:** http://localhost:8000/  
- **Debug:** http://localhost:8000/debug  

### MuJoCo component profiles

The default simulator profile preserves the colored demo boxes:

```powershell
$env:CLOUDLAB_SIM_PROFILE = "demo_boxes"
& "..\.venv\Scripts\python.exe" "backend\main.py"
```

To use the shared optical housing CAD for every movable optical component:

```powershell
$env:CLOUDLAB_SIM_PROFILE = "optical_housings"
& "..\.venv\Scripts\python.exe" "backend\main.py"
```

Profiles live under `simulation_profiles/`. The optical housing profile keeps
the existing UI catalog, icons, tags, and poses; it changes only MuJoCo
geometry, mass, collision dimensions, and grasp height.

The xArm7 MJCF model is vendored from MuJoCo Menagerie under
`third_party/mujoco_menagerie/ufactory_xarm7/`, so MuJoCo mode works from a
plain `cloud-labs` checkout. To test against a different local copy, set
`MUJOCO_XARM7_XML` to the desired `xarm7.xml` path before starting the backend.

### 4. Real lab mode

Set `LAB_MODE=REAL` and a valid `LAB_AUTOMATION_PATH` so `from lab_automation...` imports work. Expect robot/camera initialization, optional recorder subprocesses, and live streams when hardware is available. If initialization fails, the process **logs the error and stays in mock**.

---

## Typical workflow

1. **Add parts** — Open the catalog, **Request** items; in mock this updates state quickly; in real lab this ties to your automation policy.
2. **Place and align** — Drag on the canvas or use the **floating component controls**; confirm moves; run **Optimize** with strategy parameters. For **Cobyla**, run **`RECORD_MEASURABLES`** on the gripper camera tag first so the optimizer reads the latest **`measurables.camera_image`** as its reference.
3. **Record a recipe** — Toggle record, perform actions, save; play back from the sidebar.
4. **Drift / golden** — After a good run, a golden file may exist; use **Debug** or `GET /api/recipes/{id}/compare` to compare poses to the current lab state.
5. **Configuration** — Use **Save configuration** in the sidebar to commit tunable layout; travel the branch graph to soft-view or hard-apply older commits.

---

## Extending the inventory

The server builds the active catalog from **`component_library.json`** filtered and ordered by **`active_catalog.json`** (`tag_ids`), using **`catalog_bundle`** (see **`backend/lab_communicator/shared/catalog_bundle.py`**). `GET /api/catalog` returns the merged rows for the **currently running** communicator (`lab.get_catalog()`).

1. Tag physical parts (e.g. ArUco) consistently with your vision stack.
2. Under your deployment’s **`LAB_VIEW_PATH`**:
   - **`component_library.json`** — all part definitions keyed by **`tag_id`**.
   - **`active_catalog.json`** — `tag_ids` list: only those parts appear in the UI, in that order.
3. `GET /api/catalog` is re-read from disk on every call, so edits are picked up without restart.

For a new real deployment, copy **`backend/lab_communicator/real/lab_view/default/`** to a writable folder, point **`LAB_VIEW_PATH`** at it, and grow **`component_library.json` / `active_catalog.json`** there. (Some older **`schemas/component_catalog*.json`** copies may still exist in the repo as documentation only — they are **not** what the FastAPI app loads.)

See also **ROADMAP.md** (repo-wide) and **`backend/lab_model/ARCHITECTURE.md`** (Universal Component — implemented).
