# Primitive UI contract (v1)

## Rule

**STATE CONTROL, TELEMETRY, and capability panels are read-only.** Every state change uses a **named primitive** with an explicit parameter object (no `parameters: {}` stubs).

| Panel | Shows | Writes via |
|-------|-------|------------|
| **STATE CONTROL → Tunables** | Current intent (`nominal_pose`, `exposure_time_ms`, …) | `MOVE_COMPONENT`, `SET_EXPOSURE`, `SET_MOTOR_SETPOINT`, … |
| **STATE CONTROL → Measurables** | Recorded observations (`pose`, `camera_image`, …) | `RECORD_MEASURABLES` |
| **TELEMETRY** | Session state (`teleop.active/ready`, `live_feed.stream`, …) + gated stream view when live | `START_TELEOP`, `END_TELEOP`, `START_LIVE_FEED`, `END_LIVE_FEED`, `TELEOP_JOG` |
| **PRIMITIVES** | One region per catalog allow-list entry | Same primitives as above |

TeleOp controls are split by context (catalog declares both `rz` and `pose3d`):

| Channel | When shown | Allows |
|---------|------------|--------|
| **`rz`** | Part on table (not in gripper) | In-place rotation only — no canvas XY drag |
| **`pose3d`** | Part held in gripper | Full XYZ + Rz (widget + canvas XY while held) |

Jog UI appears only when the server sets `telemetry.teleop.ready === true` (after `_primitive_prepare_teleop`). While `active && !ready`, the UI shows **Loading TeleOp…**.

## Tunable field → primitive

| Tunable field | Primitive | Parameters |
|---------------|-----------|------------|
| `exposure_time_ms` | `SET_EXPOSURE` | `{ exposure_time_ms }` |
| `nominal_motor_positions` | `SET_MOTOR_SETPOINT` | `{ motor_id, angle_deg }` |
| `nominal_pose` (table) | `MOVE_COMPONENT` | `{ target_x, target_y, rotation }` |
| `nominal_pose` (held) | `HOVER` / `PLACE_FROM_HOVER` | pose + `z` for hover |
| `storage` / presence | `STORE_COMPONENT`, `PLACE_FROM_STORAGE`, … | per primitive |

Relative motor jog (does not update nominal): `MOVE_MOTOR` with `{ motor_id, distance }`.

TeleOp pose: `START_TELEOP` → (lab ready) → `TELEOP_JOG` frames or canvas drag → `END_TELEOP`.

Live camera: `START_LIVE_FEED` → MJPEG in TELEMETRY panel when `live_feed.stream.live` → `END_LIVE_FEED`. `RECORD_MEASURABLES` ends live feed first.

## Macro: `APPLY_TUNABLES_PATCH`

Recipe convenience only. Fixed order in `backend/lab_model/primitives/macros/apply_tunables_patch.py`:

1. Hyperparameters (`exposure_time_ms`, …)
2. Motor setpoints (`nominal_motor_positions`)
3. Placement intent (`nominal_pose`, `presence`, `storage`) — **no motion**
4. Motion primitives are **not** invoked from this macro

## Frontend layout

```
Component popup (component-popup.js)
├── STATE CONTROL (read-only)
│   ├── Tunables
│   └── Measurables
├── TELEMETRY (read-only session + stream when live)
└── PRIMITIVES (write path; sorted display order)
    ├── MOVE COMPONENT
    ├── SET EXPOSURE / motor tunables …     ← tunable-control group
    ├── STORE / PICK / HOVER …              ← manipulation group
    ├── RECORD MEASURABLES / OPTIMIZE …     ← workflow group
    └── START TELEOP / START LIVE FEED …    ← telemetry group (last)
```

Implementations: `frontend/js/ui/component-viewer.js`, `frontend/js/primitives/` (UI catalog), `backend/lab_model/primitives/` (dispatch).

Display order is enforced in `frontend/js/primitives/index.js` (`PRIMITIVE_DISPLAY_ORDER`), independent of catalog JSON order.

## Platform registries

- **Backend:** `GET /api/platform/registries` — tunable/measurable plugins + primitive metadata (`lab_model.platform.export_platform_registries`).
- **Frontend:** `frontend/js/lab-capabilities.js` — preloaded at startup via `loadPlatformRegistries()`.

## Measurables

Each catalog `capabilities.statecontrol.measurables` entry names a field and a **widget** (`ImageViewer`, `MotorRotationsReadout`, `NumberBadge`, …). `RECORD_MEASURABLES` runs `lab_model.measurables.observe_for_tag`, which iterates declared fields and registered observers (`camera_image`, `pose`, …). Mock fills synthetic camera PNGs under `{LAB_VIEW_PATH}/camera_captures/`.
