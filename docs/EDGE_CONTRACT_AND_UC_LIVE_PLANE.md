# Edge contract, UC language, and the live plane

**Status:** design + **Phase 0 schemas landed** on `develop/caio-edge-contract`  
**Audience:** maintainers deciding how cloud-labs, Twin/SDK, and lab repos (e.g. robot-deathray / lab_automation) meet under one language  
**Last updated:** 2026-07-16  

**Machine-readable contract:** [`schemas/edge_contract/v1/`](../schemas/edge_contract/v1/README.md)  
**Related:** [`mock_backend/README.md`](../mock_backend/README.md), Wiki Learn → Primitives / Kernels / OPU, [`EDGE_UC_MIGRATION_ROADMAP.md`](./EDGE_UC_MIGRATION_ROADMAP.md)

---

## 0. Non-negotiable principle

**Cloud Labs owns one language.** Everything that mutates or observes the lab must be expressible in that language:

| Concept | Role |
|---------|------|
| **Primitives** | The only verbs (see Wiki inventory / `lab_model.language.primitives`) |
| **Tunables** | Commanded DOFs |
| **Measurables** | Observations (analysis tensors / scalars); wire formats are *of* a measurable |
| **Telemetry capabilities** | Catalog-declared live channels for a tag (teleop widgets, live_feed) — **armed by primitives**, not free-form APIs |
| **Kernels** | Measurement artifacts; inputs to `EVAL_KERNEL` / `OPTIMIZE`, not verbs |
| **Configuration / VC** | ControlManager on the coordinator; edge only executes reconcile primitives |

There is **no** first-class `get_table_top_feed`, `get_video_stream`, or lab_automation-only helper in the product API. If Twin needs a live picture of `tag_22`, that is:

1. `START_LIVE_FEED` on that camera tag (primitive), then  
2. consume the **wire representation** of measurable `camera_image` (or a catalog-declared live_feed channel bound to that measurable).

If a fast path cannot be named in UC, **we change UC or we refuse the path** — we do not invent a side door for speed. Speed is achieved by **placement and transport of UC objects**, not by abandoning UC.

---

## 1. What we have today (honest snapshot)

### Strengths

- Concise primitive inventory including TeleOp + live feed (`START_TELEOP`, `TELEOP_JOG`, `START_LIVE_FEED`, …).
- Measurable analysis contract for cameras: **`bgr_hwc_uint8`**; PNG/JPEG as wire/storage.
- Mock as a full teaching edge; real bridge to lab_automation / robot-deathray for motion, recorder TCP, `LiveControlSession` (~50 Hz).
- Optional edge agent path for `/api/command` and jobs; ensemble OPTIMIZE can run off-coordinator.
- Twin and SDK both speak primitives in principle.

### Fractures (why redesign is needed)

1. **Communicator ownership** — `RealLabCommunicator` imports lab_automation, owns TF/math, scrambles on MoveIt churn.  
2. **Side doors** — Twin uses dedicated HTTP/WS/MJPEG routes and helpers (`get_video_feed_status`, capture methods) that are not clearly “the primitive + the measurable.”  
3. **Topology split** — teleop/live video often stay on the coordinator process while `/api/command` can proxy to edge → live paths and edge attachment disagree.  
4. **Lab-state overuse** — full JSON poll (~2 Hz) used as a general sync bus; wrong tool for laser-hunting feel.  
5. **Sync** — separate calls for “image” and “motors” without a guaranteed common epoch at the edge.  
6. **UI ≠ SDK power** — Twin has transports and endpoints the SDK does not mirror 1:1 (and vice versa for kernels).

Gemini’s three-tier transport idea is directionally right. It must be **rephrased entirely in UC**, which is what this document does.

---

## 2. The full picture (target)

