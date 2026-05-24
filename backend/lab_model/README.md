# Lab model: StateControl, Telemetry, and primitives

This folder is the **`lab_model`** Python package: shared domain logic for the digital twin (not hardware I/O). It defines how each **Universal Component** splits **slow/formal state** (StateControl) from **fast/live sessions** (Telemetry).

**Platform map:** [`ARCHITECTURE.md`](ARCHITECTURE.md) · **UI rules:** [`../../docs/primitive_ui_contract.md`](../../docs/primitive_ui_contract.md) · **Commands:** [`primitives/README.md`](primitives/README.md)

---

## 1. What `lab_model` is (and is not)

**`lab_model`** holds **shared domain logic** that does not talk to hardware by itself:

| Module | Role |
|--------|------|
| **`domain/component.py`** | Per-component **statecontrol** + **telemetry**, presence, accessors, shape normalization. |
| **`domain/holding.py`** | Top-level **holding** block and **system_status** (including TELEOP). |
| **`domain/storage_region.py`** | Q3 inventory grid; slot fit and layout helpers. |
| **`orchestration/`** | TeleOp, live feed, moves, record, optimize, in-air, … |
| **`measurables/`**, **`tunables/`**, **`telemetry/`** | Plugin registries for observe / commit / session metadata. |
| **`primitives/`** | Closed enum of lab verbs, Pydantic bodies, dispatch. |

Hardware I/O lives in **`lab_communicator`** (`mock/`, `real/`). Communicators import `lab_model` when updating JSON state or running orchestration.

---

## 2. Where state lives on each component

Each entry in `components[tag_id]` uses this shape (legacy flat `tunables`/`measurables` are migrated on load):

```text
components[tag_id] = {
  "id": "...",
  "type": "...",
  "statecontrol": {
    "tunables":   { ... },   ← commanded intent
    "measurables": { ... }   ← recorded observations
  },
  "telemetry": {
    "teleop": { "active", "ready", "lease_ts", "last_jog_ts", "last_error" },
    "live_feed": { "stream": { "connected", "live", "backend", ... } }
  }
}
```

**StateControl** answers: *What do we intend, and what have we formally recorded?*

**Telemetry** answers: *What live sessions are active right now?* (TeleOp lease, MJPEG stream). Telemetry is **not** mixed into tunables/measurables JSON slices.

---

## 3. StateControl — tunables (intent)

Defaults (`default_tunables()` in `component.py`):

| Area | Meaning |
|------|--------|
| **`presence`** | `breadboard`, `storage`, or `off_table`. |
| **`nominal_pose`** | Intended pose: `x`, `y`, `rotation` (mm / deg). |
| **`nominal_motor_positions`** | Motor id → commanded angle. |
| **`storage`** | `in_storage` and optional **`slot`** `{ i, j }`. |
| **`placement`** | e.g. **`mode`**: `MANUAL`, strategy name after optimize. |
| **`exposure_time_ms`** | Cameras only — shutter intent. |

Updated only via **primitives** (`MOVE_COMPONENT`, `SET_EXPOSURE`, …). The UI **read-only** tunables panel displays current values; it does not write them.

---

## 4. StateControl — measurables (observations)

Defaults (`default_measurables()`):

| Field | Meaning |
|-------|--------|
| **`pose`** | Measured center pose (layout, storage checks). |
| **`last_optimization_score`** | Scalar from last optimize run. |
| **`last_optimized_pose`** | Pose snapshot after optimization. |
| **`camera_image`** | After **`RECORD_MEASURABLES`**: `{ path, source, cam_id, format }` pointing at a PNG on disk; otherwise `null`. |

**`RECORD_MEASURABLES`** runs observers declared in **`capabilities.statecontrol.measurables`** (see `measurables/record.py`). Ends active live feed first.

Measurables are **receipts**, not the canvas ghost source of truth during normal editing (`tunables.nominal_pose` drives intent).

---

## 5. Telemetry — TeleOp and live feed

| Sub-block | Meaning | Primitives |
|-----------|---------|------------|
| **`telemetry.teleop`** | Per-component control lease | `START_TELEOP`, `END_TELEOP`, `TELEOP_JOG` |
| **`telemetry.live_feed.stream`** | MJPEG session for this tag | `START_LIVE_FEED`, `END_LIVE_FEED` |

TeleOp session fields:

- **`active`** — lease requested or held.
- **`ready`** — lab finished setup (`_primitive_prepare_teleop`); jog allowed only when true.
- **`lease_ts`** — refreshed on start, ready, and each jog; stale-lease sweeper uses this (default TTL 5 min).

Catalog declares TeleOp widget metadata under **`capabilities.telemetry.teleop`**: **`rz`** (table rotation-only) and **`pose3d`** (in-gripper XYZ + rotation), plus stream URL under **`capabilities.telemetry.live_feed.stream`**.

---

## 6. API surface

| Method | Path | Returns |
|--------|------|---------|
| GET | `/api/components/{tag_id}/tunables` | `statecontrol.tunables` |
| GET | `/api/components/{tag_id}/measurables` | `statecontrol.measurables` |
| POST | `/api/components/{tag_id}/measurables/record` | Fresh measurables after `RECORD_MEASURABLES` |
| GET | `/api/components/{tag_id}/telemetry` | Full `telemetry` slice |
| GET | `/api/components/{tag_id}/camera-image` | PNG file from `measurables.camera_image.path` |
| POST | `/api/components/{tag_id}/teleop/start` | `START_TELEOP` (returns while lab prepares; poll until `ready`) |
| POST | `/api/components/{tag_id}/telemetry/live-feed/start` | `START_LIVE_FEED` |

Full state: **`GET /api/lab-state`**.

---

## 7. Summary

| Concept | One-line |
|--------|-----------|
| **StateControl.tunables** | Commanded **intent** — changed by primitives only. |
| **StateControl.measurables** | **Recorded** observations — filled by `RECORD_MEASURABLES` and commits. |
| **Telemetry** | **Live sessions** (TeleOp, MJPEG) — separate from formal state. |
| **UI** | Read-only panels for StateControl + Telemetry; **PRIMITIVES** for all writes. |

For catalog JSON, see [`../../capability_contract.md`](../../capability_contract.md). For mock/real hooks, see [`../lab_communicator/README.md`](../lab_communicator/README.md).
