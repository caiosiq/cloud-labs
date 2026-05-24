# lab_automation ↔ cloud-labs: deep analysis (real bench + TeleOp)

**Audience:** anyone planning to bring the **real communicator** and **lab_automation** in line with the refactored cloud-labs framework (capabilities, TeleOp v2, statecontrol/telemetry).

**Repos:**

| Repo | Path (typical) | Role |
|------|----------------|------|
| **cloud-labs** | `optics-digital-twin/` | UI, JSON state machine, catalog, HTTP primitives, TeleOp session model |
| **lab_automation** | sibling `lab_automation/` | xArm, cameras, steppers, vision, `OpticalExperiment` motion |

**Date:** May 2026 (post TeleOp v2 frontend + tunable commit work in mock).

---

## 1. Executive summary

You are **correct** to push back on a “thin TeleOp seam.” The gap is not a missing wrapper in `real/primitives.py` — it is a **fundamental mismatch of control models**:

| | **cloud-labs TeleOp v2 (today)** | **lab_automation (today)** |
|---|----------------------------------|----------------------------|
| Control style | High-rate **intent stream** (target_pose, live poll ~20 Hz) | **Blocking macro moves** (`move_to(..., wait=True)`) |
| Session | `telemetry.teleop` + in-memory `TeleopLivePoseStore` | `is_physically_holding` + discrete `*_cloudlab` calls |
| Rotation on table | UI nudges θ continuously; canvas LIVE/TARGET | **`scan_rotate_placed_cloudlab`**: approach → **grasp** → sweep → **release** (atomic) |
| Live feedback | HTTP poll + optional MJPEG | No robot pose stream; camera stream only on **industrial recorder TCP** |
| TeleOp in lab_automation | **Does not exist** (zero `teleop`/`jog` symbols) | Stages 0–9 of `newprimitives.md` **done** for pick/hover/place/scan-rotate |

**Mock cloud-labs** exercises the full TeleOp *UI contract* with a **software motion simulator** (`TeleopLivePoseStore`). That is appropriate for UI development but **must not be mistaken** for what real hardware can do today.

**Real bench work** requires coordinated changes in **three layers**:

1. **lab_automation** — new live-control primitives (modes, queues, pose publisher, session locks).
2. **cloud-labs real communicator** — boot integrity, v1 state shape, frame transforms, bridge TeleOp → lab session APIs.
3. **Catalog / UX** — honest primitive surfaces (e.g. table Rz TeleOp may require an explicit **setup macro**: approach + grasp + enter live mode).

---

## 2. Architecture (how the pieces connect today)

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Frontend (TeleopRz, TeleopPose3d, LivePosePoll, canvas LIVE/TARGET)     │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │ HTTP
┌───────────────────────────────▼─────────────────────────────────────────┐
│  cloud-labs backend/main.py                                              │
│    POST …/teleop/start|end                                               │
│    POST …/telemetry/goto  (TELEOP_GOTO)                                  │
│    GET  …/telemetry/live-pose                                            │
│    POST /api/command  (PICK, HOVER, SCAN_ROTATE_IN_PLACE, …)             │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────────────┐
│  lab_model (backend-agnostic)                                            │
│    orchestration/teleop.py      → TeleopController                       │
│    orchestration/teleop_live_pose.py → in-memory pose + motion loop      │
│    state/commits.py             → commit_teleop_*, commit_* poses        │
│    primitives/dispatch.py       → PRIMITIVE_REGISTRY                     │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │ LabCommunicator template
        ┌───────────────────────┴───────────────────────┐
        │                                               │
┌───────▼──────────────┐                    ┌───────────▼──────────────────┐
│ MockLabCommunicator  │                    │ RealLabCommunicator           │
│ JSON persist         │                    │ OpticalExperiment (sync)      │
│ Simulated HW         │                    │ asyncio.to_thread wrappers    │
└──────────────────────┘                    └───────────┬──────────────────┘
                                                        │
                        ┌───────────────────────────────▼──────────────────┐
                        │  lab_automation                                         │
                        │    OpticalExperiment  →  AssemblyManager  →  XArmDriver │
                        │    CameraDriver (USB) | Recorder TCP (industrial)       │
                        │    WiFiStepperController | RealSense pipeline           │
                        └────────────────────────────────────────────────────────┘