```text
┌──────────────────────────────────────────────────────────────────────────┐
│ CLIENT (same language)                                                    │
│  Twin = HTML shell + cloudlabs client semantics + continuous subscriptions│
│  SDK  = same primitives / measurables / kernels / leases                  │
└─────────────────────────────┬────────────────────────────────────────────┘
                              │  northbound: coordinator API
                              │  (commands, jobs, VC, catalog, kernel registry)
┌─────────────────────────────▼────────────────────────────────────────────┐
│ CLOUD-LABS COORDINATOR (server)                                           │
│  · Validate primitives, leases, catalogs                                   │
│  · ControlManager (commits / stash / pins)                                 │
│  · Job queue; kernel artifact registry                                     │
│  · Backend registry → how to reach an edge (URL / process / version)     │
│  · Does NOT import OpticalExperiment / MoveIt / xArm                       │
└─────────────────────────────┬────────────────────────────────────────────┘
                              │  southbound: Edge Contract v1
                              │  (UC only: execute + capability-declared streams)
┌─────────────────────────────▼────────────────────────────────────────────┐
│ EDGE AGENT  (lives in LAB REPO, e.g. robot-deathray/cloudlabs_edge/)       │
│  · Implements Edge Contract                                                │
│  · Adapters → lab_automation / MoveIt / recorder / LiveControlSession      │
│  · Owns BGR RAM, kernel host, teleop 50 Hz loop, JPEG wire encode          │
│  · Atomic latch / epoch for coordinated observations                       │
└──────────────────────────────────────────────────────────────────────────┘
```

**Mock edge** stays the reference implementation (can live in cloud-labs or a tiny sibling package). **Real edge** is owned by the lab repo.

---

## 3. Cloud-labs server side (coordinator)

### Owns

- Product language schemas (primitives, catalogs, measurable decls, telemetry capability decls).  
- Session leases and job manager.  
- ControlManager (configuration history) — checkouts become sequences of primitives sent southbound.  
- Kernel **registry** (ids, digests, download URLs for artifacts).  
- Backend **registry**: `backend_id` → `{ contract_version, edge_base_url, capabilities_cache, … }`.  
- Thin **EdgeClient**: `execute(primitive, args)`, subscribe to channels listed in capabilities, `probe` as `EVAL_KERNEL` (not a parallel invention).

### Does not own

- Robot TF, MoveIt planning, xArm SDK, recorder MindVision process.  
- Long-lived BGR buffers.  
- “Smart” coordinate conversion for the bench (lab-frame in / lab-frame or confirmed pose out).

### Northbound API (clients)

Clients should see **one** command surface for mutations: the primitive command API (today `/api/command` or equivalent). Convenience routes in Twin may remain as **thin aliases** that only build a primitive body — never a second semantic universe.

Reads:

- `GET_TUNABLES` / `GET_MEASURABLES` (and/or coordinator lab-state snapshot as a **Tier C convenience** that is explicitly “stale-OK overview,” not live laser hunting).  
- Stream attach URLs **copied from edge capabilities** after arming primitives (coordinator may mint auth tokens / proxy, but the *meaning* is still the measurable/channel).

---

## 4. Client side: Twin and SDK are the same power

### Principle

> **Twin is an SDK client with a browser shell and default subscriptions.**  
> It must not gain verbs the SDK lacks, or lose verbs the SDK has (except UX: authoring a TorchScript file in a textbox is optional sugar, not a different language).

| Concern | SDK | Twin |
|---------|-----|------|
| Mutate | `connect` → primitives | Same primitives via command API |
| Observe once | `RECORD_MEASURABLES`, `EVAL_KERNEL`, measurable resolve | Same |
| Live sense | After `START_LIVE_FEED` / `START_TELEOP`, subscribe to capability channels | Same channels; HTML widgets bind to them |
| Closed-loop | Submit `OPTIMIZE` / jobs | Same (UI may not expose every solver knob) |
| Kernels | register / probe / pass ids into OPTIMIZE | Browse + select existing kernels; optional upload later |
| VC | commit / stash / load_snapshot / reconcile | Control graph UI |

### What Twin may poll continuously

Only:

1. **Tier C overview** — lab-state or equivalent (~1–2 Hz) for layout, leases, badges — explicitly not synchronized science.  
2. **Armed telemetry channels** — JPEG wire of `camera_image`, teleop pose WS — after the matching START_* primitive.  
3. **Job progress** while OPTIMIZE runs.

