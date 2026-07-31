# Lab platform architecture (`lab_model`)

This document is the master map for how the optics digital twin backend is organized.
It describes the **Universal Component** model as implemented today: **StateControl** +
**Telemetry** on each component, one catalog per bench, and Edge Contract southbound.

## Four domains (two slow, two live)

| Domain | Sub-pillar | Meaning | Changed by | Stored in state |
|--------|------------|---------|------------|-----------------|
| **StateControl** | **Tunables** | Commanded intent (nominal pose, exposure, storage, …) | Named **write primitives** | `components[tag].statecontrol.tunables` |
| **StateControl** | **Measurables** | Recorded observations (pose, camera_image, scores, …) | `RECORD_MEASURABLES` + commits | `components[tag].statecontrol.measurables` |
| **Telemetry** | **TeleOp** | Fast per-component control lease | `START_TELEOP` / `END_TELEOP` / `TELEOP_JOG` | `components[tag].telemetry.teleop` |
| **Telemetry** | **Live feed** | Streaming camera sessions | `START_LIVE_FEED` / `END_LIVE_FEED` | `components[tag].telemetry.live_feed` |

**Golden rules**

- UI **read-only** panels for StateControl and Telemetry (see `component-viewer.js`).
- Every mutation goes through a **primitive** with explicit parameters (no `{}` stubs).
- Catalog JSON is the **per-bench allow-list**; Python registries define **global meaning**.
- `RECORD_MEASURABLES` tears down live feed first (camera exclusivity).

Legacy lab state may still expose flat `tunables` / `measurables` at the component root;
`ensure_component_shape()` in `domain/component.py` normalizes to `statecontrol` + `telemetry`.

## Component state shape (runtime)

```json
{
  "id": "cam_gripper_1",
  "type": "OPTICAL_CAMERA",
  "statecontrol": {
    "tunables": { "nominal_pose": { }, "exposure_time_ms": 200, "storage": { }, "placement": { } },
    "measurables": { "pose": { }, "camera_image": null }
  },
  "telemetry": {
    "teleop": {
      "active": false,
      "ready": false,
      "lease_ts": null,
      "last_jog_ts": null,
      "last_error": null
    },
    "live_feed": {
      "stream": { "connected": false, "live": false, "backend": null, "resource_id": null, "last_error": null }
    }
  }
}
```

- **TeleOp `ready`:** lab finished setup (`_primitive_prepare_teleop`); jog controls enabled only when `active && ready`.
- **Lease TTL:** stale sessions cleared by sweeper using `lease_ts` (default 5 min; `teleop_safety.teleop_ttl_ms` in `lab_manifest.json`; `0` disables).

## Catalog capabilities shape

```json
"capabilities": {
  "statecontrol": {
    "tunables": { "exposure_time_ms": { "widget": "FloatRange", "min": 10, "max": 1000, "unit": "ms" } },
    "measurables": { "camera_image": { "widget": "ImageViewer", "format": "png" } }
  },
  "telemetry": {
    "teleop": {
      "rz": { "widget": "TeleopRz", "step_deg": [0.5, 2.0, 10.0] },
      "pose3d": {
        "widget": "TeleopPose3d",
        "step_mm": [0.5, 2.0, 10.0],
        "step_deg": [0.5, 2.0, 10.0],
        "step_z_mm": [1.0, 5.0, 20.0]
      }
    },
    "live_feed": {
      "stream": { "widget": "MJPEGViewer", "url": "/api/components/{tag_id}/telemetry/stream" }
    }
  },
  "primitives": [ "MOVE_COMPONENT", "SET_EXPOSURE", "STORE_COMPONENT", "...", "START_TELEOP", "START_LIVE_FEED" ]
}
```

Live feed uses a single **`stream`** channel (legacy `preview` / `JPEGPoll` removed).

## Package split

```text
lab_model/                      ← HOW THE LAB WORKS (semantics; no hardware I/O)
  language/                     ← Universal Component vocabulary
    domain/                     ← component, holding, storage, motor angles
    primitives/                 ← PrimitiveId, schemas, registry, dispatch
    tunables/ measurables/ telemetry/  ← plugin registries
  execution/                    ← how verbs run
    orchestration/              ← host-protocol templates (teleop, moves, …)
    edge/                       ← EdgeClient, streams, poll-attach registry
    optimization/               ← ensemble, kernels, solvers
  coordinator/                  ← this server’s control plane
    backends/                   ← registry, lab_view_config
    catalog/                    ← library, pins, hash
    jobs/                       ← leases, queue, runners
    state/                      ← RuntimeManager, ControlManager, reconcile
  platform.py                   ← integrity / registry export

mock_backend/                      ← teaching Edge Contract host (outside lab_model)
lab cloudlabs_edge/             ← physical bench edge (sibling repo)
```

**Import rule:** `lab_model` must **not** import edge host packages (`mock_backend`, lab
`cloudlabs_edge`). Hosts and `main.py` import `lab_model`. Southbound from the
coordinator is only via `lab_model.execution.edge` (EdgeClient).

## Primitive dispatch flow

