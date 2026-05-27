# Universal Component Architecture

**Full refactor plan, design rationale, and reference for the next-generation
cloud-labs / `lab_automation` architecture.**

| | |
|---|---|
| **Status** | v1 draft design doc · **Phases 0–9 + Universal Component shape implemented** (see [`backend/lab_model/ARCHITECTURE.md`](backend/lab_model/ARCHITECTURE.md)) |
| **Audience** | Cloud-labs maintainers, `lab_automation` maintainers, UROP advisor review |
| **Companion docs** | [`capability_contract.md`](capability_contract.md) (focused technical spec), [`backend/lab_model/README.md`](backend/lab_model/README.md) (tunables/measurables today), [`backend/lab_model/primitives/README.md`](backend/lab_model/primitives/README.md), [`Run_CloudLab_Scripts.md`](Run_CloudLab_Scripts.md), [`import_json.md`](import_json.md) |
| **Scope** | Frontend (`optics-digital-twin/frontend/`), backend communicator (`optics-digital-twin/backend/`), and the separately-versioned `lab_automation` repository |

---

## Table of contents

- [Part I — Motivation](#part-i--motivation)
- [Part II — The new model](#part-ii--the-new-model)
- [Part III — Where we are today](#part-iii--where-we-are-today)
- [Part IV — The Capability Contract (technical spec)](#part-iv--the-capability-contract-technical-spec)
- [Part V — Design decisions and reasoning](#part-v--design-decisions-and-reasoning)
- [Part VI — Phased implementation plan](#part-vi--phased-implementation-plan)
- [Part VII — Open questions](#part-vii--open-questions)
- [Part VIII — Glossary & references](#part-viii--glossary--references)

---

# Part I — Motivation

## 1. What we are solving

Three classes of friction surfaced in a recent advisor discussion:

1. **Network latency in the control loop.** Every UI tweak that requires a
   tight feedback loop (turning a knob and watching a laser spot move,
   sliding a slider while looking at a camera image) currently round-trips
   through the JSON state machine that was designed for *commands*, not for
   *continuous control*. The JSON state is polled at ~2 Hz; trying to drive
   teleoperation through it produces choppy, frustrating UX.

2. **Live camera feeds polluting the state.** Cameras are first-class lab
   citizens — the optimizer reads their frames, the operator watches them
   to align beams. But raw frames are megabytes; the JSON state was never
   meant to carry binary blobs. Today’s code holds path strings to PNGs on
   disk, but the *live MJPEG / preview-poll* pipelines are entirely outside
   the state model (`/api/table-cam/stream`, `/api/video-feed/stream`,
   `/api/optimization-feed/stream` are hardcoded routes the frontend
   `<img>` tags point at directly).

3. **Ambiguous system state when humans are in the loop.** The current
   `system_status` enum (`IDLE` / `BUSY` / `OPTIMIZING` / `HOLDING`) covers
   automated motion well. It has no clean model for *"a human is turning a
   knob right now"*, which is conceptually different from `BUSY` (an
   automated routine that locks human input out). Lumping the two into
   `BUSY` either over-restricts the UI (no live tuning allowed) or
   under-protects the hardware (commands can collide with manual moves).

These three problems share a root cause: **the current architecture has one
data plane for everything.** Commands, low-frequency state, and
high-frequency live feeds all flow through the same JSON-polling
mechanism. Adding more sensors makes it worse, not better.

## 2. Why this matters beyond the immediate pain

The lab is moving toward a regime where:

- More components exist on the table (cameras, static sensors, motorized
  mounts, possibly future sensors we haven't designed yet).
- LLM-driven and recipe-driven workflows (see [`import_json.md`](import_json.md))
  treat the lab as an API; they need a *predictable, declarative* schema
  to reason about.
- The split between "the JSON twin" and "the physical lab" needs to be
  surfaceable to anyone reading the code — not buried in conventions and
  comments.

The refactor below is not just about smoothing the current UX; it is
about giving the codebase a *substrate* that scales to the next two
years of lab growth without ad-hoc bolt-ons.

## 3. Two analogies that anchor the new model

**Twitch streamer analogy** (used to explain the control-plane / data-plane
split):

- The JSON state is a streamer's **static text profile** — their bio,
  what game they're playing, when they're live next. Low-frequency,
  authoritative, polled when needed.
- The live MJPEG / WebSocket telemetry is the **video tunnel** —
  high-frequency, transient, viewed by anyone subscribed. The profile
  doesn't try to embed the video; it just *says* the streamer is live
  and where to tune in.

**Git commit vs Google Doc analogy** (used to explain tunables vs telemetry):

- Sending a command with new tunables is like **`git commit`**: you
  package up your intent, send it, and the system executes it
  atomically. Either it lands or it doesn't.
- Driving a knob in TELEOP is like **typing in Google Docs**: every
  keystroke is reflected upstream in real time; there is no "commit"
  until you press a button (`RECORD_MEASURABLES`) to snapshot what you
  did.

Both analogies appear in the implementation below; if you forget which
data flow you're working in, returning to these usually clarifies it.

---

# Part II — The new model

## 4. "Everything is a Component"

Every addressable entity in the lab is a **Component**, regardless of
whether it can be physically picked up by the robot. Mirrors, lenses,
beam blocks, optical filters, gripper cameras, ceiling cameras, future
sensors — all share the same JSON shape:

```text
components[tag_id] = {
  "id": "...",
  "type": "...",
  "statecontrol": {
    "tunables":    { ... },   ← intent (slow / formal)
    "measurables": { ... }    ← receipts (slow / formal)
  },
  "telemetry": {
    "teleop":     { active, ready, lease_ts, ... },   ← fast control session
    "live_feed":  { stream: { connected, live, ... } } ← streaming observe
  }
}
```

Catalog **`capabilities`** mirrors this: `statecontrol`, `telemetry`, and `primitives` (see [`capability_contract.md`](capability_contract.md)). Legacy flat `tunables`/`measurables` on components or in catalog are normalized at load time.

There are **no** hardcoded sections like "Cameras" or "Optics". The UI
introspects each component's `capabilities` block to decide what controls
to render. Adding a new device type means writing a `lab_automation`
class and declaring a `capabilities` entry — never editing the UI to add
"a new section for the new device".

This is the single change that, when fully applied, makes the right-side
panel symmetric: clicking any component (camera, mirror, sensor)
produces the same kind of UI structure — only the widgets differ.

## 5. The three data pillars

Each component's data is **strictly partitioned** into three pillars.

### 5.1. Tunables — "the intent / the command"

> *What the user or script wants the hardware to do.*

Examples:

- `nominal_pose: { x: 267, y: -85.8, rotation: 0 }` — where the user
  wants the mirror to sit.
- `nominal_motor_positions: { "1": 0, "3": 0 }` — what angle the user
  wants each motor at.
- `exposure_time_ms: 200` — how long the user wants the camera shutter
  open.
- `placement.mode: "MANUAL"` — strategy the user picked for placement.

Tunables are **committed atomically** via primitives (`MOVE_COMPONENT`,
`MOVE_MOTOR`, `OPTIMIZE`, ...). They are the *Git commits* of the lab.

### 5.2. Measurables — "the settled reality / the receipt"

> *The confirmed, high-fidelity physical truth of the hardware.*

Examples:

- `motor_rotations: { "1": 0.0, "3": 0.0 }` — encoder readback after a
  move completed.
- `last_optimization_score: 0.99` — scalar feedback from the last
  optimization run.
- `camera_image: { path, format, source, cam_id }` — pointer to a
  pristine PNG on disk written by `RECORD_MEASURABLES`.

**Crucial rules:**

- Measurables are *never* the canvas's source of truth. The canvas
  draws from `tunables.nominal_pose` (see §7 below). Measurables are
  *receipts* — confirmations that a tunable was honored, not the live
  state of the world.
- Cameras (`OPTICAL_CAMERA`) **do not produce spatial measurables of
  themselves**. They are *agents* that, when triggered, populate
  measurables of *other* components.
- Raw `.png` binaries **do not** belong in the JSON. The measurable
  carries a path; the file lives on disk.

### 5.3. Telemetry — "the process / live data"

> *Transient, high-speed, out-of-band data streams.*

Examples:

- 30 fps MJPEG from a table camera.
- Per-frame motor coordinate updates during TELEOP.
- Optimizer iteration thumbnails written to a watch folder.

**Key property:** telemetry bypasses the JSON state entirely. The JSON
holds the **endpoint** (a URL or WebSocket path); subscribers attach to
that endpoint and stream out-of-band. The polling loop never has to
carry a byte of MJPEG.

## 6. Five system states

The state machine grows from four states (`IDLE`, `BUSY`, `OPTIMIZING`,
`HOLDING`) to five by adding **`TELEOP`**.

| State | Meaning | Human input | Telemetry open? |
|-------|---------|-------------|-----------------|
| **IDLE** | Settled. Nothing moving. Measurables are recent and trusted. | ✅ Allowed | Optional |
| **BUSY** | Automated atomic primitive running (e.g. *Scan until beam*). | ❌ Locked out | Optional |
| **OPTIMIZING** | Multi-step optimization run (`NEWTON`, `COBYLA`). | ❌ Locked out | Yes (optimizer feeds frames) |
| **HOLDING** | A part is in the gripper mid-sequence. | Restricted (only same-tag commands) | Optional |
| **TELEOP** | Human is driving a *specific component* live. | ✅ Required | Yes (continuous jog stream) |

`TELEOP` is **per-component** (a flag on the specific component being
driven; the global `system_status` still tells you the dominant mode of
the lab). See §16.5 for the concurrency policy that goes with this.

## 7. The Golden Rule of Measurables (and `RECORD_MEASURABLES`)

> *In robotics, if a part moves, its old position data is a lie.*

Therefore: **the moment a component enters `BUSY` or `TELEOP`, its
`measurables` must be explicitly emptied (nulled) in the state.**

When the user finishes teleop, the system returns to `IDLE`, but the
measurables remain empty. To repopulate them, the user clicks a UI
button labeled **`RECORD_MEASURABLES`** which:

1. Sends an atomic command (`POST /api/components/{tag}/measurables/record`).
2. Causes the lab to take a pristine, high-res photo *and* record exact
   encoder coordinates (per the component's `capabilities.measurables`).
3. Populates the JSON.

This is the *Google Doc → snapshot* moment of the Twitch analogy. Before
this snapshot, the JSON has no measurables; subscribers who need the
truth either read telemetry live, or wait for the next snapshot.

**`RECORD_MEASURABLES` already exists in the codebase under a different
name (`OBSERVE_MEASURABLES`).** Phase 2 of the rollout (Part VI) is a
mechanical rename.

## 8. Canvas semantics — the most important reframing

This is the conceptual move that makes everything else cleaner.

**Before:** the canvas reads `measurables.pose` to draw every component.
That couples the layout drawing to whatever the lab last reported, which
means we either (a) keep measurables populated while the part is
moving — violating the Golden Rule — or (b) accept that the part vanishes
from the canvas during every move.

**After:** the canvas reads **`tunables.nominal_pose`** for everything.
Measurables become *off-canvas receipts*. The Golden Rule becomes safe to
enforce because the canvas no longer depends on measurables.

**Ghost state takes on a dual role:**

1. **`source: "DRAG"`** — the existing behavior. User clicks and drags a
   component; the ghost shows the intended new tunable; click "Confirm"
   and the ghost commits → the value lands in `tunables.nominal_pose`,
   the ghost clears.
2. **`source: "TELEOP"`** — new behavior. User enters TELEOP on a
   component; the ghost is continuously driven by the live telemetry
   stream (motor encoder frames, WebSocket coordinates). It looks like a
   ghost to the renderer, but the data flow is opposite: instead of
   sourcing from the UI input, it sources from the wire. When the user
   exits TELEOP, the ghost commits → the value lands in
   `tunables.nominal_pose`, the ghost clears.

Same renderer behavior; different visual treatment via the `source`
field. Same commit pathway.

This single decision — **canvas draws tunables; ghost = control buffer
for both drag and telemetry** — eliminates the entire class of "what
does the canvas do when measurables are null?" problems.

---

# Part III — Where we are today

**Update (2025):** Phases 0–9 and the Universal Component refactor are **implemented**. Components use **`statecontrol` + `telemetry`**; the UI splits **read-only** StateControl/Telemetry from **PRIMITIVES** writes. See [`backend/lab_model/ARCHITECTURE.md`](backend/lab_model/ARCHITECTURE.md) for the current map. The gap analysis below is kept for historical context — many items marked ❌ below are now ✅.

A surprising amount of the new architecture is already in place. This
part is the gap analysis: what exists, what doesn't, what's mis-shaped.

## 9. What already works

### 9.1. Tunables / Measurables split is live

Defined in [`backend/lab_model/domain/component.py`](backend/lab_model/domain/component.py) and used throughout
the state machine. Default shapes:

```python
def default_tunables() -> Dict[str, Any]:
    return {
        "presence": "breadboard",
        "nominal_pose": {"x": 0.0, "y": 0.0, "rotation": 0.0},
        "nominal_motor_positions": {},
        "storage": {"in_storage": False, "slot": None},
        "placement": {"mode": "MANUAL"},
    }

def default_measurables() -> Dict[str, Any]:
    return {
        "pose": {"x": 0.0, "y": 0.0, "rotation": 0.0},
        "last_optimization_score": None,
        "last_optimized_pose": None,
        "camera_image": None,
    }
```

The migration from the legacy `{state, pose, intent, metadata}` shape to
this split is already done — see `scripts/archive/migrate_to_tunables.py`.

### 9.2. Cameras are already components

`tag_21` and `tag_22` exist in lab state as `type: "OPTICAL_CAMERA"`,
sharing the same JSON shape as mirrors and lenses. The conceptual move
of "everything is a component" is already half-done; what's missing is
that cameras still carry `measurables.pose` like optics (they shouldn't
under the new rule), and the right-side panel still treats cameras
specially.

### 9.3. `OBSERVE_MEASURABLES` is the `RECORD_MEASURABLES` primitive

Defined in [`backend/lab_model/primitives/registry.py`](backend/lab_model/primitives/registry.py):

```python
PrimitiveId.OBSERVE_MEASURABLES: {
    "kind": PrimitiveKind.ATOMIC,
    "read_only": False,
    "handler": "observe_measurables_for_tag",
    "http": "POST /api/components/{tag_id}/measurables/observe",
},
```

The mock implementation (`backend/lab_communicator/mock/primitives.py:207`)
already triggers a synthetic table-cam capture for `OPTICAL_CAMERA` tags
and writes the result into `measurables.camera_image`. The real backend
has the same primitive wired up via `lab_communicator/real/video.py`.

The proposed `RECORD_MEASURABLES` UI button is *exactly* the existing
`POST /api/components/{tag}/measurables/observe`. Phase 2 renames it for
clarity; no new primitive needs to be invented.

### 9.4. System status enum is mature

`IDLE` / `BUSY` / `OPTIMIZING` / `HOLDING` are defined in
[`backend/lab_model/holding.py`](backend/lab_model/holding.py) with dedicated refusal helpers per
state in [`backend/lab_communicator/shared/state_machine.py`](backend/lab_communicator/shared/state_machine.py). Adding
`TELEOP` is straightforward — it's a fifth enum value plus its own
refusal helpers.

### 9.5. Telemetry channels physically exist

The control-plane / data-plane split is **already implemented at the
transport layer**:

- `GET /api/video-feed/stream` — ceiling camera MJPEG.
- `GET /api/table-cam/stream` — table cam MJPEG.
- `GET /api/table-cam/preview` — low-latency polled JPEG.
- `GET /api/optimization-feed/stream` — optimizer iteration thumbnails.

These bypass `GET /api/lab-state` completely. What's missing is that the
JSON doesn't **advertise** these endpoints per-component — the frontend
hardcodes them in `index.html`.

## 10. What is missing

| Pillar of the new model              | Status      | Gap |
|--------------------------------------|-------------|-----|
| Tunables / Measurables split          | ✅ Live      | Hygiene only |
| Cameras as components                 | ✅ Live      | Cameras still carry spatial measurables |
| `OBSERVE` / `RECORD_MEASURABLES`      | ✅ Live      | Not exposed as a per-component UI button |
| `IDLE` / `BUSY` / `OPTIMIZING` / `HOLDING` | ✅ Live | `TELEOP` missing |
| Null-on-motion rule                   | ❌ Not enforced | Measurables stay populated during BUSY |
| Telemetry endpoints in JSON           | ❌ Hardcoded | Need `telemetry_endpoints` block per component |
| Symmetric right-panel rendering       | ❌ Hardcoded | LIVE FEED + System Monitor + Table cam are fixed slots |
| Per-component `capabilities`          | ❌ Missing  | Catalog has no schema declarations |
| Widget registry                       | ❌ Missing  | UI is hardcoded per field |
| Canvas reads `tunables.nominal_pose`  | ❌ Reads measurables | All `measPose` call sites need updating |
| `lab_automation` catalog-driven       | ❌ Hardcoded | IPs/cameras/steppers are class constants |

## 11. The shape of the current pain

Two concrete examples ground the abstract problems above:

### 11.1. Adding a new sensor today

A new device (say, a power meter) arrives. To wire it into the UI:

1. Add a new section to `frontend/index.html` (hardcoded HTML).
2. Add a new JS module in `frontend/js/` to drive it.
3. Add a new API route in `backend/main.py`.
4. Hook it up in `lab_automation` ad-hoc.

Each of these is a manual edit. There's no schema gating any of it. The
new sensor effectively gets its own bespoke architecture every time.

### 11.2. Hardcoded IPs

We just walked through `wifi_stepper{1,2,3}` mapping to IPs that live in
`lab_automation/managers/experiment_manager.py:69` as a class constant.
Adding a stepper today means editing that file in the other repo. The
catalog says `"motor_controller": "wifi_stepper3"`; the IP isn't named
anywhere the cloud-labs operator can see it.

Both pains share the same root cause: **the schema and the configuration
are scattered across two repos and several languages**. Concentrating
them in a single JSON contract (Part IV) fixes both.

---

# Part IV — The Capability Contract (technical spec)

> The full spec lives in [`capability_contract.md`](capability_contract.md). This section
> is a self-contained restatement for readers who want the whole story in
> one document. If the two ever diverge, **`capability_contract.md`** is
> canonical.

## 12. Three-layer architecture

```text
┌────────────────────────────────────────────────────────────────────┐
│                  component_catalog.json                            │  ← THE LAW
│  (per lab-view bundle; the only schema declaration that exists)    │
└────────────────────┬───────────────────────────┬───────────────────┘
                     │                           │
              (read at boot)               (read at boot)
                     │                           │
                     ▼                           ▼
        ┌────────────────────────┐   ┌──────────────────────────┐
        │   cloud-labs           │   │   lab_automation         │
        │                        │   │                          │
        │ • Widget Registry      │   │ • Maps catalog entries   │
        │   (closed v1 set)      │   │   to OpticalComponent    │
        │ • Renders UI from      │   │   subclasses + SDK calls │
        │   capabilities         │   │ • Validates inputs at    │
        │ • Dispatches primitives│   │   the hardware boundary  │
        │   declared by catalog  │   │                          │
        └────────────────────────┘   └──────────────────────────┘
```

**Rules:**

- Both repos **read** the catalog; **neither writes it**.
- Cloud-labs does **not** import `lab_automation` for schema. (Mock mode
  works without `lab_automation` installed.)
- `lab_automation` does **not** import cloud-labs. It receives the
  catalog as a constructor argument: `OpticalExperiment(catalog=<dict>)`.
- The catalog is **human-editable JSON**, optionally regenerated by a
  helper script that introspects `lab_automation` classes — but at
  runtime, both repos read only the file.

## 13. Catalog JSON shape

The catalog is a single JSON object. Today, `component_library.json` is
a top-level **array**; under this spec it becomes an **object** with two
keys: `schema_version` and `components`. Existing per-component fields
are preserved; a new `capabilities` block is added.

### 13.1. Per-component skeleton

```json
{
  "schema_version": 1,
  "components": {
    "tag_18": {
      "id": "mirror_curved",
      "type": "OPTICAL_MIRROR",
      "name": "Curved Mirror (OC)",
      "tag_id": "tag_18",
      "size": { "width": 62, "height": 62 },
      "height_mm": 60,
      "properties": { "radius_of_curvature": "100mm", "reflectivity": 0.98 },
      "motor_ids": [1, 3],
      "motor_controller": "wifi_stepper1",
      "capabilities": {
        "tunables": {
          "nominal_pose":            { "widget": "TablePose" },
          "nominal_motor_positions": { "widget": "NudgeMotorGroup", "motor_ids": [1, 3], "step_deg": 2.5 }
        },
        "measurables": {
          "last_optimization_score": { "widget": "NumberBadge", "format": ".3f" },
          "motor_rotations":         { "widget": "MotorRotationsReadout" }
        },
        "telemetry": {},
        "primitives": [
          "MOVE_COMPONENT", "MOVE_MOTOR", "MOTOR_SEND_HOME", "MOTOR_SET_ZERO",
          "OPTIMIZE", "STORE_COMPONENT", "PLACE_FROM_STORAGE",
          "PICK_COMPONENT", "HOVER", "PLACE_FROM_HOVER", "SCAN_ROTATE_IN_PLACE",
          "RECORD_MEASURABLES"
        ]
      }
    }
  }
}
```

### 13.2. Camera entry (illustrates telemetry block)

```json
"tag_22": {
  "id": "cam_gripper_1",
  "type": "OPTICAL_CAMERA",
  "name": "Gripper Camera 1",
  "tag_id": "tag_22",
  "size": { "width": 62, "height": 62 },
  "height_mm": 60,
  "properties": { "resolution": "1920x1080", "fov": 60 },
  "capabilities": {
    "tunables": {
      "nominal_pose":    { "widget": "TablePose" },
      "exposure_time_ms": { "widget": "FloatRange", "min": 10, "max": 1000, "default": 200, "unit": "ms" }
    },
    "measurables": {
      "camera_image": { "widget": "ImageViewer", "format": "png" }
    },
    "telemetry": {
      "stream":  { "widget": "MJPEGViewer", "url": "/api/components/{tag_id}/telemetry/stream" },
      "preview": { "widget": "JPEGPoll",    "url": "/api/components/{tag_id}/telemetry/preview", "default_fps": 10 }
    },
    "primitives": ["MOVE_COMPONENT", "RECORD_MEASURABLES", "SET_TUNABLE"]
  }
}
```

### 13.3. Static (fixed) component entry

```json
"ceiling_cam_1": {
  "type": "OPTICAL_CAMERA",
  "name": "Ceiling Cam 1",
  "capabilities": {
    "tunables": {},
    "measurables": {
      "camera_image": { "widget": "ImageViewer", "format": "png" }
    },
    "telemetry": {
      "stream": { "widget": "MJPEGViewer", "url": "/api/components/ceiling_cam_1/telemetry/stream" }
    },
    "primitives": ["RECORD_MEASURABLES"]
  }
}
```

Note the absence of `MOVE_COMPONENT` in `primitives`. **That absence is
how the catalog expresses "this component is static"** — no `is_movable`
flag is needed. Dispatch refuses move primitives because they are not in
the declared list.

### 13.4. Dot notation for nested keys

Field names use dot notation: `"placement.mode"` is
`tunables.placement.mode`. Pro: flat, greppable. Con: collides if a
tunable name ever contains a literal dot. We accept the trade-off for v1.

## 14. Widget vocabulary (v1)

The **closed set** of widget types cloud-labs ships with. Adding a widget
is a single cloud-labs PR (one frontend component + one entry in the
registry). The list is intentionally small — discipline in this list is
the difference between Notion-like UX and SAP-like UX.

### 14.1. Tunable widgets

| Widget                | Data shape                                | Notes |
|-----------------------|-------------------------------------------|-------|
| `TablePose`           | `{ x, y, rotation }` (mm, mm, deg)        | Canvas-draggable + numeric inputs. Renders the component icon on the layout. |
| `NudgeMotorGroup`     | `{ "<motor_id>": float, ... }` (deg)      | +/- buttons per motor; reads `motor_ids`, `step_deg`. |
| `FloatRange`          | `float`                                   | Slider + numeric input. Reads `min`, `max`, `default`, `unit`. |
| `IntRange`            | `int`                                     | Same, step 1. |
| `Boolean`             | `bool`                                    | Toggle switch. |
| `StringDropdown`      | `string`                                  | Reads `options: string[]`. |
| `StorageSlot`         | `{ i, j }`                                | Q3 storage grid picker. |

### 14.2. Measurable widgets

| Widget                  | Data shape                                | Notes |
|-------------------------|-------------------------------------------|-------|
| `PoseReadout`           | `{ x, y, rotation }` (or extended)        | Read-only display of encoder confirmation. |
| `MotorRotationsReadout` | `{ "<motor_id>": float, ... }`            | Read-only dial group. |
| `ImageViewer`           | `{ path, format, source, cam_id, ... }`   | Loads PNG/JPEG via the camera-image route. |
| `NumberBadge`           | `float \| int \| null`                    | Reads `format` (printf-style). |
| `Timestamp`             | ISO-8601 string                           | Relative time ("3 s ago"). |

### 14.3. Telemetry widgets

| Widget                   | Endpoint kind          | Notes |
|--------------------------|------------------------|-------|
| `MJPEGViewer`            | HTTP multipart MJPEG   | Mounted as `<img src=…>`. |
| `JPEGPoll`               | HTTP single JPEG       | Polled at `default_fps`. Lower latency than MJPEG. |
| `LiveCoordinatesReadout` | WebSocket JSON frames  | Live x/y/z/rot readout during TELEOP. |

### 14.4. Universal fallback

| Widget          | Behavior |
|-----------------|----------|
| `JsonInspector` | Read-only formatted JSON. Used by the soft-fallback path (§15). Not normally declared in the catalog; cloud-labs picks it automatically when the catalog references an unknown widget. |

## 15. Validation policy

**Hybrid**: hard-fail on schema-version / required-field / unknown-primitive
mismatches; soft-fall back on unknown widgets.

### 15.1. Hard failures (cloud-labs refuses to boot)

- Catalog `schema_version` not in cloud-labs's `SUPPORTED_SCHEMA_VERSIONS`.
- A component entry missing a required field (`id`, `type`, `tag_id`,
  `capabilities.primitives`).
- A primitive id in `capabilities.primitives` not in
  `lab_model.primitives.PrimitiveId`.
- A widget descriptor's required config missing (e.g. `FloatRange`
  without `min` and `max`).

### 15.2. Soft fallbacks (cloud-labs boots, logs warning)

- A widget name unknown to the running build → renders the field with
  `JsonInspector`, logs one warning at boot.
- A measurable / telemetry channel listed in the catalog is missing from
  current state → renders an empty placeholder.

### 15.3. Validation locations

| Where                    | What is validated |
|--------------------------|-------------------|
| Boot (cloud-labs)        | Schema version + structural integrity (§15.1). |
| Boot (`lab_automation`)  | Each catalog entry maps to a known `OpticalComponent` subclass; capability fields recognized hardware-side. |
| Per request (cloud-labs) | Dispatch refuses a primitive not declared in target component's `capabilities.primitives`. |
| Per request (`lab_automation`) | Tunable updates outside catalog's declared `min`/`max`/`options` rejected at hardware boundary. |

Both repos must reject the same inputs for the same reasons. The catalog
is the only schema either repo references.

## 16. Cross-cutting decisions baked into the contract

### 16.1. Per-component TELEOP (decided)

`tunables.teleop_active: bool` on the specific component being driven.
Global `system_status` reflects the *dominant* mode (typically `IDLE`
during TELEOP, but `OPTIMIZING` can coexist on another component — see
16.5). Refusal helpers: `refuse_if_teleop_active(tag)` (same-component)
and `refuse_if_any_teleop_active()` (whole-lab gate, for primitives that
require absolute quiescence like robot home/calibration).

### 16.2. Ghost source-tagging (decided)

Frontend store gains `source` per ghost entry:
- `source: "DRAG"` — UI drag buffer. Existing behavior.
- `source: "TELEOP"` — live telemetry destination. New behavior.

Same data structure, same renderer, different visual treatment (e.g.
dashed outline for unsubmitted drag; solid faint outline for live
telemetry). Same commit path on completion.

### 16.3. `RECORD_MEASURABLES` rename (decided)

`OBSERVE_MEASURABLES` → `RECORD_MEASURABLES` is a clean rename across
the entire stack (primitive id, route, handler, docs, frontend). No
alias kept (cloud-labs is the only consumer).

### 16.4. Schema-authority reading (decided)

**Reading B**: catalog JSON is generated/edited in cloud-labs; both
repos read the same file at boot. `lab_automation` receives the catalog
via constructor argument; cloud-labs runtime never imports
`lab_automation` for schema.

(Two alternatives were considered: Reading A — runtime introspection;
shared third location. Reading B chosen for the lowest coupling between
repos.)

### 16.5. TELEOP/automation concurrency policy (decided)

**Default: per-component concurrency is allowed.** Optimization on
component A can run while the user teleops component B. A
`lab_manifest.json` safety knob (`teleop_safety.require_lab_idle:
true`) overrides this and forces whole-lab quiescence for any TELEOP
session.

### 16.6. Telemetry endpoint shape (decided)

URLs are **direct** in the catalog, with `{tag_id}` substitution at
boot:

```json
"stream": { "widget": "MJPEGViewer", "url": "/api/components/{tag_id}/telemetry/stream" }
```

No resolver layer. (Manifest-resolved handles considered and rejected
for v1 to keep things simple; revisit if remote tunneling ever lands.)

## 17. `lab_automation` integration contract

### 17.1. Constructor signature

```python
class OpticalExperiment:
    def __init__(
        self,
        mock: bool = False,
        catalog: Optional[Dict[str, Any]] = None,  # new
        ...
    ):
        ...
```

- `catalog=None` → preserves current behavior (hardcoded steppers /
  cameras / IPs) so existing `lab_automation` users outside cloud-labs
  are not broken.
- `catalog=<dict>` → opt-in capability-driven mode: drivers are
  configured from the catalog, input validation uses the catalog.

### 17.2. What `lab_automation` reads

Only the **hardware-relevant** fields:

- `type` — drives which `OpticalComponent` subclass to instantiate.
- `tag_id`, `motor_ids`, `motor_controller` — per-component hardware
  wiring.
- `capabilities.tunables[*]` — uses `min`, `max`, `options`, `default`
  for input validation. **Ignores `widget`.**
- `capabilities.primitives` — gates which methods on the resulting
  Python object dispatch may call.

### 17.3. What `lab_automation` ignores

- `capabilities.tunables[*].widget` (cloud-labs concern).
- `capabilities.measurables[*].widget` (cloud-labs concern).
- `capabilities.telemetry[*].url` (cloud-labs serves the routes).
- `properties.*` (display-only physical specs).

---

# Part V — Design decisions and reasoning

This section is the "show your work" portion: every non-obvious decision
above, with the alternatives considered and why we picked what we
picked.

## 18. Why "tunables-only canvas" (and what got cleaner because of it)

**Original instinct:** Canvas draws `measurables.pose` because that's
"where the part actually is".

**Problem:** If we null measurables during BUSY (per the Golden Rule),
the canvas shows nothing during every move. UX disaster.

**Considered:**

- **A.** Don't null measurables; just mark them "stale". Loses the
  Golden Rule's safety property.
- **B.** Cache the last-known measured pose in the frontend store; keep
  drawing during BUSY. Workable but introduces a second source of truth
  on the client that has to be invalidated correctly.
- **C.** Canvas reads `tunables.nominal_pose` instead. Ghost handles
  both drag (old behavior) and telemetry (new behavior).

**Picked C.** Reasoning:

1. The canvas was never the right place for "measured truth" anyway —
   it's a layout/planning tool. The user wants to see *what they
   intended*, not the last sensor readout.
2. Measurables become genuinely off-canvas (receipts in the side panel,
   not poses on the layout). That matches the user's mental model.
3. The Golden Rule becomes safe — nulling measurables doesn't visually
   break anything.
4. Ghost state's dual role (drag vs telemetry) folds naturally because
   *both* represent "in-flight intent that hasn't committed to tunables
   yet".

**Cost:** Phase 1 has to refactor every `measPose` call site in
`frontend/js/canvas/render.js`, `frontend/js/component-model.js`, and
downstream. Real but contained.

## 19. Why per-component TELEOP (and not system-wide)

**Considered:**

- **A.** System-wide TELEOP — `system_status: "TELEOP"` flips the whole
  lab. Jogging one mirror locks out everything else. Simplest state
  machine.
- **B.** Per-component TELEOP — `tunables.teleop_active: true` on a
  specific component; the global status stays IDLE for the rest of the
  lab. More granular; allows OPTIMIZE on A + TELEOP on B.

**Picked B.** Reasoning:

1. The lab is *physically* able to do this (different axes, different
   controllers — no shared resource conflict between, say, a stepper
   motor on a mirror and a camera exposure tweak).
2. Forcing a global lock under-utilizes the hardware.
3. The granularity matters more later when there are more sensors and
   actuators on the table.

**Cost:** Concurrency story is non-trivial. We need:
- Per-component refusal helpers and a global "is anyone teleopping?"
  helper.
- A `lab_manifest.json` knob to opt back into whole-lab-quiet (§16.5)
  for benches that prefer the strict policy.
- Careful Phase 8 design to make sure two motion authorities don't
  collide on a shared resource we haven't enumerated.

## 20. Why **Reading B** for schema authority (cached, not live)

**Considered:**

- **A.** Cloud-labs imports `lab_automation` at runtime and calls
  `OpticalComponent.describe_capabilities()` per request. Single source
  of truth at every read; mock mode also needs `lab_automation` installed.
- **B.** Catalog JSON is the source of truth; both repos read it at
  boot. A regenerate script populates it from `lab_automation`
  introspection. Mock mode works without `lab_automation`.
- **C.** Shared schema file (e.g. `lab_schema.json`) lives in
  `lab_automation`; both repos read it. Same as B but file ownership
  inverted.

**Picked B.** Reasoning:

1. Mock mode independence is precious. Cloud-labs should boot, render
   the UI, and serve a synthetic lab without `lab_automation` Python
   imports being available. Reading A breaks this.
2. The catalog is **per lab-view bundle** in cloud-labs (different
   benches → different catalogs). That ownership model already exists;
   inverting it for Reading C just shifts complexity.
3. The "schema authority" we care about is *human* — there's exactly
   one JSON file we edit, and both repos read it. Whether the bytes
   physically live in repo A or repo B doesn't change the contract.

**Cost:** The regenerate script (Phase 5) needs to exist and be run
when `lab_automation` adds a new capability. Documented as a manual
step in `lab_automation`'s release process.

## 21. Why a **closed widget registry** is the most important UI decision

The pattern of schema-driven UIs is well-known and well-loved (JSON
Schema Form, Salesforce, Notion). It's also well-known for producing
ugly, generic-feeling UIs when applied naively — every form looks like
a list of text boxes.

The fix is the **closed widget registry**: the catalog declares a
*widget type* per field, not just a data type. The frontend ships rich
implementations of each widget; the catalog points at them by name.

**Considered:**

- **A.** Open string types in the catalog (`"type": "exposure_time"`),
  frontend has a switch statement matching specific names. Brittle; new
  types require frontend code AND catalog edits.
- **B.** Inferred widgets from data type (`"type": "number"` →
  always a numeric input). Generic UX; loses the "rich widget per
  semantic" goal.
- **C.** Closed widget registry: a finite vocabulary of widget names
  (e.g. `Pose3D`, `FloatRange`, `NudgeMotorGroup`) that the frontend
  ships implementations for; catalog references them by name. Adding a
  widget = one frontend PR.

**Picked C.** Reasoning:

1. Discipline. The registry forces conversation about "do we need a
   new widget here, or does an existing one fit?" That discipline
   prevents widget explosion (we don't want 200 widgets after a year).
2. Backward compat is easy: catalogs declaring unknown widgets fall
   back to `JsonInspector` (15.2), so labs can ship catalogs ahead of
   cloud-labs UI releases without breaking.
3. Rich UX is the headline payoff of the entire refactor. This is the
   mechanism by which we get it.

## 22. Why **dot notation** for nested capability keys

**Considered:**

- **A.** Fully nested objects: `tunables.placement.mode` becomes
  `"placement": {"mode": {"widget": "StringDropdown", ...}}`.
- **B.** Dot notation: `"placement.mode": {"widget": "StringDropdown",
  ...}`.

**Picked B.** Reasoning:

1. Grep-ability. `rg "placement.mode"` finds all references; with
   nested objects you'd need a structural search.
2. Flat readability. The catalog should be scannable; nesting hides
   important fields behind expandable arrows when the file is opened.
3. Trivially serializable; no path-encoding ambiguity in tooling.

**Cost:** Tunable field names cannot contain literal dots. Acceptable
constraint (none currently do).

## 23. Why **direct URLs** for telemetry endpoints (vs. handles)

**Considered:**

- **A.** Direct URLs: `"url": "/api/components/tag_22/telemetry/stream"`.
  Pragmatic, works today.
- **B.** Abstract handles: `"channel": "table_cam", "cam_id": 1` →
  frontend resolver maps to URL.
- **C.** Manifest-resolved handles: single name (`"channel":
  "table_cam_1"`) → resolved via `lab_manifest.json`.

**Picked A.** Reasoning:

1. Cloud-labs serves the routes anyway; the URL space is fully under
   its control. No remote-tunnel use case yet.
2. Resolver layer is dead weight until there's a reason for it.
3. If we ever do need remote tunneling, switching to handles is a
   one-pass migration (the catalog already declares the routes; we'd
   just add a resolver step on the frontend).

## 24. Why **hybrid validation** (hard on primitives, soft on widgets)

**Considered:**

- **A.** All-hard: any unknown name (primitive or widget) → refuse to boot.
- **B.** All-soft: any unknown name → log warning, keep going.
- **C.** Hybrid: unknown *primitives* are hard (cloud-labs owns the
  primitive vocabulary, so an unknown id is genuinely a bug); unknown
  *widgets* are soft (the registry is allowed to lag the catalog).

**Picked C.** Reasoning:

1. Primitives are a closed vocabulary owned by cloud-labs
   (`lab_model.primitives.PrimitiveId`). An unknown one is always a real
   problem — either a typo or a version skew that will cause silent
   "this command doesn't work" symptoms later.
2. Widgets are a closed vocabulary owned by cloud-labs's UI layer, but
   the catalog can reasonably want to declare a widget that this
   particular build doesn't ship yet (e.g., user is testing a catalog
   against an old cloud-labs). Falling back to `JsonInspector` is the
   safe thing — the field still works, just less prettily.
3. The two cases have genuinely different blast radii; treating them
   identically would either be too strict (option A) or too lax
   (option B).

---

# Part VI — Phased implementation plan

Ten phases (0–9). Each is a meaningful, shippable unit of work.

## Phase 0 — Lock the Capability Contract (no code)

**Deliverable:** This document + [`capability_contract.md`](capability_contract.md). 
Reviewed and approved by maintainers and advisor. **No code changes.**

**Exit criteria:** Both documents committed; design questions in
Part VII are either resolved or explicitly deferred.

## Phase 1 — Canvas reads `tunables.nominal_pose`

**Why first:** Foundational. Every subsequent phase that touches
measurables assumes the canvas no longer depends on them.

**Files touched (frontend only):**

- `frontend/js/canvas/render.js` — rebase all draw routines onto
  tunables.
- `frontend/js/component-model.js` — `measPose` callers updated;
  `measPose` itself becomes a legacy helper (or deleted).
- `frontend/js/state/lab-state.js` — ghost rebuild logic no longer
  reads measurables.
- `frontend/js/canvas/interaction.js` — collision detection on
  tunables.

**Risk:** Medium-high. Highest-traffic UI code in the repo.

**Validation:** Run both mock and a saved real-lab snapshot; verify
canvas position is correct before and after.

## Phase 2 — `OBSERVE_MEASURABLES` → `RECORD_MEASURABLES`

**Why early:** Pure mechanical sweep; can ship in parallel with Phase 1.

**Files touched:**

- `backend/lab_model/primitives/{ids,registry,schemas,dispatch,protocol,README}.py`.
- `backend/lab_communicator/{base,real/primitives,mock/primitives}.py`
  (handler renames).
- `backend/main.py` (route rename).
- Docs: `backend/lab_model/README.md`, root `README.md`,
  `backend/lab_model/primitives/README.md`.
- Frontend: `frontend/js/command-parse.js`,
  `frontend/js/ui/context-panel.js`.

**Risk:** Low. Mechanical.

**Note:** No alias kept (clean break; we're the only consumer).

## Phase 3 — Null measurables on motion

**Why:** Enforce the Golden Rule. Now safe because Phase 1 made the
canvas independent of measurables.

**Files touched:**

- `backend/lab_communicator/shared/state_machine.py` — commit hooks
  that null measurables on BUSY/TELEOP entry.
- `backend/lab_communicator/shared/snapshot.py` — similar for
  snapshots.
- Audit consumers: `backend/lab_communicator/shared/pose_refresh_merge.py`,
  `backend/lab_model/state/commits.py`, recipe golden-comparison
  in `backend/main.py`. Each needs a "what if `measurables` is null"
  branch.

**Risk:** Medium. Many consumers; each needs to be confirmed.

## Phase 4 — `RECORD_MEASURABLES` UI button

**Why:** Makes the renamed primitive visible to the user before the
larger Phase 7 UI overhaul. Small, high-value UX win.

**Files touched:**

- `frontend/js/ui/context-panel.js` — add a button on selected
  component that calls `POST /api/components/{tag}/measurables/record`.
- Surface the resulting `camera_image` (and any new measurables) in
  the side panel as the "receipt" view.

**Risk:** Low.

## Phase 5 — Catalog format migration

**Why:** Without this, every later phase has to fight the existing
catalog shape.

**Files touched:**

- `scripts/archive/migrate_component_library_to_capabilities.py` — new script
  that:
  - Reads existing array-form `component_library.json`.
  - Wraps in `{ schema_version: 1, components: { ... } }`.
  - Infers a `capabilities` block per component using type-based
    presets (mirror/lens/camera/etc.).
  - Writes back, preserving formatting.
- `backend/lab_communicator/shared/lab_view_config.py` — loader for
  the new shape; validation per §15.
- All per-lab-view-bundle catalogs migrated.
- `lab_automation/managers/experiment_manager.py` — add `catalog=None`
  kwarg to `OpticalExperiment.__init__`; opt-in capability-driven
  initialization.
- `lab_automation/CLOUDLAB_CONTRACT.md` — hardware-side mirror of this
  contract.

**Risk:** Medium-high. Cross-repo coordination required.

**Exit criteria:** Both repos read the same catalog file; mock mode
still boots without `lab_automation`.

## Phase 6 — Telemetry endpoints in JSON

**Why:** Bridge from hardcoded right-panel video to per-component
telemetry. Prerequisite for Phase 7.

**Files touched:**

- Catalog entries gain populated `telemetry` blocks (cameras,
  motorized components).
- `backend/main.py` — new per-component routes:
  `/api/components/{tag}/telemetry/stream`,
  `.../telemetry/preview`, `.../telemetry/jog` (Phase 8 uses the last
  one).
- Old `/api/video-feed/*`, `/api/table-cam/*`,
  `/api/optimization-feed/*` stay as **deprecated aliases** for one
  release cycle.

**Risk:** Low. Mostly route refactor.

## Phase 7 — Symmetric right panel + widget registry

**Why:** The headline UX payoff. After this, clicking any component
produces structurally identical UI; differences are widget-driven.

**Files touched:**

- Delete hardcoded LIVE FEED + Table Cam blocks from
  `frontend/index.html`.
- New `frontend/js/widgets/` directory:
  - `index.js` — widget registry.
  - One file per widget (TablePose, FloatRange, NudgeMotorGroup,
    MJPEGViewer, ImageViewer, NumberBadge, ...).
- New `frontend/js/ui/component-viewer.js` — generic side-panel
  renderer that introspects `capabilities` and mounts the right
  widgets per pillar.
- Existing `frontend/js/ui/context-panel.js` slimmed down or absorbed
  into the new component-viewer.
- Delete `frontend/js/table-cam/`, `frontend/js/video-feed.js`,
  `frontend/js/cobyla-reference.js` (their behavior is now
  catalog-declared and widget-rendered).

**Risk:** High. Largest single visible change.

## Phase 8 — Per-component TELEOP

**Why:** The most concurrency-sensitive piece. Deferred until canvas
+ measurables + telemetry are stable, so we can build TELEOP on a
known-good base.

**Files touched:**

- `backend/lab_model/holding.py` — add
  `SYSTEM_STATUS_TELEOP` constant.
- `backend/lab_model/domain/component.py` — add `teleop_active` field
  to tunables defaults; conventions for who reads/writes it.
- `backend/lab_communicator/shared/state_machine.py` — refusal
  helpers: `refuse_if_teleop_active(tag)`,
  `refuse_if_any_teleop_active()`.
- `backend/lab_model/primitives/ids.py` — new primitives: `START_TELEOP`,
  `END_TELEOP`, jog primitive (REST or WebSocket — see Q1 in Part VII).
- `backend/main.py` — corresponding routes.
- `backend/lab_communicator/shared/lab_view_config.py` — read
  `teleop_safety.require_lab_idle` from manifest.
- `frontend/js/widgets/teleop-jog.js` — new widget (slider/knob that
  emits jog frames).
- `frontend/js/state/store.js` — ghost state gains `source` field.
- `frontend/js/canvas/render.js` — visual treatment per ghost source.

**Risk:** High. New state machine semantics + new transport.

## Phase 9 — Sunset deprecated endpoints

**Reality check (post-Phase-8b audit):** the original Phase 9 entry
claimed "the old endpoints have no callers inside cloud-labs" — that
was wrong. The four legacy route families (`/api/video-feed/*`,
`/api/table-cam/*`, `/api/cobyla-reference-image/*`,
`/api/optimization-feed/*`) still drove four live UI panels in the
frontend (top-row video, table-cam panel, Cobyla reference manager,
OPTIMIZING feed swap), and `frontend/js/cobyla-reference.js` was never
deleted in Phase 7. The phase is therefore split into four sub-phases
in dependency order. Risks vary by sub-phase; the original "Low" was
only correct for 9a.

### Phase 9a — Cobyla reference image (✅ done)

**Why:** Smallest blast radius and the only sub-phase whose product
question was already resolved (Q3: the reference becomes "the latest
recorded `measurables.camera_image` on the relevant camera
component").

**Files touched:**

- `backend/main.py` — removed the 4 `/api/cobyla-reference-image*`
  routes (GET / POST / GET status / DELETE). Replaced with a comment
  block explaining the migration target and what 9d still owes.
- `frontend/js/cobyla-reference.js` — deleted (~230 lines).
- `frontend/js/app-main.js` — dropped the `initCobylaReference` import
  and call site.
- `frontend/js/state/store.js` — dropped the
  `cobylaRefPreviewObjectUrl` field.
- `frontend/index.html` — removed the entire Cobyla reference panel
  (preview slot, 4 buttons, file input, status line).
- `README.md` — dropped the 4 route rows.

**Completed in Phase 9d:** in-process Cobyla reference helpers removed;
COBYLA reads `measurables.camera_image` via
`load_cobyla_reference_bgr_from_state`.

**Risk:** Low. No optimizer code changed; UI surface clearly marked
DEPRECATED before removal; the workflow ("`RECORD_MEASURABLES` on the
camera, then OPTIMIZE") is already shipped (Phase 4).

### Phase 9b — Optimization feed migration (✅ done)

**Why:** `frontend/js/state/lab-state.js` swapped the table-cam preview
to the lab-wide `/api/optimization-feed/stream` whenever
`system_status == OPTIMIZING`. Phase 9b moves this to a per-component
route scoped to the tag currently under `OPTIMIZE`.

**Decision:** Keep the "OPTIMIZING swaps the preview" UX. The feed
shows optimizer iteration thumbnails (not the live camera MJPEG), so
re-pointing at `/api/components/{tag}/telemetry/stream` would have
been a behavior regression. Instead we added a dedicated
`telemetry/optimization-stream` channel on the optimizing component.

**Files touched:**

- `backend/lab_communicator/base.py` — stamp
  `optimization_target_id` on `OPTIMIZE` entry; clear on finalize.
- `backend/lab_communicator/shared/snapshot.py` — default
  `optimization_target_id` to `None` in session reconciliation.
- `backend/main.py` — added
  `GET /api/components/{tag_id}/telemetry/optimization-stream`;
  removed legacy `GET /api/optimization-feed/stream`.
- `frontend/js/state/lab-state.js` — bind the table-cam `<img>` to
  the per-component URL using `optimization_target_id` (with
  `pendingActions` fallback).
- `README.md` — route table updated.

**Risk:** Low–medium. One UX path; no state-machine changes beyond
the new top-level field.

### Phase 9c — Top-row live video (✅ done)

**Why:** `frontend/js/video-feed.js` + the top-row `<img>` element in
`index.html` polled `/api/video-feed/{status,stream}`. These predated
the per-component telemetry model and were already marked DEPRECATED
in the UI.

**Decision:** Drop the top-row slot entirely. No overhead-camera
catalog entry exists yet (ceiling hardware on REAL uses a separate
code path from gripper table cams), and Phase 7 already exposes
MJPEG via each camera component's `telemetry.stream` widget in the
symmetric panel. Re-pointing the slot at a gripper cam would have
changed semantics; adding a synthetic overhead tag was deferred.

**Files touched:**

- `backend/main.py` — removed `GET /api/video-feed/{status,stream}`.
- `frontend/js/video-feed.js` — deleted.
- `frontend/js/app-main.js` — dropped init wiring.
- `frontend/js/ui/pose-refresh.js` — removed post-refresh video poll.
- `frontend/index.html` — removed the 200px LIVE FEED pane.
- `README.md` — route table updated.

**Carry-over:** `LabCommunicator.get_video_stream` on REAL remains for
when an overhead camera is added to the catalog (likely as a dedicated
component with `telemetry.stream` in Phase 9d or a follow-up).

**Risk:** Medium (visible UI change). Operators use the component panel
for live camera streams instead.

### Phase 9d — Table-cam panel + Cobyla optimizer migration ✅ Done

**Removed:**

- Nine lab-wide ``/api/table-cam/*`` HTTP routes from ``backend/main.py``.
- ``frontend/js/table-cam/`` (panel.js, api.js, preview-engine.js) and
  the deprecated table-cam dock in ``frontend/index.html``.
- In-process ``lab.set_cobyla_reference_*`` / ``get_cobyla_reference_*``
  helpers on mock/real communicators and ``base.py``.

**Kept:**

- ``LabCommunicator.table_cam_*`` methods — still used by per-component
  ``/api/components/{tag_id}/telemetry/*`` routes.
- Slim **Optimization preview** sidebar slot (``#optimization-feed-img``)
  bound to ``telemetry/optimization-stream`` during OPTIMIZE.

**Optimizer migration:**

- COBYLA reads reference BGR via
  ``load_cobyla_reference_bgr_from_state`` from the catalog camera tag's
  ``measurables.camera_image`` (after ``RECORD_MEASURABLES``).

**Operator workflow:** use each camera tag in the component panel for
live view, capture, and reference; run OPTIMIZE from the command console
or context panel. Default exposure for COBYLA/NEWTON comes from
``getTableCamExposureSeconds()`` (selected tag's ``exposure_time_ms`` or
``store.defaultCameraExposureSec``).

## Phase ordering rationale

```
Phase 0:  ───── Spec lock ─────
                  │
Phase 1:  Canvas reads tunables ──┐
Phase 2:  Rename → RECORD ────────┤  (parallel-safe)
                  │               │
Phase 3:  Null measurables on motion (depends on Phase 1)
                  │
Phase 4:  RECORD UI button (small win; can ship after Phase 2)
                  │
Phase 5:  Catalog migration (cross-repo; gates Phases 6+7)
                  │
Phase 6:  Telemetry endpoints in JSON ─┐
Phase 7:  Symmetric UI + widget registry  (parallel-safe after 5)
                  │
Phase 8:  TELEOP (depends on 6 + 7)
                  │
Phase 9:  Sunset deprecated endpoints
```

---

# Part VII — Open questions

Unresolved in v1; flagged for follow-up at the affected phase.

**Q1. TELEOP jog transport.** REST `POST /jog` per frame (~30 fps
polling) or per-component WebSocket (`ws://.../tag_22/jog`)? WebSocket
is more natural for continuous data; REST is simpler and matches the
rest of the API. Lean WebSocket; decide before Phase 8.

**Q2. TELEOP timeout / disconnect handling.** If the user closes the
browser mid-teleop, the component stays `teleop_active: true` forever.
Server-side TTL with heartbeat? Auto-clear after N seconds of no jog
frames? Decide before Phase 8.

**Q3. Cobyla reference image.** ✅ Resolved in Phase 9a: the reference
is "the latest recorded `measurables.camera_image` on the relevant
camera component". No dedicated tunable or side-channel blob; the
operator workflow is `RECORD_MEASURABLES` on the camera, then OPTIMIZE.
The HTTP setter routes were removed in 9a; the optimizer-side wiring
(reading from `measurables.camera_image` instead of an injected
ndarray) was completed in Phase 9d.

**Q4. Recipe / golden state migration.** Recipes today snapshot
measurables; under the new model golden files should snapshot
tunables (since measurables are transient receipts). Need a migration
script for `lab_view/recipes/*.json`. Decide before Phase 3 or 7.

**Q5. `OPTIMIZE` strategy declaration in the catalog.** Should the
catalog declare which strategies a component supports
(`capabilities.optimize.strategies: ["NEWTON", "COBYLA"]`)? Probably
yes; shape not finalized. Decide before Phase 5.

**Q6. Composite widgets.** Some UIs are most natural as one widget
reading multiple fields (e.g. a storage widget that shows
`{slot, presence}` together). Allow composite widgets in v1, or one
widget per field with layout grouping handled separately? Lean: one
widget per field for v1; revisit after Phase 7.

**Q7. Per-bench widget overrides.** Two benches with the same
component type might want different widget config (different
`step_deg` on nudge buttons, different `max` on exposure). Per-bundle
catalog already supports this; just confirm via worked example in
Phase 5.

**Q8. TELEOP-specific capability block.** Phase 8 may need a fourth
capability bucket (`teleop`) declaring jog channels and their widgets,
separate from the general `telemetry` block. Defer to Phase 8.

**Q9. Schema-version negotiation.** If we ever support multiple
schema versions in one cloud-labs build, what does the negotiation
look like? For v1 the answer is "only one supported; hard fail
otherwise". Revisit if breaking changes ever ship mid-version.

---

# Part VIII — Glossary & references

## 25. Glossary

| Term | Meaning |
|------|---------|
| **Tunables** | The user's / script's intent for a component. Examples: `nominal_pose`, `exposure_time_ms`. |
| **Measurables** | Hardware-confirmed receipts. Examples: encoder readbacks, `camera_image` paths. |
| **Telemetry** | Live, out-of-band data streams that bypass the JSON state entirely. Examples: MJPEG video, jog WebSockets. |
| **Component** | Any addressable entity in the lab: mirrors, lenses, cameras, sensors. All share one JSON shape. |
| **Capability Contract** | The schema declaration block (`capabilities`) per component, describing what tunables/measurables/telemetry it has and which primitives it supports. |
| **Widget** | A frontend UI component that renders a specific data shape. Catalogs reference widgets by name from a closed registry. |
| **Widget Registry** | The closed set of widgets cloud-labs ships. Adding a widget = one cloud-labs PR. |
| **Catalog** | The single JSON file declaring all components on a bench. Lives in cloud-labs (per lab-view bundle); read by both cloud-labs and `lab_automation` at boot. |
| **TELEOP** | New system state: a human is driving a specific component live. Per-component (a flag on the component being driven), not whole-lab. |
| **`RECORD_MEASURABLES`** | Atomic primitive that triggers a pristine measurement (camera capture, encoder snapshot) on a specific component. Renamed from `OBSERVE_MEASURABLES`. |
| **Golden Rule** | "If a part moves, the old measurables are a lie." Implemented as: nulling a component's measurables on BUSY/TELEOP entry. |
| **Ghost (DRAG)** | Frontend state buffer for "user is dragging a component but hasn't committed yet". Existing behavior. |
| **Ghost (TELEOP)** | Frontend state buffer driven by incoming telemetry during TELEOP. New behavior; same data shape as DRAG ghost, distinguished by a `source` field. |
| **Lab view bundle** | Per-bench directory containing `lab_manifest.json`, catalog, layout, recipes, saved states. See [`backend/lab_communicator/README.md`](backend/lab_communicator/README.md). |

## 26. References

- [`capability_contract.md`](capability_contract.md) — focused technical
  spec, canonical for the JSON shape.
- [`backend/lab_model/README.md`](backend/lab_model/README.md) — current
  tunables/measurables model.
- [`backend/lab_model/primitives/README.md`](backend/lab_model/primitives/README.md) — primitive vocabulary and dispatch.
- [`backend/lab_communicator/README.md`](backend/lab_communicator/README.md) — lab view bundle layout, manifest.
- [`Run_CloudLab_Scripts.md`](Run_CloudLab_Scripts.md) — Python
  orchestration layer.
- [`import_json.md`](import_json.md) — declarative JSON sequences
  (LLM-generated lab plans).
- `lab_automation/CLOUDLAB_CONTRACT.md` (in the `lab_automation` repo) —
  hardware-side mirror of the Capability Contract. Must be kept in sync
  with this document.

## 27. Change log

| Date       | Author | Change |
|------------|--------|--------|
| 2026-05-21 | initial | First draft, Phase 0 artifact. Architecture locked; implementation phases enumerated; open questions captured. |