No widget may open a camera by calling an undefined helper. Catalog `capabilities.telemetry.live_feed` points at a channel that **is** the wire form of a declared measurable (or a documented live projection of it — see §6).

---

## 5. Edge side: `cloudlabs_edge/` in the lab repo

### Suggested layout (robot-deathray / lab_automation)

```text
robot-deathray/   # importable as lab_automation today
├── … existing drivers / managers / OpticalExperiment …
└── cloudlabs_edge/
    ├── main.py              # Edge Contract server (process entry)
    ├── contract.py          # schemas: execute, capabilities, epoch packets
    ├── kernel_host.py       # load/cache TorchScript; eval on local BGR
    ├── latch.py             # atomic observation epochs (§7)
    └── adapters/
        ├── motion.py        # MOVE_* / PICK / … → OpticalExperiment or MoveIt
        ├── motors.py        # MOVE_MOTOR / setpoints → wifi steppers etc.
        ├── vision.py        # capture BGR; encode JPEG wire for live_feed
        └── teleop.py        # START_TELEOP → LiveControlSession; WS pose/jog
```

Adapters are the **only** place that knows xArm vs MoveIt. Contract handlers never import ROS.

### Edge Contract v1 (UC-shaped — not a parallel API)

Southbound surface (names illustrative; semantics fixed):

| Surface | UC meaning |
|---------|------------|
| `GET /capabilities` | Declares supported **primitives**, measurable ids + analysis/wire formats, telemetry channels **bound to** tags/measurables, features (torchscript, reconcile) |
| `GET /bench` (or `/descriptor`) | **Static** bench geometry + identity Twin/SDK need once (layout, optional laser guides) — see §5.1 |
| `POST /execute` | **Exactly one primitive** + args (+ optional idempotency key). Arms/disarms streams only via START_*/END_* |
| Stream URLs in capabilities | **Transport** for armed telemetry — not new verbs |
| Kernel eval | **`EVAL_KERNEL` via `/execute` only** in v1 |

**Forbidden on the edge public surface:** `get_feed`, `get_table_cam`, ad-hoc binary dumps not tied to a measurable id and START_LIVE_FEED.

### 5.1 Static bench descriptor vs `capabilities` (layout.json and friends)

Today’s `lab_view/` bundle mixes several lifetimes. In the new model, split them:

| Kind | Examples (today) | Where it should live | Why |
|------|------------------|----------------------|-----|
| **Static geometry / map** | `layout.json` (bounds, danger zone, storage grid, breadboard spacing) | Edge **`GET /bench`** (file e.g. `cloudlabs_edge/bench/layout.json`), cached by coordinator for Twin/SDK | UI must draw the table; scripts may ask once. Rarely changes. |
| **Runtime abilities** | Supported primitives, stream paths, torchscript yes/no | **`GET /capabilities`** | Hot, versioned with the edge process; not geometry. |
| **Product catalog (UC)** | `component_library.json`, `active_catalog.json` | Prefer **coordinator** (or synced from edge at register time into coordinator catalog store) | Language of tags/tunables/measurables is cloud-labs product; edge must *honor* it, not own a second Wiki. |
| **Overlays** | `laser_lines.json` | Optional under `/bench` | Twin guides; static-ish. |
| **Stream tuning** | `table_cam_preview.json` profiles | Inside **capabilities** (`telemetry_channels` / wire profiles: fps, scale, Q) | Describes live channels, not table geometry. |
| **VC / commits** | `control/` | **Coordinator only** | Not an edge concern. |
| **Session checkpoint** | `session_last_lab_state.json` | Coordinator or edge-local recovery — not capabilities | Restart recovery, not discovery. |
| **Motor angle file** | `motor_rotations.json` | Runtime / latch path — not static bench | Live tunable samples, not layout. |
| **Communicator path** | `lab_manifest.json` communicator + `lab_automation_path` | Replaced by backend registry → `edge.base_url` | No more “import this folder.” |

**Do not stuff `layout.json` into `capabilities.json`.**  
Capabilities answers “what can this edge *do* right now?” Layout answers “what does this table *look like*?” Mixing them makes every capability poll carry geometry and confuses conformance (abilities vs map).

**Client access (same for Twin and SDK):**

