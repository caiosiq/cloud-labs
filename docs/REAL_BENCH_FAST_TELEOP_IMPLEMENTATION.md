# Real bench implementation plan — primitives, fast TeleOp, unified components

**Companion to:** [LAB_AUTOMATION_AND_TELEOP_ANALYSIS.md](./LAB_AUTOMATION_AND_TELEOP_ANALYSIS.md)  
**Phased execution:** [REAL_BENCH_ROADMAP.md](./REAL_BENCH_ROADMAP.md)

**Audience:** implementation work across **cloud-labs** (`optics-digital-twin`) and **lab_automation`.

This document is the **complete build plan** for:

1. Wiring every cloud-labs primitive on the real bench (registry → handler → hardware).  
2. Making TeleOp and live telemetry **fast** (servo mode, WebSocket, camera preview profiles).  
3. **Unified component model** — table-top camera, ceiling cams, future lasers, etc. as first-class catalog components with `tag_id`, mirrored in lab_automation.  
4. **lab_automation simplification** — delete legacy scripts, introduce `LabComponent` hierarchy specialized for cloud-labs.

---

## 1. Executive summary

Four problems must be solved together (not in isolation):

| Problem | Wrong fix | Right fix |
|---------|-----------|-----------|
| **Primitive names not reaching hardware** | Patch one method | Full **registry → handler → lab API** map for every `PrimitiveId` |
| **TeleOp UI feels live in mock** | Call `hover_component_cloudlab` per nudge | **Servo-mode motion loop** + **duplex WebSocket** (not HTTP GET @ 20 Hz) |
| **Live camera feels sluggish** | Same path as `RECORD_MEASURABLES` | **Dedicated preview profile** (scale, JPEG quality, drain policy) |
| **Cameras/lasers not real components in lab_automation** | More `if cam_id` in `video.py` | **Catalog-driven `LabComponent` registry** in both repos; **sync component_library** |

**Unified component rule (cloud-labs):**  
If something appears in the catalog with **tunables, measurables, telemetry, or primitives**, it is a **component** keyed by **`tag_id`**. The UI, HTTP routes, and lab state all use that key: `/api/components/{tag_id}/…`.

**Unified component rule (lab_automation):**  
If cloud-labs can **command or read** it, lab_automation holds a **`LabComponent` instance** in a registry keyed by the same `tag_id`. **`OpticalComponent`** (mirrors, lenses, filters) is one **subclass** for manipulable table optics — not the only kind of component.

**Table-top camera gap today:**

| Location | Table-top / overhead camera |
|----------|----------------------------|
| **Mock** `component_library.json` | ✅ `tag_99` — `id: cam_table_top`, `stream_source: overhead` |
| **Real** `component_library.json` | ❌ **missing** — no `tag_99` |
| **lab_automation** | ❌ `ceiling_cam1` / recorder TCP — **fields on `OpticalExperiment`**, not in `component_map` |

This plan includes **catalog reorganization + lab_automation class hierarchy + deletion of unused legacy code** so the bench matches the language cloud-labs already speaks.

**Yes — every primitive in `PRIMITIVE_REGISTRY` must have an explicit real path.**  
cloud-labs **dispatch already routes** `START_TELEOP`, `END_TELEOP`, `TELEOP_GOTO`, `RECORD_MEASURABLES`, etc. to `LabCommunicator` methods. The break is **below** that line: `RealLabCommunicator` + `lab_automation` still behave like the pre-TeleOp era for motion and telemetry.

**Yes — HTTP GET live-pose @ 20 Hz should be replaced** for real (and eventually mock) sessions. Use a **server-push channel** (WebSocket recommended) for pose (and optionally preview metadata). Keep HTTP for **session boundaries** (`START` / `END`) and **slow** state (`GET /api/lab-state`).

**Yes — xArm position mode (`set_mode(0)`, `wait=True`) is incompatible with live nudging.**  
TeleOp motion requires **servo Cartesian mode** (`set_mode(1)` or vendor-equivalent) with a **high-rate command consumer** and a **parallel pose publisher**.

---

## 2. Registry model (what “mapped in RealLabCommunicator” means)

cloud-labs uses a **closed primitive registry** — same pattern as tunables/measurables/telemetry plugins:

| Layer | Registry | Registration |
|-------|----------|--------------|
| Tunables | `TUNABLE_REGISTRY` | `@register_tunable` |
| Measurables | `MEASURABLE_REGISTRY` | `@register_measurable` |
| Teleop UI fields | `TELEOP_REGISTRY` | `@register_teleop_control` |
| **Lab commands** | **`PRIMITIVE_REGISTRY`** | **`handler: "<method on LabCommunicator>"`** |

File: `backend/lab_model/primitives/registry.py`

**Dispatch rule:** `execute_validated_command` → `_invoke_atomic` → `getattr(lab, handler)(...)`.

So “mapping” a primitive on real means **all** of:

1. Entry in `PRIMITIVE_REGISTRY` with correct `handler` name *(mostly done)*  
2. Method on `LabCommunicator` / `RealLabCommunicator` *(partially done)*  
3. Orchestrator in `lab_model/orchestration/*` *(done for most)*  
4. **`_primitive_*` or dedicated hook that calls lab_automation** *(gaps)*  
5. **Matching API in `OpticalExperiment` / drivers** *(TeleOp missing)*  

### 2.1 Proposed real-side registration discipline

Add an explicit **Real primitive binding table** (code or generated doc test) so nothing ships half-wired:

```text
PrimitiveId.TELEOP_GOTO
  → LabCommunicator.teleop_goto
  → TeleopController.goto
  → RealLabCommunicator._teleop_command_sink   # NEW: queue, not blocking move
  → lab_automation.LiveControlSession.set_target(...)
```

Same for `RECORD_MEASURABLES`, `START_LIVE_FEED`, etc.

Optional: `@register_real_primitive(PrimitiveId.TELEOP_GOTO)` decorator on real hooks — mirrors telemetry plugins and gives a single import-time audit.

---

## 3. Full primitive map (cloud-labs → real → lab_automation)

Status as of this writing. **Handler** = `PRIMITIVE_REGISTRY` method on `LabCommunicator`.

### 3.1 Read / tunable / measurable

| Primitive | Handler | Real today | lab_automation | Notes |
|-----------|---------|------------|----------------|-------|
| `GET_TUNABLES` | `return_tunables_for_tag` | ✅ state read | — | |
| `GET_MEASURABLES` | `return_measurables_for_tag` | ✅ state read | — | |
| `RECORD_MEASURABLES` | `record_measurables_for_tag` | ⚠️ table cam CAP path | recorder `CAP` (slow) | **Not** same as live stream |
| `SET_EXPOSURE` | `set_exposure_time_ms` | ⚠️ table `VEXP` when cam_id resolves | TCP to recorder | Overhead tags often intent-only |
| `SET_MOTOR_SETPOINT` | `set_motor_setpoint` | ✅ | `wifi_stepper.move_motor` | |
| `APPLY_TUNABLES_PATCH` | macro | ✅ | composes above | |

### 3.2 Table / storage motion

| Primitive | Handler | Real today | lab_automation |
|-----------|---------|------------|----------------|
| `MOVE_COMPONENT` | `move_component` | ⚠️ needs `component_map` | `place_component_wo_home_specific_xy_cloudlab` |
| `STORE_COMPONENT` | `store_component` | ⚠️ | via move + storage commit |
| `PLACE_FROM_STORAGE` | `place_from_storage` | ⚠️ | move hook |
| `AFFIRM_PLACED_AT_CURRENT` | `affirm_placed_at_current` | ⚠️ | scan-based |
| `REPACK_STORAGE` / `RECENTER_IN_STORAGE` | same | ⚠️ | move variants |

### 3.3 In-air session

| Primitive | Handler | Real today | lab_automation |
|-----------|---------|------------|----------------|
| `PICK_COMPONENT` | `pick_component` | ⚠️ | `pick_component_cloudlab` |
| `HOVER` | `hover_component` | ⚠️ blocking | `hover_component_cloudlab` |
| `PLACE_FROM_HOVER` | `place_from_hover` | ⚠️ | `place_from_hover_cloudlab` |
| `SCAN_ROTATE_IN_PLACE` | `scan_rotate_in_place` | ⚠️ atomic sweep | `scan_rotate_*_cloudlab` |
| `CONFIRM_HOLDING_TAG` | `confirm_holding_tag` | ✅ state | `get_gripper_status` at boot |

### 3.4 TeleOp + live feed (the gap)

| Primitive | HTTP route | Handler | Real motion today | Needed |
|-----------|------------|---------|-------------------|--------|
| `START_TELEOP` | `POST …/teleop/start` | `start_teleop` | No-op prepare | **Setup macro + servo mode + WS open** |
| `END_TELEOP` | `POST …/teleop/end` | `end_teleop` | Sim pose commit only | **Teardown + position mode + WS close** |
| `TELEOP_GOTO` | `POST …/telemetry/goto` | `teleop_goto` | **`TeleopLivePoseStore` sim only** | **Queue → servo stream** |
| `TELEOP_JOG` | `POST …/telemetry/jog` | `teleop_jog` → goto | alias | same sink |
| `START_LIVE_FEED` | `POST …/live-feed/start` | `start_live_feed` | ⚠️ STREAM_ON | teleop preview profile |
| `END_LIVE_FEED` | `POST …/live-feed/end` | `end_live_feed` | STREAM_OFF | |

### 3.5 Explicitly real-disabled (by design)

| Primitive | Behavior |
|-----------|----------|
| `ADD_COMPONENT` | Real refuses — scan-only inventory |
| `REMOVE` | no-op on real |
| `SCAN` | dispatch stub (legacy) |

**Boot blocker (real communicator):** init code for `component_map`, scan, recorders may not run until `RealLabCommunicator.__init__` is repaired — without this, *all* rows above are ⚠️.

---

## 4. TeleOp architecture — three channels, not one

Treat TeleOp as **three transport classes**:

```
┌─────────────────────────────────────────────────────────────────────────┐
│  A. SESSION (HTTP POST, low rate, reliable)                              │
│     START_TELEOP  →  prepare + enter live mode + open streams            │
│     END_TELEOP    →  teardown + commit tunables + close streams          │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│  B. COMMAND (duplex WebSocket OR UDP-like side channel, 10–50 Hz)         │
│     client → server: { "type":"goto", "target_pose":{...}, "seq": N }    │
│     server → client: { "type":"ack", "seq": N, "accepted": true }        │
│     (optional coalesce: only latest target kept in queue)                  │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│  C. TELEMETRY (server-push WebSocket, 20–50 Hz)                          │
│     server → client: { "type":"pose", "ts_ms": ..., "pose": { rz: ... }} │
│     (optional: { "type":"jpeg", "cam_id": 1, "len": ... } binary frame)  │
└─────────────────────────────────────────────────────────────────────────┘
```

### 4.1 Why not HTTP for B and C?

| Approach | 20 Hz cost | Verdict |
|----------|------------|---------|
| `GET /telemetry/live-pose` | New TCP + HTTP request/response per tick; FastAPI + browser overhead | ❌ Replace |
| Long-poll GET | Better but still request-per-tick latency | ⚠️ transitional only |
| **WebSocket push** | One connection; server writes when ready | ✅ Preferred |
| SSE (Server-Sent Events) | One-way push; simpler than WS | ✅ OK for pose-only |
| Shared memory / IPC | Fastest inside one machine | ✅ Inside lab_automation; cloud-labs still needs WS to browser |

**Mock must use the same WebSocket contract** with a **software pose integrator** instead of encoder reads — otherwise frontend diverges.

### 4.2 What stays HTTP?

- `POST /teleop/start`, `POST /teleop/end` — transactional, audit-friendly  
- `POST /telemetry/goto` — **deprecated for held/streaming TeleOp**; keep for compatibility / non-streaming clients until WS client ships  
- `GET /api/lab-state` — 1–2 Hz background poll (unchanged)  

---

## 5. Motion engineering — xArm TeleOp modes

### 5.1 Current (too slow for UI)

```python
# lab_automation/drivers/xarm_driver.py (today)
self.arm.set_mode(0)          # Position control
self.arm.set_position_aa(..., wait=True)  # 500 ms – 2 s per move
```

Every `TELEOP_GOTO` through this path **must wait for settle** → unusable for nudging.

### 5.2 Target (live nudging)

**On `START_TELEOP` (after setup macro completes):**

1. Verify workspace / estop / gripper state  
2. `set_mode(1)` — **servo / cartesian stream mode** (verify against installed xArm SDK docs)  
3. Start **motion worker thread** reading latest target at fixed dt (e.g. 50 Hz)  
4. Start **pose publisher thread** reading encoders at same or higher rate into lock-protected buffer  

**During session:**

- UI sends targets on **WebSocket channel B** (or internal queue from WS handler)  
- Worker **coalesces** to latest target (drop stale frames — absolute targets are self-healing)  
- Each tick: `set_servo_cartesian(...)` or SDK equivalent **without** waiting for full settle  
- Pose publisher copies `{x,y,z,rotation}` → cloud-labs live buffer → WebSocket **channel C**

**On `END_TELEOP`:**

1. Stop worker (drain queue)  
2. Optional: hold final pose briefly  
3. `set_mode(0)` — return to safe position mode for macros  
4. Run teardown (open gripper if table-rotation session)  
5. Commit tunables (already in cloud-labs `commit_teleop_session_pose`)

### 5.3 Safety requirements (non-negotiable)

- **Session mutex:** while TeleOp active, refuse `PICK`, `MOVE_COMPONENT`, `OPTIMIZE` at orchestrator  
- **Speed/clamp limits** in servo loop (max Δ per tick)  
- **Watchdog:** no WS command for N seconds → hold position or auto `END_TELEOP`  
- **E-stop hook** clears mode and queue  
- **Table Rz setup:** transient grasp — define max grip force / slip detection (future)

### 5.4 Table Rz vs held pose3d (different setup macros)

| Mode | `prepare_teleop` (lab_automation) | Live control | `end_teleop` teardown |
|------|-------------------------------------|--------------|------------------------|
| **Rz (on table)** | Approach → descend → **close gripper** (transient hold) | Wrist θ only (XY/Z locked) | Open → retract |
| **pose3d (held)** | Verify `is_physically_holding` | Full XYZ + yaw | Stay holding or optional place |

Reuse pieces of `scan_rotate_placed_on_table` for **setup** only — not the full atomic sweep.

---

## 6. lab_automation — new modules (explicit API)

Do **not** overload `hover_component_cloudlab` for 20 Hz — it is the wrong abstraction.

### 6.1 Suggested surface (`managers/live_control.py`)

```python
class LiveControlSession:
    """One active session per OpticalExperiment."""

    def enter_table_rotation(self, component, *, safe_z=None) -> None: ...
    def enter_held_cartesian(self, component) -> None: ...

    def set_target_pose(self, *, rotation=None, x=None, y=None, z=None) -> None:
        """Non-blocking; coalesced by worker."""

    def get_live_pose(self) -> dict: ...
    def exit(self) -> None: ...
```

### 6.2 `XArmDriver` extensions

```python
def enter_servo_cartesian_mode(self) -> None: ...
def exit_servo_cartesian_mode(self) -> None: ...
def stream_cartesian_target(self, x, y, z, r, p, y) -> None:  # non-blocking
def read_cartesian_pose(self) -> tuple: ...  # fast encoder read
def stop_motion(self) -> None: ...
```

### 6.3 Threads (inside lab_automation process)

| Thread | Rate | Work |
|--------|------|------|
| `LiveControlWorker` | 50 Hz | Pop latest target → stream to arm |
| `LivePosePublisher` | 50 Hz | `read_cartesian_pose` → shared buffer |
| (existing) Recorder stream | 30–60 Hz | JPEG preview encode |

cloud-labs **reads** the pose buffer via a thin C extension / shared memory / or callback registered at `START_TELEOP` — avoid `asyncio.to_thread` per tick.

---

## 7. cloud-labs changes

### 7.1 FastAPI WebSocket routes (new)

```text
WS /api/components/{tag_id}/teleop/session
```

**Client → server messages:**

```json
{ "type": "goto", "seq": 42, "target_pose": { "rotation": 45.0 }, "speed": { "angular_deg_s": 15 } }
{ "type": "ping" }
```

**Server → client messages:**

```json
{ "type": "pose", "ts_ms": 1710000000123, "pose": { "x": 1, "y": 2, "z": 40, "rotation": 45.12 } }
{ "type": "session", "phase": "executing" | "idle" | "ready" }
{ "type": "error", "message": "..." }
```

On connect: verify `telemetry.teleop.ready`; attach to pose ring buffer.

### 7.2 `TeleopLivePoseStore` split

| Backend | Pose source | Goto effect |
|---------|-------------|-------------|
| **Mock** | Software integrator toward target | Unchanged logic, fed via WS |
| **Real** | `LivePosePublisher` buffer | WS goto → lab_automation queue |

Remove real reliance on `_motion_loop` simulation.

### 7.3 `RealLabCommunicator` hooks

| Hook | Responsibility |
|------|----------------|
| `_primitive_prepare_teleop` | Call `LiveControlSession.enter_*`; start WS pose fanout |
| `_teleop_enqueue_goto` | WS + legacy HTTP → same queue |
| `_primitive_finalize_teleop` | `LiveControlSession.exit()`; restore mode 0 |

### 7.4 Catalog / telemetry descriptor update

`telemetry.teleop.live_pose` widget descriptor should gain:

```json
{
  "widget": "LivePosePoll",
  "transport": "websocket",
  "url": "/api/components/{tag_id}/teleop/session",
  "default_hz": 50
}
```

Keep `default_fps` + HTTP URL as fallback for old clients.

---

## 8. Camera engineering — capture vs live stream

### 8.1 Two different pipelines (already in lab_automation)

| | **RECORD_MEASURABLES / CAP** | **Live feed / STREAM** |
|---|------------------------------|-------------------------|
| **Goal** | Best quality single frame for analysis | Lowest latency preview |
| **Exposure** | Per-shot (seconds possible) | Fixed video exposure |
| **Resolution** | Full sensor → PNG file | Scaled BGR → JPEG buffer |
| **Path** | TCP `CAP` → disk write → cloud-labs reads PNG | Background loop → `_latest_jpeg` → `GET_JPEG` |
| **cloud-labs** | `capture_table_cam()` | MJPEG route → `fetch_preview_jpeg_cloudlab` |

File: `lab_automation/scripts/recorder_cam_laser_align_cloudlab.py`

- Stream loop: `grab_latest_frame` with `_STREAM_DRAIN_FRAMES=6`, `_publish_preview_frame(scale, jpeg_quality)`  
- Default CLI: `--scale 0.75`, `--jpeg-quality 72`  
- cloud-labs spawns recorder with `table_cam_preview.json` (same defaults)

### 8.2 TeleOp preview profile (recommended)

During **`START_LIVE_FEED`** while TeleOp active, spawn or reconfigure with:

| Parameter | Normal preview | **TeleOp preview** |
|-----------|----------------|---------------------|
| `scale` | 0.75 | **0.25** |
| `jpeg_quality` | 72 | **50** |
| `_STREAM_DRAIN_FRAMES` | 6 | **2** (fresher frame, less CPU) |
| Target loop sleep | 20 ms | **10–15 ms** |
| Exposure | 0.1 s | **≤ 0.05 s** if SNR allows |

**Do not** run full CAP during TeleOp — ever.

### 8.3 Remaining slowness (why it still feels laggy)

1. **cloud-labs MJPEG** calls `fetch_preview_jpeg_cloudlab(timeout=0.45)` per frame — 450 ms budget!  
2. **HTTP MJPEG** adds another layer vs pushing JPEG over the TeleOp WebSocket.  
3. **Double poll:** browser MJPEG + live-pose GET — contends on server.

**Target architecture for TeleOp video:**

- Option A: **Binary JPEG frames on same TeleOp WebSocket** (multiplexed)  
- Option B: **Dedicated WS** `/teleop/preview/{cam_id}` with binary frames  
- Option C: **Shared memory** from recorder → cloud-labs → WS (same machine only)

### 8.4 Table cam is not a component (lab_automation)

- Inventory: OpenCV **ceiling** USB 0/5  
- Alignment: OpenCV **gripper** USB 1/2  
- Industrial: **TCP recorder** 9999/10000  

Catalog `cam_gripper_*` → recorder TCP — **not** USB. Document `properties.cam_backend` in catalog:

```json
"properties": { "cam_backend": "recorder_tcp", "recorder_cam_id": 1 }
```

---

## 9. Frontend changes

### 9.1 Replace `teleop-live-pose.js` poll with WebSocket client

File today: `frontend/js/api/teleop-live-pose.js` — `setInterval` + `fetch(GET)`.

New:

- `teleop-session-ws.js` — connect on `START_TELEOP`, disconnect on `END`  
- Push updates → `store.teleopLivePose[tagId]` (same store keys — **current_pose** naming preserved)  
- Goto: send on WS when session active; fallback to HTTP `POST …/goto` if WS down  

### 9.2 Widget `LivePosePoll`

Repaint from store (unchanged); transport-agnostic.

### 9.3 TeleOp widgets (`TeleopRz`, `TeleopPose3d`)

- Nudge buttons → WS goto messages (coalesced client-side max 20 Hz)  
- TARGET row updates immediately (optimistic); CURRENT from WS pose  

### 9.4 Mock backend

`MockLabCommunicator` implements same WebSocket routes with software integrator — **no** special-case frontend for mock.

---

## 10. End-to-end sequence (table Rz, target state)

```text
1. UI: POST START_TELEOP
   cloud-labs: commit_teleop_start → prepare_teleop
   lab_automation: enter_table_rotation (approach, grasp)
   cloud-labs: commit_teleop_ready
   lab_automation: set_mode(1), start worker + publisher
   UI: WebSocket connect

2. Loop (20–50 Hz):
   UI: WS goto { rotation: θ_target }
   lab_automation worker: stream wrist target
   lab_automation publisher: pose buffer
   cloud-labs: WS push { pose.rotation: θ_current }
   UI: canvas LIVE + panel CURRENT

3. Optional parallel:
   START_LIVE_FEED → recorder STREAM_ON (teleop profile)
   UI: MJPEG or WS JPEG binary — spot tracking

4. UI: POST END_TELEOP
   lab_automation: exit (open, retract), set_mode(0)
   cloud-labs: commit_teleop_session_pose → tunables
   UI: WebSocket close
   GET lab-state: nominal rotation persisted
```

---

## 11. Implementation phases (ordered)

### Phase 0 — Boot + primitive audit (cloud-labs)

- [ ] Fix `RealLabCommunicator.__init__`  
- [ ] Generate **primitive coverage test** from `PRIMITIVE_REGISTRY` vs real hooks  
- [ ] v1 state shape + lab frame on scan  

**Exit:** `component_map` populated; every handler callable without ImportError.

### Phase 1 — WebSocket telemetry (cloud-labs + frontend + mock)

- [ ] `WS /teleop/session` with pose push @ 50 Hz (mock integrator)  
- [ ] Frontend switches from HTTP poll  
- [ ] Deprecate hot-path GET live-pose (keep route for debug)  

**Exit:** Canvas LIVE smooth in mock without HTTP poll storm.

### Phase 2 — lab_automation `LiveControlSession` + xArm servo mode

- [ ] `XArmDriver` mode 0 ↔ 1 lifecycle  
- [ ] Worker + publisher threads  
- [ ] Table rotation enter/exit (grasp hold)  
- [ ] Held cartesian enter (verify holding)  

**Exit:** Bench test: WS goto → arm moves without 500 ms settle per frame.

### Phase 3 — Bridge real TeleOp (cloud-labs)

- [ ] `_primitive_prepare_teleop` / finalize → lab session  
- [ ] WS goto → lab queue (HTTP goto → same queue for compat)  
- [ ] Pose buffer read from lab publisher  

**Exit:** Full TeleOp Rz on hardware with tunable commit on END.

### Phase 4 — Camera teleop profile

- [ ] `table_cam_preview_teleop.json` (scale 0.25, quality 50)  
- [ ] `START_LIVE_FEED` selects profile when TeleOp active  
- [ ] Reduce MJPEG client timeout; optional WS JPEG  

**Exit:** Spot visibly tracks rotation with &lt; 100 ms perceived lag.

### Phase 5 — Hardening

- [ ] Session mutex, watchdog, estop  
- [ ] Gripper slip / force (when hardware supports)  
- [ ] Catalog `cam_backend` cleanup  
- [ ] `OpticalExperiment(catalog=...)`  

---

## 12. Acceptance metrics

| Metric | Target |
|--------|--------|
| Pose WS latency (server stamp → UI paint) | p95 &lt; 80 ms on LAN |
| Goto command acceptance | p95 &lt; 30 ms (queue insert) |
| Arm following UI (Rz) | no per-frame 500 ms settle |
| TCP HTTP requests during TeleOp | **0** pose polls; only WS + occasional lab-state |
| JPEG preview size @ teleop profile | &lt; 40 KB typical |
| END teleop | `nominal_pose.rotation` matches final live pose ± ε |

---

## 13. Risks and open decisions

1. **xArm servo mode API** — confirm exact SDK calls (`set_mode(1)`, cartesian servo packet format) on lab firmware version.  
2. **Grasp during table Rz** — acceptable to lift part slightly vs force-limited contact?  
3. **Duplex WS vs two WS** — one socket for pose+goto vs split (simpler debugging with two).  
4. **HTTP goto during migration** — keep coalescing queue shared with WS.  
5. **Motorized mounts** — Rz TeleOp may be `MOVE_MOTOR` stream, not arm servo (catalog `teleop.mode` gates backend).  
6. **Multi-user** — single TeleOp lease already enforced; WS must reject second connector.

---

## 14. File index (implementation touch list)

### cloud-labs

| File | Change |
|------|--------|
| `backend/main.py` | WebSocket route; teleop session |
| `backend/lab_model/orchestration/teleop.py` | Real vs mock pose source |
| `backend/lab_model/orchestration/teleop_live_pose.py` | Mock-only sim; remove from real hot path |
| `backend/lab_communicator/real/communicator.py` | prepare/finalize teleop; lab session bridge |
| `backend/lab_communicator/real/primitives.py` | Primitive audit; no blocking goto |
| `backend/lab_communicator/real/video.py` | Teleop preview profile; lower timeouts |
| `frontend/js/api/teleop-session-ws.js` | **NEW** |
| `frontend/js/api/teleop-live-pose.js` | Fallback / deprecated poll |
| `frontend/js/teleop-session.js` | Start/stop WS with poll |

### lab_automation

| File | Change |
|------|--------|
| `drivers/xarm_driver.py` | Servo mode + stream API |
| `managers/live_control.py` | **NEW** session + threads |
| `managers/experiment_manager.py` | Facade methods |
| `managers/robot_manager.py` | Setup/teardown for table rotation |
| `scripts/recorder_cam_laser_align_cloudlab.py` | Teleop profile args |
| `managers/recorder_capture_helpers_cloudlab.py` | Faster preview fetch |

---

## 15. Bottom line (TeleOp + transport)

- **Registry:** cloud-labs dispatch is not the bottleneck — **real + lab_automation must implement the handler chain** for every new primitive name.  
- **Speed:** TeleOp is a **real-time control problem** — servo mode, worker threads, server-push telemetry, and a **separate camera preview profile**.  
- **Transport:** **WebSocket push for pose** (and likely preview); HTTP only for session edges.  
- **Mock:** Must speak the same WebSocket protocol so the UI is not forked.

---

# Part II — Unified components, catalog, lab_automation reorganization

---

## 16. Design principle: one component language, two repos

```
┌─────────────────────────────────────────────────────────────────────────┐
│  CATALOG (component_library.json) — single source of truth               │
│    tag_id, type, capabilities.{statecontrol, telemetry, primitives}      │
│    properties.hardware_binding — how lab_automation attaches drivers     │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │ boot: merged_catalog + active_catalog
        ┌───────────────────────┴───────────────────────┐
        │                                               │
┌───────▼────────────────────────┐    ┌─────────────────▼────────────────┐
│  cloud-labs lab_state          │    │  lab_automation ComponentRegistry │
│  components[tag_id]            │    │  tag_id → LabComponent instance     │
│    statecontrol (tunables/     │    │    ManipulableOptic | Camera | Laser│
│     measurables)               │    │    bind_driver() / read() / stream()│
│    telemetry (teleop/live)     │    │                                     │
└────────────────────────────────┘    └─────────────────────────────────────┘
```

**Corollaries:**

1. **No shadow naming** — stop inferring hardware from `cam_gripper_1` → TCP 1 while USB gripper uses the same index. Use explicit `properties.hardware_binding`.  
2. **Manipulable vs fixed** — expressed by **primitive list**, not a boolean flag (`MOVE_COMPONENT` absent = fixed). Already in `universal_component_architecture.md` §13.3.  
3. **Measurables without pose** — cameras must **not** carry `measurables.pose`; only `camera_image`, beam metrics, etc.  
4. **Anything tunable is a component** — including lasers, motor controllers, environmental sensors — even if the robot never touches them.

---

## 17. Naming convention (fix `id` vs `tag_id` vs display name)

Today catalog rows mix roles:

```json
"tag_9": {
  "id": "nd_filter",
  "name": "ND Filter",
  "tag_id": "tag_9"
}
```

**Canonical rules (enforce in schema validation):**

| Field | Role | Example |
|-------|------|---------|
| **`tag_id`** | Primary key; URL path; lab_state key; lab_automation registry key | `tag_99`, `tag_22` |
| **`id`** | Stable slug (snake_case); logs, file prefixes | `cam_table_top`, `nd_filter` |
| **`name`** | Human UI label | `Table-top camera` |
| **`type`** | Closed enum — drives factory subclass | `OPTICAL_CAMERA`, `MANIPULABLE_OPTIC`, `LASER_SOURCE`, … |

**Rename misleading rows (real + mock bundles):**

| Current | Problem | Target |
|---------|---------|--------|
| `tag_22` / `id: cam_gripper_1` | Name says gripper USB; backend is **recorder TCP 1** | `id: cam_table_beam_1`, binding `recorder_tcp:1` |
| `tag_21` / `cam_gripper_2` | Same | `id: cam_table_beam_2`, binding `recorder_tcp:2` |
| (missing on real) | No table-top overhead component | **`tag_99`** / `cam_table_top` (sync mock → real) |
| `ceiling_cam_1` (doc only) | Example used slug without `tag_*` | **`tag_90`** / `id: cam_ceiling_1` |

**Do not** use bare slugs like `ceiling_cam_1` as lab_state keys — always `tag_XX`.

---

## 18. Component type taxonomy (catalog `type` enum)

Extend closed enum (cloud-labs `catalog/schema.py` + lab_automation factory):

| `type` | Subclass (lab_automation) | Has `nominal_pose`? | Robot? | Example primitives |
|--------|---------------------------|---------------------|--------|-------------------|
| `MANIPULABLE_OPTIC` | `ManipulableOptic` | ✅ | pick/hover/place | `MOVE_COMPONENT`, `PICK`, TeleOp, motors |
| `OPTICAL_CAMERA` | `CameraComponent` | ❌ (fixed) | no | `RECORD_MEASURABLES`, `SET_EXPOSURE`, `START_LIVE_FEED` |
| `CEILING_CAMERA` | `CeilingCameraComponent` | ❌ | no (used by scan) | `RECORD_MEASURABLES`, inventory scan hook |
| `LASER_SOURCE` | `LaserComponent` | ❌ | no | `SET_EXPOSURE` or custom `SET_LASER_POWER`, `RECORD_MEASURABLES` |
| `MOTOR_STAGE` | `MotorStageComponent` | optional | via stepper only | `MOVE_MOTOR`, `SET_MOTOR_SETPOINT` |
| `LAB_FIXTURE` | `FixtureComponent` | ❌ | no | read-only or empty |

**Note:** `OPTICAL_FILTER`, `MIRROR`, etc. can remain as fine-grained `type` values that map to **`ManipulableOptic`** factory, or collapse to `MANIPULABLE_OPTIC` with `properties.optic_kind`.

---

## 19. `properties.hardware_binding` (replace implicit hacks)

Every non-trivial component declares how lab_automation attaches:

```json
"properties": {
  "hardware_binding": {
    "backend": "recorder_tcp",
    "recorder_cam_id": 1,
    "recorder_port": 9999,
    "preview_profile": "teleop"
  }
}
```

```json
"properties": {
  "hardware_binding": {
    "backend": "opencv_usb",
    "device_index": 0,
    "width": 3840,
    "height": 2160,
    "backend_api": "msmf"
  }
}
```

```json
"properties": {
  "hardware_binding": {
    "backend": "wifi_stepper",
    "controller_key": "wifi_stepper2",
    "motor_ids": [1, 3]
  }
}
```

```json
"properties": {
  "hardware_binding": {
    "backend": "laser_serial",
    "port": "COM4",
    "protocol": "v1"
  }
}
```

**cloud-labs** `resolve_cam_id_for_tag()` becomes **`resolve_hardware_binding(row)`** returning a typed struct; old `cam_gripper_N` convention supported only as **migration shim** (log deprecation warning).

**Stream backend** (`resolve_telemetry_stream_backend`) reads binding:

| `backend` | Live feed path | Still capture path |
|-----------|----------------|-------------------|
| `recorder_tcp` | WS/TCP JPEG stream, teleop profile | `CAP` → PNG (slow) |
| `opencv_usb` | in-process MJPEG or WS relay | `CameraDriver.capture()` |
| `overhead` | alias for ceiling OpenCV | same |

---

## 20. Target catalog: cameras (mock + real must match)

### 20.1 Table-top beam camera (industrial recorder) — `tag_22`, `tag_21`

```json
"tag_22": {
  "id": "cam_table_beam_1",
  "type": "OPTICAL_CAMERA",
  "name": "Table beam camera 1",
  "tag_id": "tag_22",
  "properties": {
    "hardware_binding": {
      "backend": "recorder_tcp",
      "recorder_cam_id": 1,
      "recorder_port": 9999
    }
  },
  "capabilities": {
    "statecontrol": {
      "tunables": {
        "exposure_time_ms": { "widget": "FloatRange", "min": 10, "max": 1000, "default": 200, "unit": "ms" }
      },
      "measurables": {
        "camera_image": { "widget": "ImageViewer", "format": "png" }
      }
    },
    "telemetry": {
      "live_feed": {
        "stream": { "widget": "MJPEGViewer", "url": "/api/components/{tag_id}/telemetry/stream" }
      }
    },
    "primitives": ["SET_EXPOSURE", "RECORD_MEASURABLES", "START_LIVE_FEED", "END_LIVE_FEED"]
  }
}
```

Remove **`nominal_pose`** from fixed cameras. Remove spurious **`PICK` / TeleOp** from camera rows unless you truly teleop a camera (you don't).

### 20.2 Table-top overview / overhead — `tag_99` (**add to real bundle**)

Mock already has this; **real `component_library.json` must gain the same row**:

```json
"tag_99": {
  "id": "cam_table_top",
  "type": "CEILING_CAMERA",
  "name": "Table overview camera",
  "tag_id": "tag_99",
  "properties": {
    "hardware_binding": {
      "backend": "opencv_usb",
      "device_index": 0,
      "role": "table_overview"
    }
  },
  "capabilities": {
    "statecontrol": {
      "tunables": {
        "exposure_time_ms": { "widget": "FloatRange", "min": 10, "max": 500, "default": 50, "unit": "ms" }
      },
      "measurables": {
        "camera_image": { "widget": "ImageViewer", "format": "png" }
      }
    },
    "telemetry": {
      "live_feed": {
        "stream": { "widget": "MJPEGViewer", "url": "/api/components/{tag_id}/telemetry/stream" }
      }
    },
    "primitives": ["SET_EXPOSURE", "RECORD_MEASURABLES", "START_LIVE_FEED", "END_LIVE_FEED"]
  }
}
```

**lab_automation today:** `ceiling_cam1` on USB 0 — this row **claims** that device explicitly.

### 20.3 Inventory ceiling stereo — `tag_90`, `tag_91` (new; not in UI sidebar today)

Used **only** by scan / ArUco — may be hidden from operator panel or shown as read-only fixtures:

```json
"tag_90": {
  "id": "cam_ceiling_1",
  "type": "CEILING_CAMERA",
  "tag_id": "tag_90",
  "properties": {
    "hardware_binding": { "backend": "opencv_usb", "device_index": 0, "role": "inventory_stereo_left" }
  },
  "capabilities": {
    "measurables": { "camera_image": { "widget": "ImageViewer" } },
    "primitives": ["RECORD_MEASURABLES"]
  }
}
```

Scan pipeline (`scan_inventory_cloudlab`) resolves stereo pair from **`role`**, not hard-coded `CAM_CEILING_1` constants.

### 20.4 Alignment gripper cameras (optional split)

If USB gripper cameras (IDs 1, 2) remain used for RealSense pick refinement — either:

- **Option A:** Keep as **internal** drivers on `AssemblyManager`, not catalog components (simplest short-term).  
- **Option B:** `tag_92` / `tag_93` as `OPTICAL_CAMERA` with `opencv_usb` + `role: gripper_alignment` (clean long-term).

Document choice in `lab_manifest.json` per deployment.

---

## 21. lab_automation — class hierarchy (target code layout)

### 21.1 Package structure (proposed)

```text
lab_automation/
  components/
    base.py              # LabComponent, ComponentRegistry
    manipulable.py         # ManipulableOptic (+ Mirror, Lens, Filter factories)
    cameras.py             # RecorderTableCamera, OpenCVCamera
    laser.py               # LaserComponent (stub → real driver)
    motors.py              # MotorStageComponent
  drivers/                 # unchanged role: raw hardware
  managers/
    experiment_manager.py  # thin facade; owns registry + robot
    robot_manager.py       # ManipulableOptic motion only
    live_control.py        # TeleOp sessions
    component_factory.py   # catalog → instances
  utils/
    cloudlab_contract.py   # Z transforms (unchanged ownership)
  scripts/
    recorder_cam_laser_align_cloudlab.py   # KEEP — spawned by binding
  tests/
    test_component_factory.py
    test_cloudlab_primitives_mock.py
```

### 21.2 Base class sketch

```python
class LabComponent(ABC):
    tag_id: str
    component_type: str
    catalog_row: dict

    @abstractmethod
    def bind(self, experiment: "OpticalExperiment") -> None: ...

    def read_measurables(self) -> dict: ...
    def apply_tunable(self, field: str, value: Any) -> None: ...


class ManipulableOptic(LabComponent):
    """Former OpticalComponent — table optics the arm moves."""
    marker_length: float
    current_location: Optional[Pose]  # robot frame
    inventory_location: Optional[Pose]
    is_held: bool
    is_placed: bool
    # pick/hover/place/scan_rotate → delegate AssemblyManager


class CameraComponent(LabComponent):
    """Fixed camera — no nominal_pose, no robot primitives."""
    @abstractmethod
    def capture_still(self) -> bytes: ...
    @abstractmethod
    def start_stream(self, profile: str = "default") -> None: ...
    @abstractmethod
    def read_preview_jpeg(self) -> bytes: ...


class LaserComponent(LabComponent):
    """Future: tunable output_power_mW, measurable power meter."""
    def set_power_mw(self, mw: float) -> None: ...
    def read_power_mw(self) -> float: ...
```

**`ComponentRegistry`:**

```python
class ComponentRegistry:
    def __init__(self, catalog: dict): ...
    def get(self, tag_id: str) -> LabComponent: ...
    def manipulables(self) -> Iterator[ManipulableOptic]: ...
    def cameras(self) -> Iterator[CameraComponent]: ...
```

Built in `OpticalExperiment.__init__(mock=, catalog=)` when `catalog=` kwarg lands.

### 21.3 What happens to `OpticalExperiment` fields

| Today | Target |
|-------|--------|
| `self.ceiling_cam1`, `self.camera_gripper_1`, … | **Removed** — accessed via `registry.get("tag_90")` |
| `self.component_map: Dict[str, OpticalComponent]` | `self.registry: ComponentRegistry` |
| `self.wifi_stepper1` | Either stay as shared drivers **or** motor components own references |
| `scan_components_cloudlab(components)` | `scan_components_cloudlab(registry.manipulables())` |

**Critical:** all **robot placement logic, coordinate transforms, holding session, clearance Z** stay in `AssemblyManager` + cloud-labs transforms — the refactor **moves ownership**, not the physics rules. Read `newprimitives.md`, `robot_manager.py` pick/place paths, and `coordinate_frames.py` before deleting code.

---

## 22. cloud-labs changes for unified components

### 22.1 Component library parity

| Task | Detail |
|------|--------|
| Sync **real** ↔ **mock** `component_library.json` | Add `tag_99`; fix camera names/bindings |
| Update **`active_catalog.json`** (real) | Include camera tags operator needs |
| Validate with **`platform.verify_registries()`** | Every widget/primitive in catalog exists in registries |
| Schema validator | Require `tag_id` key matches outer key; require `hardware_binding` for cameras |

### 22.2 Resolver refactor

Replace scattered logic in:

- `resolve_cam_id_for_tag`  
- `resolve_telemetry_stream_backend`  
- `real/video.py` special cases  
- `measurables/camera_image.py`  

With:

```python
@dataclass
class HardwareBinding:
    backend: str
    ...

def resolve_hardware_binding(catalog_row) -> HardwareBinding: ...
```

`RECORD_MEASURABLES` → binding → `CameraComponent.capture_still()` on lab side.  
`START_LIVE_FEED` → binding → stream profile (`default` vs `teleop`).

### 22.3 lab_state for fixed components

Fixed cameras **exist in `lab_state.components[tag_id]`** even if never “placed”:

```json
"tag_99": {
  "id": "tag_99",
  "type": "CEILING_CAMERA",
  "statecontrol": {
    "tunables": { "exposure_time_ms": 50.0 },
    "measurables": { "camera_image": null }
  },
  "telemetry": { "teleop": { "active": false, "ready": false } }
}
```

Boot script (`initialize_state` or cloud-labs seed) **inserts fixture rows** for all catalog tags with `presence: fixed` — not only scan-discovered manipulables.

### 22.4 UI

- Component viewer already driven by capabilities — fixed cameras appear in sidebar when in `active_catalog`.  
- Remove hard-coded `/api/video-feed` routes over time → per-tag `telemetry.live_feed.stream.url` only.  
- TeleOp live feed during rotation uses **`tag_22`** beam cam with **`teleop` preview profile**, not overhead 4K.

---

## 23. Example: adding a laser (proves extensibility)

### Catalog (`tag_50`)

```json
"tag_50": {
  "id": "pump_laser_1",
  "type": "LASER_SOURCE",
  "name": "Pump laser",
  "tag_id": "tag_50",
  "properties": {
    "hardware_binding": { "backend": "laser_eth", "host": "192.168.1.50", "channel": 1 },
    "wavelength_nm": 808
  },
  "capabilities": {
    "statecontrol": {
      "tunables": {
        "output_power_mw": { "widget": "FloatRange", "min": 0, "max": 2000, "unit": "mW" }
      },
      "measurables": {
        "output_power_mw": { "widget": "NumberBadge", "format": ".1f" }
      }
    },
    "primitives": ["APPLY_TUNABLES_PATCH", "RECORD_MEASURABLES"]
  }
}
```

### cloud-labs

- Register tunable plugin `output_power_mw` in `lab_model/tunables/`.  
- Register measurable plugin in `lab_model/measurables/`.  
- `APPLY_TUNABLES_PATCH` macro → `SET_LASER_POWER` (new primitive) or generic tunable apply.  
- `RealLabCommunicator._primitive_*` → `experiment.registry.get("tag_50").set_power_mw(...)`.

### lab_automation

```python
class LaserComponent(LabComponent):
    def apply_tunable(self, field, value):
        if field == "output_power_mw":
            self._driver.set_power(value)
    def read_measurables(self):
        return {"output_power_mw": self._driver.read_power()}
```

**No robot code touched.** Factory registers subclass by `type: LASER_SOURCE`.

---

## 24. lab_automation simplification — deletion manifest

Goal: **one cloudlab-specialized codebase** — not a general experiment framework with 2019 scripts.

### 24.1 KEEP (production cloudlab path)

| Path | Role |
|------|------|
| `managers/experiment_manager.py` | Facade (slim down) |
| `managers/robot_manager.py` | Arm + manipulable motion |
| `managers/vision_manager.py`, `calibration.py`, `visual_servo.py`, `alignment.py` | Scan + optimize support |
| `managers/recorder_capture_helpers_cloudlab.py` | Recorder client |
| `drivers/xarm_driver.py`, `camera_driver.py`, `wifi_stepper.py` | Hardware |
| `scripts/recorder_cam_laser_align_cloudlab.py` | Recorder subprocess |
| `utils/cloudlab_contract.py`, `clearance.py`, `angles.py`, `trajectory.py` | Contracts |
| `objects/optics.py` | → migrate to `components/manipulable.py` |
| `tests/test_cloudlab_primitives_mock.py`, `tests/test_clearance.py` | Regression |

### 24.2 ARCHIVE then DELETE (after grep confirms zero cloud-labs imports)

| Path | Reason |
|------|--------|
| `original_codes/*.py` | Superseded monoliths (~30k LOC) |
| `scripts/service_recorder.py` | Legacy monolith |
| `scripts/recorder_cam_laser_align_simplified.py` | Superseded by cloudlab script |
| `scripts/recorder_cam_laser_align_sandbox.py` | Dev sandbox — keep module import only if needed by cloudlab script |
| `scripts/stepper_controller_mine_and_check_w_cam.py` | One-off |
| `scripts/05_sim_build_cavity.py` | Simulation demo |
| `scripts/11_run_experiment_recorder_cam.py` | Pre-cloudlab runner |
| `managers/recorder_capture_helpers.py` | Legacy TCP protocol — migrate optimize strategies first |
| `drivers/recorder_camera_driver.py` | Legacy client |
| `managers/camera_recorder_manager.py` | Legacy launcher |

### 24.3 KEEP as dev-only (move to `devtools/`)

| Path | Role |
|------|------|
| `scripts/01_check_hardware.py` … `04_check_cameras.py` | Bench bring-up |
| `scripts/03_check_robot_motion.py` | Sanity ±Z |
| `scripts/SCRIPTS_GUIDE.md` | Operator docs |

### 24.4 Trim inside `experiment_manager.py`

Legacy placement methods (`place_component_and_rotate`, reconstruction sweeps, etc.) — **delete** once grep shows cloud-labs never calls them. Keep only:

- `*_cloudlab` entry points  
- `optimize_component` + strategy dispatch  
- `get_gripper_status`, Z intent helpers  

**Do this deletion only after** cloudlab primitive tests pass on hardware — the unused code still documents motion edge cases you may need to port into `AssemblyManager` comments.

---

## 25. Robot interaction rules (preserve while refactoring)

These **must not regress** — they are the “small logic” embedded across `robot_manager.py`, `scan.py`, and cloud-labs transforms:

| Rule | Owner |
|------|-------|
| Lab frame ↔ robot frame XY/yaw | **cloud-labs only** (`coordinate_frames.py`) |
| `Pose` in lab_automation is **robot table frame** | `objects/base.py` |
| Holding session (`is_physically_holding`, tag match) | lab_automation + cloud-labs JSON `holding` |
| Pick uses RealSense refine when not mock | `AssemblyManager.pick_grasp_from_inventory` |
| Placed scan-rotate ≠ user holding session | `_scan_placed_atomic_active` vs `_holding_tag_id` |
| Scan sets `is_placed=True` on cloudlab inventory path | `scan_inventory_cloudlab` |
| Storage intent sidecar | cloud-labs `stored_intent.json` + scan reconcile |
| Golden Rule: null measurables on motion/teleop start | cloud-labs orchestrator |
| Safe Z / retract | `utils/clearance.py` |

**Refactor strategy:** extract **`ManipulableMotionService`** from `AssemblyManager` first (copy-paste move, no logic change), **then** wire `ComponentRegistry` — never both at once on a live bench.

---

## 26. Integrated implementation phases (TeleOp + components)

Phases interleave — order matters.

| Phase | Scope | Exit criterion |
|-------|--------|----------------|
| **0** | Fix real communicator boot | `component_map` / scan works |
| **A** | Catalog parity: `tag_99` on real, rename cameras, `hardware_binding` | mock ≈ real library; validator green |
| **B** | cloud-labs `resolve_hardware_binding` | No `cam_gripper_N` hack in new code |
| **C** | lab_automation `ComponentRegistry` + `CameraComponent` | Recorders + ceiling cams as components |
| **D** | Fixture rows in lab_state at boot | `GET …/tag_99/tunables` works on real |
| **1** | WebSocket pose (Part I) | Mock + real protocol match |
| **E** | Migrate manipulables to `ManipulableOptic` in registry | Scan/pick unchanged behavior |
| **2** | Servo TeleOp + live_control (Part I) | Hardware Rz nudge |
| **F** | Teleop camera profile on `tag_22` | Spot tracks rotation &lt; 100 ms perceived |
| **G** | Delete legacy manifest §24.2 | CI + hardware smoke pass |
| **H** | `OpticalExperiment(catalog=)` + optional `LaserComponent` stub | CLOUDLAB_CONTRACT satisfied |

---

## 27. Acceptance checklist (components + speed)

- [ ] Every catalog tag in `active_catalog` has `lab_state.components[tag_id]` after boot.  
- [ ] `tag_99` on **real** matches mock; overhead stream via `/api/components/tag_99/telemetry/stream`.  
- [ ] `tag_22` still capture uses **CAP** (slow); live TeleOp uses **stream profile** (fast).  
- [ ] `resolve_hardware_binding` unit tests for all camera rows.  
- [ ] lab_automation registry returns same tag_ids cloud-labs uses in URLs.  
- [ ] Adding laser catalog row + tunable plugin works without editing `video.py`.  
- [ ] TeleOp pose via WebSocket p95 &lt; 80 ms on LAN.  
- [ ] No HTTP live-pose poll during active TeleOp session.  
- [ ] legacy `original_codes/` removed from default branch.  

---

## 28. Bottom line (complete plan)

This is **not** only a TeleOp patch. It is a **paired refactor**:

1. **cloud-labs** — complete primitive wiring, WebSocket telemetry, catalog parity (`tag_99`, honest camera bindings).  
2. **lab_automation** — **`LabComponent` hierarchy**, catalog-driven registry, servo live control, delete legacy experiment scripts.  
3. **Both** — same `tag_id` vocabulary; tunables/measurables/primitives define what exists; hardware_binding defines how to reach it.

The table-top camera **is already a component in mock** (`tag_99`) but **missing on real** and **not a component in lab_automation** (`ceiling_cam1` field). Fixing that triplet is the template for every future instrument — including lasers.

