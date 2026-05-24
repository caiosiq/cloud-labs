# Lab platform architecture (`lab_model` + `lab_communicator`)

This document is the master map for how the optics digital twin backend is organized.
It describes the **Universal Component** model as implemented today: **StateControl** +
**Telemetry** on each component, one catalog per bench, and a thin hardware bridge.

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
lab_model/                 ← HOW THE LAB WORKS (semantics, no mock/real I/O)
  domain/                  ← component, holding, storage_region, motor_rotation_store
  orchestration/           ← teleop, live_feed, table_moves, motors, in-air, optimize, record
  primitives/              ← PrimitiveId, schemas, registry, dispatch, macros
  catalog/                 ← schema validation, bundle load, normalize_capabilities
  state/                   ← commits, state_machine, snapshot
  tunables/                ← @register_tunable plugins
  measurables/             ← @register_measurable plugins (+ observe_for_tag)
  telemetry/               ← teleop + live_feed registry plugins
  platform.py              ← validate_platform_integrity(), export_platform_registries

lab_communicator/          ← BRIDGE TO THIS BENCH
  base.py                  ← LabCommunicator: thin delegates + _primitive_* hooks
  mock/ / real/            ← hardware hooks (_primitive_*)
  shared/                  ← lab view paths, factory, file I/O
```

**Import rule:** `lab_model` must **not** import `lab_communicator`.
`lab_communicator` may import `lab_model`.

## Primitive dispatch flow

```text
POST /api/command  (or dedicated routes, e.g. teleop/start, measurables/record)
  → lab_model.primitives.parse_command_payload (commands only)
  → schedule_validated_command / execute_validated_command
  → LabCommunicator.<handler> (atomic) or macro expansion
  → orchestration/* + mock/real _primitive_* hooks
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

1. `lab_model/tunables/<field>.py` — `@register_tunable`, commit, optional hardware.
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

## Repository layout

| Path | Role |
|------|------|
| `lab_model/domain/` | Component shapes, holding, storage geometry, motor angles |
| `lab_model/orchestration/` | TeleOp, live feed, moves, record, optimize, … |
| `lab_communicator/` | Bridge: `base.py`, `mock/`, `real/`, `shared/` |
| `frontend/js/ui/component-viewer.js` | Read-only StateControl + Telemetry |
| `frontend/js/primitives/` | Primitive forms (write path) |

## Related docs

- [`README.md`](README.md) — StateControl tunables vs measurables
- [`primitives/README.md`](primitives/README.md) — HTTP command layer
- [`../lab_communicator/README.md`](../lab_communicator/README.md) — mock/real bridge
- [`../../capability_contract.md`](../../capability_contract.md) — catalog JSON spec
- [`../../docs/primitive_ui_contract.md`](../../docs/primitive_ui_contract.md) — UI rules
- [`../../universal_component_architecture.md`](../../universal_component_architecture.md) — design rationale (with implementation notes)