1. On backend select / `connect`: coordinator fetches (or proxies) `GET {edge}/bench` once → Twin `applyLabLayout` / SDK `lab.get_bench()` (name TBD).  
2. Separately: `GET {edge}/capabilities` (cached, refresh on edge reconnect).  
3. Ongoing: lab-state / streams as today in the tier model.

Minimal `/bench` body for Twin (matches current `layout.json` spirit):

```json
{
  "backend_id": "real.default",
  "layout": {
    "version": 1,
    "lab_bounds_mm": { "x_min": -500, "x_max": 500, "y_min": -500, "y_max": 500 },
    "danger_zone": { "radius_mm": 90, "padding_mm": 5 },
    "storage": { "rule": "negative_xy", "grid_nx": 5, "grid_ny": 5 },
    "breadboard": { "grid_spacing_mm": 25, "origin_offset_mm": { "x": -7.4, "y": 0 } }
  },
  "laser_lines": []
}
```

Edge folder sketch:

```text
cloudlabs_edge/
  main.py
  capabilities.json          # or built dynamically
  bench/
    layout.json              # ← Twin canvas
    laser_lines.json         # optional
  adapters/
  …
```

Coordinator may **cache** `/bench` next to the backend registry so Twin still works if you later host a read-only copy for offline docs — but the **source of truth for a physical bench’s geometry** is the edge (the people who know the table).

### Integration with existing deathray code

| Existing piece | Role under edge agent |
|----------------|------------------------|
| `OpticalExperiment.*_cloudlab` | motion adapter targets |
| `LiveControlSession` (~50 Hz) | teleop adapter |
| Recorder TCP `GET_JPEG` / `CAP` | vision adapter (wire vs still) |
| WiFi `move_motor` | motors adapter |
| Future MoveIt | replace motion adapter internals only |

---

## 6. Unifying streams with measurables (addresses “JPEG isn’t a tensor”)

### Analysis vs wire (same measurable)

For `tag_22` / `measurables.camera_image`:

| Role | Format | Where |
|------|--------|--------|
| **Analysis** | `bgr_hwc_uint8` (H, W, 3) BGR | Edge RAM only for kernels / ensemble / `RECORD_MEASURABLES` commit path |
| **Wire** | lossy JPEG / MJPEG (profile: teleop scale/Q) | Network to Twin after `START_LIVE_FEED` |
| **Storage in runtime JSON** | PNG (or path handle) metadata | Tier C state after record |

Capabilities must say, for example:

```json
"measurables": {
  "tag_22.camera_image": {
    "analysis": { "layout": "bgr_hwc_uint8", "dtype": "uint8" },
    "wire": { "encoding": "jpeg", "profiles": ["default", "teleop"] },
    "live_channel": "tag_22.camera_image.live"
  }
},
"telemetry_channels": {
  "tag_22.camera_image.live": {
    "binds_measurable": "tag_22.camera_image",
    "transport": "mjpeg_http",
    "path": "/stream/tag_22/camera_image",
    "requires_primitive": "START_LIVE_FEED",
    "default_profile": "teleop"
  }
}
```

So the UI is not “getting a feed”; it is **reading the wire projection of a measurable** that was **armed by a primitive**.

### Teleop pose / motors

Live pose under TeleOp is **not** inventing a fake measurable named `get_pose`. Prefer one of:

**Option A (preferred for UC purity):** live channel bound to **tunables** being driven (`nominal_pose` / `nominal_motor_positions`) as “reported live samples of the same tunable fields,” stamped with `epoch_ms`, while TeleOp is active. Analysis scripts that need science-grade sync still use `RECORD_MEASURABLES` / latch (§7).

**Option B:** declare an explicit measurable for encoder readback if product decides encoders are observations without a setpoint — only if that matches the lab model (today motors are tunables recalculated by observe).

Do **not** create a third ontology called “raw telemetry blobs.”

### Tier mapping (Gemini’s three tiers, in UC)