```text
POST /api/command  (or Twin aliases → EdgeClient)
  → lab_model.language.primitives.parse_command_payload
  → schedule_validated_command / execute_validated_command
  → edge host _primitive_* / orchestration templates
```

Dedicated HTTP routes (same semantics as primitives):

| Route | Primitive |
|-------|-----------|
| `POST .../teleop/start` | `START_TELEOP` |
| `POST .../teleop/end` | `END_TELEOP` |
| `POST .../telemetry/jog` | `TELEOP_JOG` |
| `POST .../telemetry/live-feed/start\|end` | `START_LIVE_FEED` / `END_LIVE_FEED` |
| `POST .../measurables/record` | `RECORD_MEASURABLES` |

Read paths:

- `GET .../tunables` → `fetch_read_primitive(GET_TUNABLES)` → `statecontrol.tunables`
- `GET .../measurables` → `fetch_read_primitive(GET_MEASURABLES)` → `statecontrol.measurables`
- `GET .../telemetry` → saved `telemetry` slice (teleop + live_feed session state)
- `GET .../camera-image` → PNG file from `measurables.camera_image.path`

## Frontend mirror

| Backend | Frontend |
|---------|----------|
| `capabilities.statecontrol.tunables` | `component-viewer.js` → STATE CONTROL → Tunables (read-only widgets) |
| `capabilities.statecontrol.measurables` | `component-viewer.js` → STATE CONTROL → Measurables |
| `capabilities.telemetry` + runtime `telemetry` | `component-viewer.js` → TELEMETRY (session JSON + gated MJPEG when live) |
| `capabilities.primitives` | `frontend/js/primitives/*.js` — **all writes** |

Orchestration: `frontend/js/ui/component-popup.js` (read-only panel + PRIMITIVES block).

### Primitive UI display order

`frontend/js/primitives/index.js` sorts the catalog allow-list:

1. **Placement + tunable control** — `MOVE_COMPONENT`, then `SET_EXPOSURE`, motor tunables, …
2. **Manipulation** — storage, pick, hover, place
3. **Workflow** — `RECORD_MEASURABLES`, `OPTIMIZE`, `SCAN_ROTATE_IN_PLACE`
4. **Telemetry sessions** — TeleOp + live feed (always last)

## Add a new tunable (checklist)

1. `lab_model/language/tunables/<field>.py` — `@register_tunable`, commit, optional hardware.
2. `lab_model/primitives` — `SET_<FIELD>` id, schema, handler.
3. `component_library.json` — `statecontrol.tunables` descriptor + primitive in list.
4. `frontend/js/widgets/` + `frontend/js/primitives/set-<field>.js`.
5. Mock/real hook only if new hardware API is required.

## Refactor phases (roadmap)

| Phase | Status | Deliverable |
|-------|--------|-------------|
| 0–7 | Done | Primitives, plugins, registries, thin bridge, frontend registry mirror |
| 8 | Done | Per-component TeleOp (lease, jog, canvas drag, `telemetry.teleop`) |
| 9 | Done | Per-component telemetry routes; lab-wide table-cam routes removed |
| UC | Done | `statecontrol` + `telemetry` shape; UI read-only vs primitives split |

Historical one-shot migration scripts live under `scripts/archive/`.

## RuntimeManager & ControlManager

**Status:** implemented — see [`../../docs/LAB_SURFACES_VC_AND_INITIALIZATION.md`](../../docs/LAB_SURFACES_VC_AND_INITIALIZATION.md).

| Manager | Role |
|---------|------|
| **RuntimeManager** | Live runtime JSON mutations (working tree). |
| **ControlManager** | Configuration version history (commits, branches, setups). **Not** lab-side `OpticalExperiment`. |

Aggregates: **Configuration** (tunables), **Observations** (measurables), **Setup** (both), **Runtime** (working tree). Teaching edge: [`../../mock_backend/`](../../mock_backend/).

## Repository layout

| Path | Role |
|------|------|
| `lab_model/language/domain/` | Component shapes, holding, storage geometry, motor angles |
| `lab_model/execution/orchestration/` | TeleOp, live feed, moves, record, optimize, … |
| `lab_model/execution/edge/` | EdgeClient southbound |
| `../../mock_backend/` | Teaching Edge Contract host |
| `frontend/js/ui/component-viewer.js` | Read-only StateControl + Telemetry |
| `frontend/js/primitives/` | Primitive forms (write path) |

## Related docs

- [`README.md`](README.md) — StateControl tunables vs measurables
- [`language/primitives/README.md`](language/primitives/README.md) — HTTP command layer
- [`../../mock_backend/README.md`](../../mock_backend/README.md) — teaching edge
- [`../../docs/EDGE_CONTRACT_AND_UC_LIVE_PLANE.md`](../../docs/EDGE_CONTRACT_AND_UC_LIVE_PLANE.md) — Edge Contract
- [`../../docs/LAB_SURFACES_VC_AND_INITIALIZATION.md`](../../docs/LAB_SURFACES_VC_AND_INITIALIZATION.md) — surfaces + VC
- [`../../schemas/edge_contract/v1/`](../../schemas/edge_contract/v1/) — machine-readable edge schemas