```

**Design rule (documented, mostly followed):** cloud-labs owns **lab-frame** geometry and JSON truth; lab_automation owns **robot-frame** motion. Transforms live in `real/coordinate_frames.py`.

---

## 3. What cloud-labs TeleOp v2 expects

From the refactored frontend + `lab_model`:

| Concept | Storage | Meaning |
|---------|---------|---------|
| **current_pose** | `store.teleopLivePose` / GET live-pose | High-rate hardware truth during session |
| **target_pose** | `store.teleopTarget` / TELEOP_GOTO body | Operator plan |
| **Session** | `telemetry.teleop.{active,ready,command}` | Lease + command phase (`idle` / `executing`) |
| **Commit** | `tunables.nominal_pose` on idle/end | Canvas-truth intent after session |

**Modes (catalog-driven):**

- **`rz`** — breadboard component: goto payload is `{ rotation }` only; xy stay at nominal.
- **`pose3d`** — held object: full `{ x, y, z, rotation }`.

**Critical:** the UI assumes **target updates immediately** on nudge and **current** tracks a **fast poll**. That is a *continuous control* mental model.

---

## 4. What lab_automation actually implements

### 4.1 `OpticalExperiment` — the only motion facade

File: `lab_automation/managers/experiment_manager.py`

**Constructor:** `OpticalExperiment(mock: bool)` only — **no `catalog=`** yet (cloud-labs probes for it and falls back).

**Active cloud-lab API (Stages 3–6, implemented):**

| Method | Purpose |
|--------|---------|
| `scan_components_cloudlab` | Ceiling stereo/mono ArUco inventory |
| `pick_component_cloudlab` | Pick-only; sets **user holding session** |
| `hover_component_cloudlab` | Absolute in-air move while holding |
| `place_from_hover_cloudlab` | Place from hover; clears holding |
| `place_component_wo_home_specific_xy_cloudlab` | Monolithic on-table reposition |
| `scan_rotate_held_cloudlab` | θ sweep while **already gripped** (HOLDING) |
| `scan_rotate_placed_cloudlab` | **Transient grasp** on table → sweep → release |
| `get_gripper_status` | Gripper position heuristic |
| `compute_intent_hover_z_lab` | Z intent math for UI |

**Not implemented:** anything named `teleop_*`, `jog_*`, or incremental live control.

**Motion model:** every call eventually hits `XArmDriver.move_to(..., wait=True)` — the caller **blocks until the move finishes**. There is no command queue, no `wait=False` stream, no servo/jog mode exposed.

### 4.2 `AssemblyManager` — robot orchestration

File: `lab_automation/managers/robot_manager.py` (historical name)

Contains both **legacy** monolithic pick-and-place paths and **cloudlab** split-session paths (`pick_grasp_from_inventory`, `hover_gripped_to`, `scan_rotate_sweep_fixed_xyz`, `scan_rotate_placed_on_table`).

**Legacy code** is still in-tree: `pick_and_place`, reconstruction sweeps, alignment loops with multi-second sleeps. Optimization strategies may still call legacy recorder helpers.

### 4.3 `XArmDriver` — the real bottleneck for TeleOp

File: `lab_automation/drivers/xarm_driver.py`

- Initializes **`set_mode(0)`** — position control.
- **`move_to` / `move_relative` / gripper / joint moves** all use **`wait=True`**.
- **`get_pose()`** exists but is used **after** moves, not as a telemetry stream.
- **No:** jog API, velocity mode, motion cancel, concurrent command acceptance.

For interactive TeleOp at 10–30 Hz, this driver layer must grow — wrapping the same xArm SDK is fine, but the **API shape** must change.

---

## 5. Rotation TeleOp — your workflow vs the UI

### 5.1 What you described (physically correct for table rotation)

For **in-place rotation of a part on the breadboard**:

1. Robot approaches the component.
2. Gripper **closes on the part** (transient grasp).
3. Arm holds position and **awaits rotation commands** (wrist θ or equivalent).
4. On session end: open gripper, retract, commit final angle to tunables.

That is **exactly** the semantics of **`scan_rotate_placed_cloudlab`**, but implemented as a **single atomic primitive** with a **pre-planned θ sweep** (`theta_min` → `theta_max` at `speed_deg_per_s`), not as an open-ended TeleOp session.

### 5.2 What TeleOp v2 UI does today

1. `START_TELEOP` → immediate `ready` on real (no setup).
2. Operator nudges **target** rotation on buttons or canvas wheel.
3. `TELEOP_GOTO` → **software interpolation** in `TeleopLivePoseStore` (no robot call on real).
4. `END_TELEOP` → commit pose to tunables (recent fix).

**Gap:** there is **no step** that performs approach + grasp before “live rotation.” The UI assumes the part is already in a controllable state.

### 5.3 Two different products hiding under “Rz TeleOp”

| Model | Setup | Control | Teardown | Best match in lab_automation |
|-------|-------|---------|----------|------------------------------|
| **A. Atomic scan-rotate** | implicit in one command | fixed sweep | implicit | `scan_rotate_placed_cloudlab` |
| **B. Interactive live rotation** | approach + grasp macro | streaming Δθ or absolute θ | release + retract | **does not exist** |

The mock UI implements **B** in software. The real robot only has **A** as a batch API.

**Design implication:** table Rz TeleOp on real likely needs a **explicit setup phase** in `prepare_teleop` or a dedicated macro:

```
ENTER_ROTATION_TELEOP = PICK-on-table (transient) + enter live θ mode
… TELEOP_GOTO rotation commands …
EXIT = open + retract + commit
```

That is closer to **`scan_rotate_placed_on_table` split into setup / live / teardown** than to calling `hover_component_cloudlab`.

### 5.4 Held-object TeleOp (pose3d)

Here the existing pieces align **better**:

- `pick_component_cloudlab` → real HOLDING session.
- Repeated **`hover_component_cloudlab(x,y,z,rotation)`** could serve as **discrete** gotos.

But each goto is still a **full blocking move** — unusable for continuous jog unless refactored to non-blocking or servo mode.

---

## 6. Camera architecture — “table top cam is NOT a component”

lab_automation has **three camera tiers**, none modeled as `OpticalComponent`:

| Tier | Implementation | IDs | Role |
|------|----------------|-----|------|
| **Ceiling / inventory** | OpenCV `CameraDriver` | USB **0**, **5** | ArUco table scan |
| **Gripper (alignment)** | OpenCV `CameraDriver` | USB **1**, **2** | Beam centroid, RealSense-assisted pick |
| **Industrial / “table cam”** | TCP recorder subprocess | TCP **9999**, **10000** | High-res beam capture, MJPEG via cloudlab protocol |

**cloud-labs catalog** exposes `OPTICAL_CAMERA` tags (`cam_gripper_1/2`) with TeleOp + live_feed capabilities — but **`resolve_cam_id` maps to TCP recorder cam 1/2**, not OpenCV USB 1/2. Same numbers, **different hardware stacks**. This is a chronic source of confusion.

**Live streaming today:**

- **Only** the cloudlab recorder path implements **`STREAM_ON` + `GET_JPEG`** with reasonable preview latency design.
- OpenCV ceiling/gripper cameras: synchronous `read()` on demand — **no streaming server** in lab_automation.
- cloud-labs real `get_video_stream` for overhead uses **`experiment.ceiling_cam1`** (OpenCV), separate from per-tag MJPEG.

**Gaps for measurable / TeleOp feedback:**

- Real lacks `capture_overhead_cam()` (mock has it).
- `RECORD_MEASURABLES` on real uses recorder TCP, not gripper OpenCV.
- Optimization strategies still use **legacy** `recorder_capture_helpers.py` while UI live feed uses **cloudlab** helpers — dual protocols on the same ports.

**Takeaway:** cameras need a **first-class driver abstraction** in lab_automation (connect / stream / capture / exposure) that cloud-labs catalog references by **backend type**, not by reusing gripper USB indices as recorder IDs.

---

## 7. Legacy vs active — honest inventory

### 7.1 lab_automation

| Status | Examples |
|--------|----------|
| **Active (cloudlab contract)** | All `*_cloudlab` methods, `utils/cloudlab_contract.py`, `utils/clearance.py`, cloudlab recorder script |
| **Legacy (still present)** | `place_component*`, `scan_components`, `original_codes/*` monoliths, legacy recorder helpers |
| **Deferred (Stage 10)** | Camera sync during scan-rotate, grip-force boot inference, live ghost on real |
| **Missing entirely** | TeleOp, jog, catalog constructor, component-modeled cameras |

`newprimitives.md` Stages 0–9 are marked **done** for discrete primitives. Stage 7 manual hardware checklist is still **open**.

### 7.2 cloud-labs real communicator

| Status | Issue |
|--------|-------|
| **Structurally aligned** | Inherits `LabCommunicator`, uses orchestration + commits |
| **Boot regression** | Init body after `initialize_robot()` appears truncated — `component_map`, scan, recorders may never run (dead code after `return` in `_load_catalog_for_lab_automation`) |
| **State shape** | `real/scan.py` emits legacy flat components, not v1 `statecontrol` + `telemetry` |
| **Frame** | Scan may write robot-frame poses without inverse transform to lab frame |
| **TeleOp** | Uses shared simulator; `_primitive_prepare_teleop` is base no-op |
| **Catalog passthrough** | Implemented in cloud-labs; lab_automation doesn't accept `catalog=` yet |

### 7.3 Deprecated concepts still in docs/comments

- `tunables.teleop_active` → now `telemetry.teleop`
- `TELEOP_JOG` → alias for `TELEOP_GOTO`
- `HOVER_PLACEHOLDER_STATE` → removed from real; comments remain in lab_automation docs
- `SCAN` primitive → dispatch no-op

---

## 8. Live control — what “minimize delay” actually requires

TeleOp is not just “call `hover_component_cloudlab` from goto.” A responsive loop needs:

### 8.1 Robot path

1. **Non-blocking or servo-mode motion** — xArm SDK supports modes beyond `set_mode(0)`; driver must expose jog/velocity or rapid queued deltas with **`wait=False`** and explicit **stop**.
2. **Command queue + worker thread** — HTTP handlers enqueue targets; worker runs at fixed rate; avoids `asyncio.to_thread` per nudge.
3. **Pose publisher** — background read of `get_pose()` at 20–50 Hz into a shared buffer (mirror of `TeleopLivePoseStore` but fed from hardware).
4. **Session lock** — TeleOp session excludes PICK/PLACE/OPTIMIZE macros; estop clears queue.
5. **Safety** — reduced speed, workspace clamps, gripper state checks in the control loop.

### 8.2 cloud-labs path

1. **Replace or augment** `TeleopLivePoseStore._motion_loop` on real with hardware-fed pose (keep simulator on mock).
2. **`prepare_teleop`** runs setup macro (for table Rz: approach + transient grasp).
3. **`end_teleop`** runs teardown macro (open + retract) then existing tunable commit.
4. **Rate limiting** — UI sends gotos at ~10 Hz; backend coalesces to latest target.

### 8.3 Camera path (parallel)

If TeleOp should show live beam/part feedback:

- Extend cloudlab recorder pattern **or** add lightweight MJPEG servers for OpenCV cameras.
- Unify optimization capture onto one recorder protocol.

**Latency budget (rough):**

| Path | Today | Target for TeleOp |
|------|-------|-------------------|
| Robot move round-trip | 100 ms – several s (blocking) | 20–50 ms command acceptance; motion continuous |
| Pose poll | N/A (simulated) | ≤50 ms stale pose |
| Preview JPEG | ~450 ms client timeout | ≤100 ms for operator feedback |

---

## 9. Recommended roadmap (both repos)

Phases are ordered by dependency. **lab_automation depth increases in Phase 3+** — this matches your intuition that TeleOp is not a thin adapter.

### Phase 0 — Real communicator boot (cloud-labs only)

Fix `RealLabCommunicator.__init__` so scan, `component_map`, stored intent, gripper reconcile, and recorders actually run. Without this, no primitive is trustworthy on hardware.

### Phase 1 — Contract parity (cloud-labs + scan)

- Real scan emits **v1** `statecontrol` + `telemetry`.
- Robot → lab inverse transform on all poses entering JSON.
- Orchestrators use `meas_pose()` / `nominal_pose()` accessors consistently.

### Phase 2 — Discrete primitive golden path (mostly done in lab_automation)

Verify on hardware: rescan → pick → hover → place → scan_rotate (held + placed) → motor move → record measurables. Close Stage 7 manual checklist in `newprimitives.md`.

### Phase 3 — **TeleOp session model in lab_automation** (new work)

Define lab-side **live control session** API (names TBD), e.g.:

```text
enter_live_rotation_session(component)  # approach + transient grasp
live_set_rotation(component, theta_deg, speed)  # non-blocking
live_get_end_effector_pose() -> dict
exit_live_rotation_session()  # open + retract
```

For held pose3d:

```text
enter_live_hover_session(component)  # already holding after pick
live_goto_pose(component, x, y, z, yaw, speed)
exit_live_hover_session()  # optional; may stay holding
```

Implement in `AssemblyManager` + `XArmDriver` extensions — **not** in cloud-labs.

### Phase 4 — Bridge cloud-labs TeleOp → lab session (real communicator)

- `_primitive_prepare_teleop` → `enter_*_session` based on `telemetry.teleop.mode`.
- On goto: enqueue to lab session (not `TeleopLivePoseStore` sim loop on real).
- Live pose GET: read lab session buffer (hardware-fed).
- `_on_teleop_motion_idle` / end: teardown + existing `commit_teleop_session_pose`.

### Phase 5 — Cameras & catalog honesty

- Catalog field: `properties.cam_backend: recorder | opencv_ceiling | opencv_gripper`.
- Real overhead capture; unify recorder protocols for optimize vs live feed.
- Optional: gripper view stream for held TeleOp.

### Phase 6 — lab_automation catalog mode

- `OpticalExperiment(catalog=...)` per `CLOUDLAB_CONTRACT.md`.
- Deprecate hardcoded stepper/camera wiring where catalog provides it.

---

## 10. Open design questions (worth deciding before coding)

1. **Table Rz TeleOp:** Is **transient grasp** during the whole session acceptable (part lifted slightly)? Or must rotation happen with part **on table** without lift (force-limited grasp)? This affects which arm poses are allowed.

2. **Unexecuted target on END:** Commit **live** pose vs **target** pose if operator nudged but motion lagged — cloud-labs recently commits live buffer; confirm for real latency.

3. **SCAN_ROTATE vs TeleOp:** Do we keep both? TeleOp could **replace** discrete scan-rotate for human operation; recipes might still use atomic scan-rotate.

4. **Motorized mounts (WiFi steppers):** Table Rz for **motorized** components might belong to **`MOVE_MOTOR` / SET_MOTOR_SETPOINT** TeleOp, not arm TeleOp — catalog should gate which backend serves `telemetry.teleop.mode`.

5. **Single vs dual pose streams:** Arm TCP pose vs component pose (held part offset) — which does LIVE represent on canvas?

6. **Failure modes:** Gripper slip during live session — detect how (`get_gripper_status` only?) and whether to abort TeleOp lease automatically.

---

## 11. Key file index

### cloud-labs (`optics-digital-twin`)

| File | Topic |
|------|-------|
| `backend/lab_communicator/base.py` | Template, TeleopController wiring, default prepare |
| `backend/lab_communicator/real/communicator.py` | Boot, OpticalExperiment, init bug |
| `backend/lab_communicator/real/primitives.py` | Hardware dispatch (`asyncio.to_thread`) |
| `backend/lab_communicator/real/coordinate_frames.py` | Lab ↔ robot transforms |
| `backend/lab_communicator/real/scan.py` | Boot/rescan state shape |
| `backend/lab_model/orchestration/teleop.py` | Session + goto |
| `backend/lab_model/orchestration/teleop_live_pose.py` | Simulated motion loop |
| `backend/lab_model/state/commits.py` | `commit_teleop_session_pose` |
| `CLOUDLAB_CONTRACT.md` | Cross-repo catalog boundary |
| `docs/primitive_ui_contract.md` | UI ↔ backend expectations |

### lab_automation

| File | Topic |
|------|-------|
| `managers/experiment_manager.py` | `OpticalExperiment`, all `*_cloudlab` |
| `managers/robot_manager.py` | `AssemblyManager` motion |
| `drivers/xarm_driver.py` | Blocking position control |
| `drivers/camera_driver.py` | OpenCV USB |
| `scripts/recorder_cam_laser_align_cloudlab.py` | Industrial cam TCP + MJPEG |
| `managers/recorder_capture_helpers_cloudlab.py` | Recorder client |
| `managers/recorder_capture_helpers.py` | **Legacy** recorder client |
| `newprimitives.md` | Stages 0–10 roadmap |
| `original_codes/` | Legacy monolith archive |
| `tests/test_cloudlab_primitives_mock.py` | Holding session smoke tests |

---

## 12. Bottom line

- **cloud-labs** has a modern **TeleOp product surface** (session, live poll, target/current, tunable commit) built and validated in **mock**.
- **lab_automation** has a modern **discrete primitive** surface (pick/hover/place/scan-rotate) validated through Stage 9 — but **no live control layer** and **no TeleOp vocabulary**.
- **Real integration** requires treating TeleOp as a **new lab_automation subsystem** (setup / live loop / teardown + driver changes), then bridging it from cloud-labs — not mapping `TELEOP_GOTO` to a single `hover_component_cloudlab` call.
- **Cameras** are not components in lab_automation; catalog naming and dual recorder protocols need cleanup before TeleOp can show trustworthy live imagery on the bench.

This document should be the reference for the next planning conversation: which TeleOp mode (table Rz vs held pose3d) to implement first on hardware, and what the lab_automation live-session API should look like before any large cloud-labs or lab_automation PR.