| Tier | Rate | UC objects | Transport |
|------|------|------------|-----------|
| **A** | ~20–50 Hz | `TELEOP_JOG` / `TELEOP_GOTO` + live tunable samples | WebSocket (armed by `START_TELEOP`) |
| **B** | ~15–30 fps | Wire form of `camera_image` (or declared live channel) | MJPEG/JPEG (armed by `START_LIVE_FEED`) |
| **C** | ~1–2 Hz or event | Lab overview, VC, `RECORD_MEASURABLES`, `EVAL_KERNEL`, `OPTIMIZE` jobs | HTTP; BGR never leaves edge on probe |

Arming is always a **primitive**. Streams are never verbs.

### Tier A WebSocket payload rules (latency)

High-frequency TeleOp must not become a JSON tax on a single-threaded asyncio edge.

**Contract rules for Tier A messages (both directions):**

- **Flat and small** — top-level keys only; no deep nested objects/arrays-of-objects in the hot loop.
- **Fixed field set** — e.g. `epoch_ms`, `tag_id`, `cmd`, `axis` / `motor_id`, `val`, and a flat map of motor id → float for pose push. No arbitrary nested `tunables.nominal_pose.{…}` trees on the wire at 50 Hz (expand to flat keys or parallel float fields).
- **No catalog dumps, no BGR, no kernel payloads** on this socket.
- **Binary framing optional later** (v1.1) if JSON still spikes; v1 may stay JSON only if flat and tiny.

Nested UC documents remain valid on **Tier C** `/execute` and latch responses; they are forbidden on the TeleOp WS hot path.

---

## 7. Synchronization (“same time” images and motors)

### What we can and cannot guarantee

- We **cannot** guarantee zero delay from bench to a remote browser.  
- We **can** guarantee that a **payload produced on the edge** carries a common **`epoch_ms`** (bench monotonic clock preferred; document skew vs UTC) for all fields latched together.
- We **must not** pretend that “read camera buffer + read encoder at software `now`” is physically simultaneous without driver-level coordination.

### Edge latch (required for science and closed-loop)

When `RECORD_MEASURABLES` or `EVAL_KERNEL` runs (and optionally on a teleop “snapshot” if we add one later):

1. Edge **`latch.py` / vision+motor adapters** perform a coordinated capture, not a naive “now” snapshot. Industrial cameras have shutter + readout delay that can lag encoders by tens of ms if ignored.
2. Prefer **hardware-aware latching** where the platform allows it: e.g. wait for strobe-out / exposure-complete, hardware trigger that gates camera and encoder sample, or a documented per-device `capture_latency_ms` compensation applied consistently when HW sync is unavailable.
3. Assign one `epoch_ms` meaning “physical scene time” as defined by that latch policy (document whether epoch is trigger time or readout-complete time).
4. Run kernel on that BGR in RAM if requested.
5. Return scalars / handles / tunable samples **with the same epoch**, plus optional `latch_quality` (`hardware_triggered` | `software_approx`).

Client loops that care about sync **must not** open JPEG stream + separate motor HTTP call and pretend they match. They call **one** observe/probe primitive (or consume one epoch packet).

### Streams (Twin laser hunting)

Tier A/B streams should also stamp frames/poses with `epoch_ms`. Twin can display “pose age vs frame age.” For human teleop, approximate sync is usually enough; for optimizer correctness, use latch primitives on the edge, not dual independent polls.

### Tier A pose push (flat example)

Hot-path server → client (flat keys only):

```json
{
  "epoch_ms": 1781631600120,
  "tag_id": "tag_20",
  "m1": 12.405,
  "m2": -3.112
}
```

Hot-path client → server jog:

```json
{
  "epoch_ms": 1781631600125,
  "tag_id": "tag_20",
  "cmd": "JOG",
  "motor_id": "1",
  "val": 0.05
}
```

Camera wire frames carry `epoch_ms` in a header or multipart comment. That is still **the measurable/tunable language**, timestamped — not a new type system. Nested tunable trees belong in Tier C latch/`GET_TUNABLES` responses, not in the 50 Hz loop.

---

## 8. Kernels: compile, place, bandwidth

| Step | Where |
|------|--------|
| Author / register artifact | Client → coordinator registry |
| Download / cache `.pt` | Edge kernel host (on first use or job start) |
| Capture BGR | Edge vision adapter |
| Execute TorchScript | Edge RAM |
| Return | scalar / small feature vector (+ `epoch_ms`) |

`OPTIMIZE` inner loop runs entirely on the edge. Laptop scripts that need “feel” either use Twin teleop (human) or submit an edge job — not a WAN `for` loop over full frames.

---

## 9. Consistency checklist (accept / reject features)

A proposed feature is **in** only if all are true:

1. Named as an existing **primitive**, or a documented expansion of one (macro).  
2. Any live bytes map to a **catalog measurable or tunable live channel** with analysis + wire decls.  
3. Arming/disarming uses START_*/END_* (or equivalent) primitives.  
4. Twin and SDK can both invoke the same primitive (UI may hide some).  
5. Edge capabilities advertise support; unsupported → clean refusal.  
6. Science-grade multi-signal use goes through **latched** observe/probe, not dual ad-hoc polls.

If Twin “needs” something that fails this list, **upgrade the language** (new primitive or measurable decl) in cloud-labs first, then implement on mock edge, then lab edge — never the reverse.

---

## 10. Migration stance (no code in this pass)

Order when we implement later:

1. Freeze this doc + Edge Contract schemas (mock implements first).  
2. Point registry at mock edge process; Twin/SDK use only contract surfaces.  
3. Add `cloudlabs_edge/` in robot-deathray wrapping current OpticalExperiment / recorder / LiveControlSession.  
4. Remove lab_automation imports from cloud-labs `real/`.  
5. Fix topology: all Tier A/B terminate on edge.  
6. MoveIt stays behind deathray motion adapter.

---

## 11. Answers to the two hard worries (short)

**Coordination / 0.5 s skew**  
Streams get `epoch_ms`; closed-loop and scripts that need truth use **one latched** `RECORD_MEASURABLES` / `EVAL_KERNEL` on the edge so image and encoders share an epoch. Do not compose science from independent live polls.

**Language vs fast JPEG**  
JPEG is the **wire format of `camera_image`**, armed by `START_LIVE_FEED`, declared in capabilities. It is not a parallel primitive. Analysis remains `bgr_hwc_uint8` on the edge. Prefer a slower guarantee of UC over a fast undefined `get_feed`.

---

## 12. Locked decisions (v1)

| # | Decision | Choice | Rationale |
|---|----------|--------|-----------|
| 1 | Live TeleOp pose | **Option A — tunable live samples** | Encoder feedback is reporting the same DOFs as commanded tunables; do not invent a parallel measurable. |
| 2 | Stream path to Twin | **Coordinator proxy by default**, with **LAN direct** only when both client and edge are confirmed on the same secure LAN | Home/VPN clients cannot reach a bench PC behind campus firewall; proxy must be lightweight (chunked MJPEG / WS route-through), not re-encode BGR. |
| 3 | Kernel probe API | **`EVAL_KERNEL` via `/execute` only** in v1 | No `/kernel/probe` alias; keeps the edge surface narrow and every eval under the same lease/logging path. |
| 4 | `epoch_ms` clock | **Bench monotonic** for latch/stream stamps; document mapping to wall clock separately if needed | Avoid NTP step jumps in closed-loop deltas. |
| 5 | Tier A WS shape | **Flat, minimal JSON** (see §6) | Avoid asyncio JSON spikes from nested schemas at 50 Hz. |
| 6 | Latch physics | **Hardware-aware latch** in edge `latch.py` (strobe/trigger or documented latency compensation) | Software “now” alone can skew image vs motors by tens of ms. |

---

## 13. Bottom line

Gemini’s tiers and “edge owns BGR” are right. Your insistence on **UC self-consistency** is the governing constraint. The architecture is:

- **Coordinator** = language, leases, VC, kernel registry, edge registry.  
- **Client** = Twin ≌ SDK (same verbs; Twin adds subscriptions/widgets).  
- **Edge (in lab repo)** = contract implementation + adapters; streams are wire forms of declared measurables/tunables, armed only by primitives; latch/epoch for sync.

We optimize latency **inside** that story — placement, WS/MJPEG transports, edge kernels — not by punching holes called `get_feed`.
