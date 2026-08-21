"""Agent-skill templates shipped inside every ``cloudlabs_edge/`` skeleton.

These live under ``.agents/skills/`` (not a Cursor-only path) so humans and any
AI assistant can open them while filling Phase 6 adapters. ``cloudlabs-edge
init`` writes them; ``doctor`` checks they are present.
"""

from __future__ import annotations

from pathlib import Path

# Relative paths under the edge root (checked by doctor).
REQUIRED_AGENT_SKILL_FILES: tuple[str, ...] = (
    ".agents/README.md",
    ".agents/skills/cloudlabs-context/SKILL.md",
    ".agents/skills/edge-contract/SKILL.md",
    ".agents/skills/measurables-tensors/SKILL.md",
    ".agents/skills/camera-bringup/SKILL.md",
    ".agents/skills/latency-channels/SKILL.md",
    ".agents/skills/kernels-optimize/SKILL.md",
    ".agents/skills/optimize-pipeline-debug/SKILL.md",
    ".agents/skills/tunables-hold-for-measure/SKILL.md",
    ".agents/skills/frames-calibration/SKILL.md",
    ".agents/skills/inventory-honesty/SKILL.md",
)

AGENTS_README = """\
# Agent skills for this edge

This folder ships with every Edge Contract skeleton. It is **tool-agnostic**:
read it yourself, or point any AI assistant at a skill when filling Phase 6
adapters. These are not a second API — they coach how to honor the contract
already defined by `capabilities.json`, `SKELETON.md`, and
`cloudlabs-edge certify`.

Start with [`skills/cloudlabs-context`](skills/cloudlabs-context/SKILL.md) if you
need the product mental model (same story as scientist onboarding). Then open a
topic skill for the adapter you are filling.

| Skill | Open when you are… |
|-------|--------------------|
| [`skills/cloudlabs-context`](skills/cloudlabs-context/SKILL.md) | Orienting: what Cloud Labs is, who talks to whom |
| [`skills/edge-contract`](skills/edge-contract/SKILL.md) | Adding endpoints, verbs, or side APIs |
| [`skills/measurables-tensors`](skills/measurables-tensors/SKILL.md) | Capturing images / shaping MeasurableTensor |
| [`skills/camera-bringup`](skills/camera-bringup/SKILL.md) | Real-lab camera init, exclusive USB, scan vs science cams |
| [`skills/latency-channels`](skills/latency-channels/SKILL.md) | Teleop, live video, or long jobs |
| [`skills/kernels-optimize`](skills/kernels-optimize/SKILL.md) | Scoring frames or closed-loop OPTIMIZE |
| [`skills/optimize-pipeline-debug`](skills/optimize-pipeline-debug/SKILL.md) | Debugging capture / kernel / actuate on this edge |
| [`skills/tunables-hold-for-measure`](skills/tunables-hold-for-measure/SKILL.md) | Keeping non-optimized tunables fixed when capturing |
| [`skills/frames-calibration`](skills/frames-calibration/SKILL.md) | Table↔robot transforms |
| [`skills/inventory-honesty`](skills/inventory-honesty/SKILL.md) | Store / place / inventory verbs |

After each adapter change, run::

    cloudlabs-edge doctor --path .
    cloudlabs-edge certify <edge-url> --path . --profile stub
"""

CLOUDLABS_CONTEXT_SKILL = """\
---
name: cloudlabs-context
description: >-
  Scientist-facing mental model of Cloud Labs (from docs/Onboarding.md).
  Use when orienting on this edge, explaining why adapters must speak the shared
  vocabulary, or deciding what belongs in the edge vs the coordinator/SDK.
---

# What Cloud Labs is

Cloud Labs lets someone drive a physical optics bench — robot arm, industrial
camera, laser, steppers, breadboard components — the same way they would drive a
simulation: a short script or the browser Twin says move this component, take a
picture, score it, optimize. The promise is **one vocabulary** for everything
you can do or observe, whether the answer comes from a laptop mock, a physics
simulation, or the real laser bench.

That matters because experimental work is iteration: debug logic on mock,
rehearse physics in simulation, run the same sequence on hardware without
rewriting the science.

## Vocabulary (the only language)

- **Primitives** — the only verbs that change or observe the world (move,
  record, optimize, arm live/teleop, …). No private side doors such as
  `get_video_feed()` outside that list.
- **Tunables** — commanded degrees of freedom (nominal pose, exposure, laser
  power, motor angle). You set them; the bench tries to make them true.
- **Measurables** — observations without a setpoint (e.g. camera image). You
  capture them; you do not “write pixels.”
- **Telemetry channels** — live feeds declared in capabilities (video, teleop),
  usable only after the arming primitive.
- **Kernels** — small TorchScript scorers that turn a frame into a score or
  features; closed-loop OPTIMIZE runs them next to the camera.

Rule: if it should happen, it must be nameable in this vocabulary — that is what
keeps scripts portable across benches.

**Edge READY** means `SYNC_RUNTIME` has completed (recordable tunables overwritten
from the world; non-recordable tunables set from `set_at_init`) — not merely that
`/health` responds. Mock/sim may implement sync as copy-from-state/JSON; a real
bench must measure. Twin hides canvas / component lists until
`lab_initialization.ready` (opaque init gate).

## Who talks to whom

The scientist’s script and the **Twin** both talk to a **shared service**
(coordinator). That service validates verbs, holds the session lease, and
forwards work to lab software. The Twin is the same vocabulary with a visual
shell — not a second, more powerful API.

**This folder (`cloudlabs_edge/`)** is the lab software beside the instrument.
It alone may touch the arm, camera, and steppers. It owns messy details
(coordinate frames, camera protocols, motion planning) so none of that leaks
into scientist notebooks. From the edge’s point of view: honor the shared
vocabulary and refuse loudly when you cannot — never invent success.

## Preview vs truth

Live video and teleop are for the human eye (alignment, intuition). Latched
`RECORD_MEASURABLES` / kernel probes are for science: one timestamp (and an
honest latch note) ties image and related fields together. Compose analysis from
latched records, not from two independent live polls.

## Closed loop

Shipping every frame to a laptop to score is the wrong geometry. Capture →
kernel → actuate should stay on the bench; the script submits a job and waits
for the result. Session kernels are how a scientist brings *their* score onto
that path.

## Named labs

`connect("mock.default")` / `"sim.default"` / `"real.default"` (or another
registered id) is how a scientist picks an instrument. Mock, sim, and real are
the same kind of system behind different names — this edge is one of those
instruments.

## Coordinator vs edge data (do not mix)

| Belongs on **this edge** | Belongs on the **coordinator** (cloud-labs) |
|--------------------------|---------------------------------------------|
| `data/library.json` + `data/inventory.json` | Working Twin lab-state FSM (`BUSY`/`HOLDING`/`IDLE`, presence, commanded tunables) |
| Bench layout / geometry (`GET /bench`) | Version control (`control/` under `coordinator_data/<backend_id>/`) |
| Motor / physical tunable tracking, RECORD/SYNC truth | Twin-only overlays for now (e.g. laser lines) |
| Streams, teleop live samples, `runtime_sync` | Session lease / jobs |

- After a successful primitive, the coordinator applies language `commit_*` and
  owns Twin `system_status` (BUSY while the southbound execute runs; then
  IDLE/HOLDING from commits) even if edge `lab_state` stays IDLE — **do not**
  invent Twin FSM writers, busy endpoints, or stash/dirty “fixes” on the edge.
- Do not author a parallel library / inventory / active_catalog on the
  coordinator; active tags are inventory keys.
- Debug: `[lab_init]` = SYNC/READY; `[lab_state]` / `[control]` / `[backend]`
  are coordinator-side. See Cloud Labs `docs/BACKEND_ISOLATION.md`.

## Teaching host import path (cloud-labs maintainers)

In-tree teaching packages live at `mock_backend/src` and `simulation_edge/src`
(folder name ≠ `backend_id`). The coordinator must put those dirs on
`sys.path` (see `backend/main.py` bootstrap) or `PYTHONPATH` — otherwise Twin
can show the mock as ready, then hang with `No module named 'mock_backend'`
and almost no log. Registry probe must fail closed + log `[backend] … FAILED`
when the in-process host cannot import. After renaming a teaching package,
grep path bootstrap, ops scripts, and skills — do not leave “ready” without
an import check.

## Full onboarding

Longer scientist-facing narrative (with figures): Cloud Labs repo
`docs/Onboarding.md` (PDF: `docs/Onboarding.pdf`).
"""

EDGE_CONTRACT_SKILL = """\
---
name: edge-contract
description: >-
  Implements Cloud Labs Edge Contract v1 rules for this cloudlabs_edge folder.
  Use when adding HTTP endpoints, primitives, adapters, or any lab-facing API;
  when tempted to add a side door outside POST /execute; or when mapping
  NotImplementedError to refused/failed responses.
---

# Edge Contract (this folder)

## Do

- Mutation has **one door**: `POST /execute` with `{"primitive", "args"}`.
- Route verbs through `dispatch.dispatch_primitive` → `adapters/*`.
- Keep `GET /capabilities` and `GET /bench` as declarations only (abilities vs geometry).
- Declare `layout.reconcile_staging_seats` on `GET /bench` when the bench can Park for swaps (coordinator `plan_batch`); empty ⇒ fail closed.
- Advertise shared Command Matrix topology in `capabilities.json` as
  `execution_threads`: `arm.0` + `sense.0` only. Do **not** list bare
  `motor.<n>` columns — `motor_id` is per-tag; the coordinator creates
  `motor.<tag_id>.<motor_id>` on demand (see cloud-labs `docs/COMMAND_MATRIX.md`).
- Map outcomes with `contract.py`: `completed` / `refused` / `failed`.
- Raise `NotImplementedError` for unsupported verbs → surface as `refused` + `NOT_IMPLEMENTED`.
- Keep hardware imports inside `adapters/` (and lab runtime helpers), not in `main.py`.

## Do not

- Do not add `get_video_feed()`, per-verb REST routes, or Twin-only shortcuts.
- Do not merge bench geometry into capabilities (or the reverse).
- Do not invent success for missing hardware — refuse or fail honestly.
- Do not import coordinator / SDK packages from the edge process.
- Do not implement a command queue on the edge — `/execute` stays single-shot;
  the coordinator owns queues, HOLDING locks, and OPTIMIZE barriers.
- Do not add Twin-only status endpoints or write Twin `system_status` /
  dirty/stash / `telemetry.teleop.active` to “fix” the UI — the coordinator
  owns BUSY/HOLDING/IDLE/TELEOP around southbound execute (see
  `cloudlabs-context`, `latency-channels`, BACKEND_ISOLATION).

## Check

```text
cloudlabs-edge doctor --path .
cloudlabs-edge certify <url> --path . --profile stub
```

Read `SKELETON.md` and `capabilities.json` `supported_primitives` before adding a verb.
"""

MEASURABLES_TENSORS_SKILL = """\
---
name: measurables-tensors
description: >-
  Builds canonical camera_image MeasurableTensor envelopes, LazyRefs, and
  latched epochs on a Cloud Labs edge. Use when implementing RECORD_MEASURABLES,
  serving /measurables/... bytes, changing image metadata, or resolving tensors
  for scripts.
---

# Measurables and tensors

## Do

- Emit `camera_image` with the **canonical** envelope (dtype/domain/axes/units/layout)
  matching the coordinator schema — prefer a local `measurables_schema.py` helper.
- Return heavy pixels as a **LazyRef** (`kind=url`, href under this edge); cache the
  exact JPEG from the latched capture so the URL is not a fresh live frame.
- Open/close a latch around capture (`latch.py`); stamp `epoch_ms` + `latch_quality`.
- Serve `GET /measurables/{tag}/camera_image.jpg` from that cache.
- When capture needs a hardware setting (exposure, …), resolve it from **current lab
  tunables** — see [`tunables-hold-for-measure`](../tunables-hold-for-measure/SKILL.md).
- **Log the science path** with a stable `[measurables]` prefix: begin (tag, camera
  class, cam_id, exposure), capture shape/dtype, jpeg byte length, envelope href,
  epoch_ms / latch_quality, and failures with stage + traceback. Also log when the
  registry falls back to `SyntheticCamera` and when the JPEG route misses (no latch).

## Do not

- Do not invent axis labels, swap H/W, or claim `hardware_triggered_latch` without hardware.
- Do not put full BGR arrays in lab-state JSON.
- Do not let live-stream endpoints substitute for `RECORD_MEASURABLES` science.
- Do not swallow capture errors silently — refuse/fail with a logged stage so Twin
  and the edge terminal agree on what happened.
- Do not invent OPTIMIZE-only defaults for tunables that already exist on the lab
  (e.g. hard-coded CAP exposure while `exposure_time_ms` is set).

## Typical files

- `adapters/observe.py`, `latch.py`, measurable schema helper, stream routes in the server app.

## Check

Certify must pass the measurable-envelope conformance check after changes.

## Related

- [`camera-bringup`](../camera-bringup/SKILL.md) — get the physical camera live
  before RECORD (exclusive USB, boot order, resolution vs calibration).
"""

CAMERA_BRINGUP_SKILL = """\
---
name: camera-bringup
description: >-
  Real-lab camera initialization, exclusive device ownership, scan vs science
  cameras, frame quality, and startup failure modes on a Cloud Labs edge. Use
  when wiring boot camera init, debugging CONNECT / no-frame / wrong-resolution
  captures, leftover recorder processes, OpenCV vs vendor-SDK collisions, or
  deciding what belongs in inventory vs bench infrastructure. Mostly relevant
  to hardware edges (mock/sim may stub or skip).
---

# Camera bring-up (real labs)

Cameras fail more often at **startup and exclusive access** than inside a single
`POST /execute`. Fix ownership and boot order before chasing Twin FSM bugs.

Mock and simulation edges may have no USB cameras — keep adapters honest
(`refused` / synthetic) and skip hardware bring-up. This skill is for benches
that open real devices.

## Two jobs, two stacks

Most optics labs have:

| Job | Typical stack | Driven by |
|-----|---------------|-----------|
| **Science / Twin stills** (`RECORD_MEASURABLES`, live feed) | Vendor SDK or a long-lived recorder process that owns USB | Inventory tags whose library binding says so (e.g. `recorder_tcp`) |
| **Scene localization** (AprilTag / stereo / overview for LOCALIZE) | OS camera API (often OpenCV) + calibration | Bench infrastructure — **not** “put every camera in inventory so it initializes” |

Do not merge those stacks. Opening the same physical camera twice (SDK + OpenCV,
or two processes) yields “no camera”, empty frames, or a listening TCP port that
never CONNECT successfully.

## Ownership (normative)

| Piece | Owner |
|-------|--------|
| Which science cameras must be live | **This edge** — `data/inventory.json` ∩ library camera bindings |
| Boot spawn + CONNECT / open | **This edge** (e.g. `cameras/initialize.py` after robot home) |
| Scan / overview OpenCV (or equivalent) | Host experiment runtime — open as infrastructure when needed |
| Twin / coordinator | Never starts lab cameras; only consumes measurables / streams |

`lab_automation` (or your host SDK) must **not** import edge inventory to decide
camera lists. Edge boot loads inventory + library, then opens what inventory
requires.

Launch scripts should start the edge HTTP process (and calibration env) — not
silently spawn a second camera stack unless that is the documented bring-up path.

## Index namespaces (do not conflate)

Labs often have three different integers that look alike:

| Namespace | Meaning |
|-----------|---------|
| Logical / Twin cam id + TCP port | Client identity for RECORD / live (`cam_id`, `recorder_port`) |
| Vendor SDK device index | Enumeration from **that** SDK (“Found N cameras: 0: …”) |
| OS / OpenCV device index | Enumeration from VideoCapture / Media Foundation / V4L2 |

A science camera’s SDK index is **not** an OpenCV device index. Feeding an SDK
index into OpenCV (or the reverse) remaps the wrong sensor — often a ceiling cam
at the wrong resolution — and breaks stereo / PnP against calibration that
assumes a specific pixel size.

## Frame quality

- Requested resolution must match **calibration intrinsics**. Opening a 4K-calibrated
  overview at VGA produces systematically wrong world poses (often multi-meter scale).
- After open, do a **warm-up grab** before the first science or scan frame —
  exclusive backends (e.g. Windows MSMF) often return empty until the stream settles.
- Prefer one open per device. Never construct a default camera handle as a
  `dict.get(key, CameraDriver(...))` fallback — many languages evaluate the
  default even on a hit and **double-open** the device.
- When the active catalog has no OpenCV/overview bindings (only science cameras
  in inventory), still open the **scan pair / overview cams your LOCALIZE path
  needs** as infrastructure. Do not fall through to “open every legacy USB index”
  including gripper indices the vendor SDK already owns.

## Inventory vs infrastructure

- Inventory = tracked components / science cameras Twin should know about.
- Overview / stereo / fixed scan cameras are usually **fixtures**. Legacy
  active catalogs often listed only manipulables — same idea: do not add overview
  tags to inventory merely so they “initialize.”
- Library may still describe a table-top camera for UI/stream metadata; that does
  not require inventory membership for LOCALIZE.

## Leftover processes and ports

Long-lived recorder / grabber processes listen on localhost ports. After a crash:

- A port may still **accept TCP** while the USB session is dead → CONNECT /
  CameraInit fails on “reuse.”
- Prefer: detect listen → try CONNECT → on failure **kill listener and respawn**
  once, rather than stacking a second process on the same port.
- On a shared Windows/Linux box, Twin HTTP, edge HTTP, and recorder ports are
  distinct. Bind failures (`address already in use`) mean another process still
  owns that port — identify and stop it; do not treat it as a catalog bug.

Typical local layout (adjust to your lab): coordinator ~`8000`, edge HTTP
~`8200`, science recorders on dedicated localhost ports.

## Boot order

```text
get_experiment() / edge host construct
  → open host runtime (catalog = library ∩ inventory for tracked tags)
  → initialize_robot() / safe home   # before exclusive camera storms if needed
  → initialize science cameras()     # inventory bindings → spawn + CONNECT
  → ensure scan/overview cams open   # infrastructure; resolution = calibration
  → boot SYNC_RUNTIME / RECORD_TUNABLES
```

Skip hardware init with an explicit env flag for laptop / certify stub profiles
(e.g. `CLOUDLABS_SKIP_CAMERA_INIT` / mock) — never silently pretend CONNECT ok.

## Logging

Use stable prefixes so Twin and the edge terminal agree:

- `[cameras]` — spawn, port reuse/respawn, CONNECT ok/fail
- `[measurables]` — capture path after the device is live
- `[scan-debug]` or equivalent — which OS indices opened, resolution got vs want

## Failure cheatsheet

| Symptom | Likely cause |
|---------|----------------|
| Connection refused on recorder port | Process never spawned / wrong port |
| Port already open, then CameraInit / “no camera” | Stale listener; need kill + respawn |
| Vendor SDK “no camera” right after OpenCV open | Competing stack stole the USB device |
| Scan poses nonsense / huge | Overview opened at wrong resolution or wrong index |
| Intermittent empty grab / MSMF errors | Double-open, USB bandwidth, or missing warm-up |
| Twin RECORD fails, edge looks “ready” | Bring-up skipped or CONNECT failed; not a coordinator FSM bug |
| Twin bind / edge bind address in use | Leftover process on that port |

## Do

- Bring science cameras up once at edge boot from inventory ∩ library.
- Keep scan/overview opens in the host runtime as infrastructure.
- Treat each index namespace as distinct; document them in library parameters.
- Respawn dead listeners; log CONNECT results explicitly.
- Match capture resolution to calibration; warm-up before first use.

## Do not

- Do not start cameras from the Twin / coordinator.
- Do not open the same physical device from two stacks.
- Do not put every fixture camera in inventory “so init runs.”
- Do not pass the **full** library as the experiment catalog when that opens every
  `vision_device_index` / overview row and remaps scan cameras.
- Do not invent Twin success when the device never CONNECT’d / never grabbed.

## Check

1. Edge log: science cameras CONNECT ok (or honest skip under mock/skip flags).
2. Scan/overview cams report the resolution calibration expects.
3. `RECORD_MEASURABLES` returns a LazyRef JPEG; LOCALIZE poses are bench-scale.

## Related

- [`measurables-tensors`](../measurables-tensors/SKILL.md) — envelope after a good frame
- [`inventory-honesty`](../inventory-honesty/SKILL.md) — what inventory means
- [`frames-calibration`](../frames-calibration/SKILL.md) — table frame after PnP
- [`latency-channels`](../latency-channels/SKILL.md) — live preview vs latched record
"""

LATENCY_CHANNELS_SKILL = """\
---
name: latency-channels
description: >-
  Chooses the correct Cloud Labs edge channel for teleop, live video, and
  deliberate work. Use when implementing START_TELEOP, live feed streams,
  WebSockets, OPTIMIZE jobs, debating HTTP vs stream vs job, or when Twin
  teleop UI never shows loading/controls after a successful START_TELEOP.
---

# Latency channels

## Three tiers (all still in the vocabulary)

| Tier | Arm with | Transport | Use for |
|------|----------|-----------|---------|
| A | `START_TELEOP` | Flat WS frames | Interactive jog / pose |
| B | `START_LIVE_FEED` | JPEG / MJPEG | Preview for the eye |
| C | `POST /execute` / jobs | Request or job stream | Move, record, optimize |

Live preview exposure is a **Tier B hyperparameter**:

- Optional `parameters.exposure_time_ms` on `START_LIVE_FEED` → recorder `VEXP` only
- `SET_LIVE_EXPOSURE` while armed → `VEXP` only; Twin stores it on
  `telemetry.live_feed.stream.live_exposure_time_ms`
- Do **not** write science `tunables.exposure_time_ms` from these paths —
  that remains `SET_EXPOSURE` for RECORD / EVAL_KERNEL / OPTIMIZE (CAP)

## Twin UI teleop session vs edge hardware lease

These are **two different leases**. Confusing them is the usual “robot grabbed
the part but Twin never entered teleop mode” bug.

| Layer | Owns | What Twin needs |
|-------|------|-----------------|
| **This edge** | Hardware arming (LiveControl / robot enter), WS path, jog/goto execution | Honest `completed` / `refused` on `START_TELEOP` / `END_TELEOP` |
| **Coordinator (Twin)** | `components[tag].telemetry.teleop.active` / `ready` / `mode`, and Twin `system_status=TELEOP` | Commit after remote execute succeeds |

- Edge `START_TELEOP` may return a **flat** result
  (`{tag_id, active: true, ws_path}`) — that is enough. You do **not** need to
  invent a nested Twin `telemetry` blob for the UI to work on current Cloud Labs.
- Edge `GET /lab-state` may keep `telemetry.teleop.active=false` as a static
  template. Twin merge **preserves coordinator teleop session flags** and only
  overlays edge streams (`live_feed`, samples). Do not “fix” Twin by writing
  Twin FSM fields from the edge.
- Twin shows LOADING when `active && !ready`, and jog controls when
  `active && ready`. After a successful remote START, the coordinator commits
  both (hardware setup already finished on the edge).

If START succeeds on the arm but the UI stays idle: check the **coordinator**
remote-commit path / lab-state poll — not a missing inventory camera and not a
frontend-only bug.

## Do

- Reject nested teleop JSON; keep Tier A payloads tiny.
- Return HTTP 409 on stream URLs until the matching START primitive armed the channel.
- Run long OPTIMIZE loops as **jobs** on the edge (progress stream), not a held HTTP body.
- Stamp time on samples; never pretend live preview is a scientific record.
- **START_TELEOP mode routing** (must match Twin `rz` vs `pose3d`):
  - On table / not holding this tag → table Rz enter (`start_table_rotation` / equivalent).
  - Holding this tag → held Cartesian enter (`start_held_cartesian` / equivalent).
  - Log which enter path ran; refuse clearly if the arm holds a *different* tag.

## Do not

- Do not closed-loop optimize by shipping every frame to a laptop.
- Do not open teleop or video without the arming primitive.
- Do not mix Tier A rates with deep schema validation on every frame.
- Do **not** always call held-Cartesian on START_TELEOP — that breaks on-table Rz TeleOp
  (`start_held_cartesian: not holding a part` while the Twin expected Rz).
- Do not invent Twin-only teleop status endpoints or write Twin
  `telemetry.teleop.active` / `system_status=TELEOP` from the edge so the UI
  “looks right” — the coordinator commits those after remote START/END.
- Do not require every lab to mirror Twin’s teleop FSM inside edge `/lab-state`
  for certify; arm the hardware and return contract outcomes.

## Typical files

- `adapters/teleop.py`, `adapters/live_feed.py`, `adapters/optimize.py`, server WS/stream routes, `runtime/jobs.py` if present.
"""

KERNELS_OPTIMIZE_SKILL = """\
---
name: kernels-optimize
description: >-
  Edge-owned TorchScript catalog, capture/router seams, and OPTIMIZE pipeline
  jobs on a Cloud Labs edge. Use when editing kernels/, kernel_host, adapters/optimize,
  optimization/capture_impl or router_impl, premade builtins, or closed-loop sessions.
---

# Kernels and optimize (edge)

## Architecture (locked)

```text
Twin/SDK  --HTTPS once-->  edge OPTIMIZE job
                             capture → kernel → loss → stepper → router
```

- General engine: `cloudlabs_edge_dev.optimization` (no `cloudlabs` / `lab_model` imports).
- Lab seams only: `optimization/capture_impl.py`, `optimization/router_impl.py`.
- Premade catalog = **this tree** `kernels/manifest.json` + `.pt` (not coordinator `schemas/kernels/`).
- Session kernels (`session.*`) may arrive in the job payload; never re-ship premade ids.
- `kernel_host._resolve_path` must map id → filename via **local** `manifest.json` `artifact` (ids use dots, files use underscores). Do not require `cloudlabs_edge_dev` on the bench just for that map; falling back to `{kernel_id}.pt` yields `builtin.roi_centroid.pt` (missing) when the file is `builtin_roi_centroid.pt`.

## Premades this experiment expects

| Id | Features | Objective habit |
|----|----------|-----------------|
| `builtin.roi_centroid` | `[cx, cy]` | `rms_distance` → pixel target |
| `builtin.beam_power` | `[flux, peak, sat]` | maximize flux; watch sat |
| `builtin.gaussian_beam_fit` | `[amp, cx, cy, σx, σy]` | often minimize σₓ |
| `builtin.beam_shift` | `[dx, dy, magnitude]` | maximize ‖Δ‖ from **this frame** `(W/2,H/2)`; weight −1; **no** reference capture |
| `builtin.beam_com` | `[cx, cy, peak]` | full-frame CoM; maximize signed offset from operator origin along X/Y ± (`signed_axis_offset`) |

Rebuild/seed: from cloud-labs repo `python scripts/ops/build_torchscript_kernels.py --seed-edges`.

## Do

- Kernels are **inputs** to `EVAL_KERNEL` / `OPTIMIZE`, not new verbs.
- In-loop capture: latched tensor (BGR), **no JPEG / LazyRef** per eval.
- Continuous blocks: `assert_optical_path_clear` (arm must not hold); bookkeeping block-scoped.
- Honor `solver.constraints` `max_delta_from_start` and `keep_best` / `rollback_on_fail`.
- Emit per-eval `stages: {capture, kernel, actuate}` on job progress (Twin debug panel).
- `GET /kernels` must reflect local manifest + `artifact_present`.
- Non-variable tunables stay at current lab values when capturing — see
  [`tunables-hold-for-measure`](../tunables-hold-for-measure/SKILL.md).

## Do not

- Do not import `cloudlabs` or `lab_model` on the edge.
- Do not silently downgrade `mode=ensemble` to legacy single-tag COBYLA without `pipeline`.
- Do not bake a fixed camera center (e.g. 2790) into beam_shift — use frame size.
- Do not add private `/kernel/*` HTTP outside the contract.
- Do not invent a separate OPTIMIZE exposure (or other tunable) — use the lab's current value.
- Do not resolve premade paths as `{kernel_id}.pt` when a manifest `artifact` exists.

## Typical files

- `kernels/manifest.json`, `*.pt`, `kernel_host.py`
- `adapters/optimize.py`, `adapters/observe.py`, `adapters/motion.py` (`clear_for_measure`)
- `optimization/capture_impl.py`, `optimization/router_impl.py`
"""

TUNABLES_HOLD_FOR_MEASURE_SKILL = """\
---
name: tunables-hold-for-measure
description: >-
  Keep non-optimized lab tunables fixed when capturing measurables (RECORD,
  EVAL_KERNEL, OPTIMIZE). Use when wiring capture exposure, SET_EXPOSURE,
  observe.capture_tensor, or any path that might invent OPTIMIZE-only defaults.
---

# Tunables hold for measure (edge)

## Invariant (locked)

When obtaining a measurable — including every OPTIMIZE eval capture:

1. **Actuators / tunables that are OPTIMIZE variables** may move (stepper + router).
2. **Every other tunable stays at its current lab value** (last SET / RECORD /
   set_at_init / library default already in effect).
3. There is **no separate “optimize exposure”** (or similar) knob. Capture uses
   the same settings the rest of the lab would use for that camera/source.

```text
lab tunables (exposure, power, …)  ──hold──►  measurable capture
OPTIMIZE variables (motors, pose, …) ──move─►  only those paths
```

## Why this is not one middleware

Different measurables need different settings (camera exposure vs laser power vs
future filters). There is no single “apply all tunables” call before every
capture. Enforce the invariant **per capture seam**:

- Resolve needed settings from **current lab state**, never invent OPTIMIZE-only
  hard-codes that bypass that state.
- Worked example: `adapters.observe.resolve_exposure_s_for_tag` — order is
  explicit override → camera last commanded → tunable overlay → library default
  → hardware last resort.
- `SET_EXPOSURE` must `commit_tunable` so overlays match hardware.
- Live preview brightness is separate: `SET_LIVE_EXPOSURE` / START_LIVE_FEED
  `exposure_time_ms` apply recorder `VEXP` only and must **not**
  `commit_tunable(exposure_time_ms)`.

## Do

- Before CAP / RECORD / OPTIMIZE capture, resolve exposure (and any future
  capture-affecting tunable) from lab state as above.
- Keep OPTIMIZE router scoped to `variable_ids` in the active block; do not nudge
  unrelated motors/poses “for the measurement.”
- When adding a new capture-affecting tunable, extend a resolver (or mirror the
  exposure pattern) — do not add a Twin Optimizer UI field unless the tunable is
  itself an OPTIMIZE variable.

## Do not

- Do not hard-code CAP exposure (e.g. 50 ms) when `exposure_time_ms` is already
  set on the lab / camera / library default.
- Do not snapshot a second “optimize exposure” into the pipeline unless the
  operator explicitly chose exposure as a variable.
- Do not reset tunables to library defaults mid-OPTIMIZE unless the job says so.

## Typical files

- `adapters/observe.py` (`resolve_exposure_s_for_tag`, `capture_tensor`, RECORD)
- `adapters/tunables.py` (`SET_EXPOSURE` + `commit_tunable`)
- `cameras/base.py` / `mindvision_tcp.py` (last commanded exposure)
- `optimization/capture_impl.py`

## Related

- [`kernels-optimize`](../kernels-optimize/SKILL.md) — closed-loop job shape
- [`measurables-tensors`](../measurables-tensors/SKILL.md) — envelope after capture
- [`optimize-pipeline-debug`](../optimize-pipeline-debug/SKILL.md) — stage triage
"""

OPTIMIZE_PIPELINE_DEBUG_SKILL = """\
---
name: optimize-pipeline-debug
description: >-
  Debug failed or stuck OPTIMIZE sessions on a Cloud Labs edge by isolating
  capture vs kernel vs actuate. Use when stage debug shows FAIL, max-delta
  refuses, clearance errors, missing artifacts, empty features, or silent IDLE.
---

# Optimize pipeline debug (edge)

Every eval should report `stages` in job progress / trace. Triage in order:

## 1. Capture

**Symptoms:** `capture.ok=false`, empty frames, wrong shape, no camera.

**Check**

- `optimization/capture_impl.py` / `adapters/observe.capture_tensor` — latch + BGR in-process.
- Tag/field in pipeline `capture[]` matches a real camera measurable.
- Exposure: must follow current lab `exposure_time_ms` (see
  `tunables-hold-for-measure` / `resolve_exposure_s_for_tag`); arm not occluding.
- One-shot: `RECORD_MEASURABLES` / `EVAL_KERNEL` on the same tag.

## 2. Kernel

**Symptoms:** `kernel.ok=false`, missing `.pt`, torch refuse, empty features scored as perfect (should be invalid/penalty).

**Check**

- `GET /kernels` → id listed and `artifact_present: true`.
- File exists under `kernels/` and matches manifest `artifact`.
- Symptom `not found at …/builtin.roi_centroid.pt` (dots in filename) while `builtin_roi_centroid.pt` exists → `kernel_host` fell back to `{kernel_id}.pt` instead of reading local manifest `artifact`. Fix `_resolve_path` to use `manifest.json` without requiring `cloudlabs_edge_dev`.
- Operator **Accept · good enough** (Twin) → edge `/jobs/{id}/accept` → session `early_stopped` / `operator_accept` (success, keep best). Distinct from `/cancel` (abort).
- `capabilities.features.torchscript_execution` matches real torch import.
- Session packages: digest verify; refuse packaging premade ids.
- Synthetic Gaussian tests: `backend/tests/test_builtin_kernels.py` (coord repo).

## 3. Actuate

**Symptoms:** `actuate.ok=false`, `refused: clearance|max_delta_from_start`, motors don't move, settle wrong.

**Check**

- Continuous: `assert_optical_path_clear` — `is_physically_holding` must be false; `clear_for_measure`.
- `max_delta_from_start` limits vs x0 (deg/mm) — oversized candidates get penalty, **no** apply.
- Abort: `keep_best` vs `rollback_on_fail` → settle apply of best / x0.
- Router block lifecycle: enter → apply_eval → exit; status BUSY only inside block.

## Quick commands

```bash
cloudlabs-edge doctor --path .
cloudlabs-edge certify <edge-url> --path . --profile hardware
```

Hardware profile checks OPTIMIZE declared, pipeline schema dry-run, `GET /kernels`.

## Twin-side companion

Coordinator skill: `.cursor/skills/cloudlabs-optimization` (compile, Wiki catalog,
UI presets, job stream → stage debug). Edge never imports that code.
"""

FRAMES_CALIBRATION_SKILL = """\
---
name: frames-calibration
description: >-
  Owns table-to-robot frame transforms on a Cloud Labs edge with fail-loud
  calibration. Use when implementing MOVE_COMPONENT, teleop pose conversion,
  or wiring LAB_ROBOT_* calibration constants.
---

# Frame calibration

## Do

- Keep table/lab millimetres and degrees as the **only** language Twin/SDK speak.
- Convert to the robot frame inside the edge (e.g. `runtime/frames.py`) at the last moment.
- Fail loud (`FrameCalibrationUnavailable` or equivalent) when a real transform is
  needed and calibration is missing.
- Allow identity only via explicit opt-in (mock / `LAB_ROBOT_FRAME_IDENTITY=1`).
- Report calibration status on `GET /bench` without raising.

## Do not

- Do not silently default a real arm to identity.
- Do not push robot-frame coordinates into the coordinator or SDK.
- Do not scatter rotation/origin/sign constants across adapters — one module owns them.

## Env / configure

Typical names: `LAB_ROBOT_TABLE_ROTATION_RAD`, `LAB_ROBOT_ORIGIN_X_MM` / `_Y_MM`,
`LAB_ROBOT_SIGN_X` / `_Y`, `LAB_ROBOT_RZ_OFFSET_DEG`. Prefer `frames.configure(...)`
in tests; production should set real lab constants.
"""

INVENTORY_HONESTY_SKILL = """\
---
name: inventory-honesty
description: >-
  Keeps store, place, inventory, and runtime-sync primitives honest on a Cloud
  Labs edge. Use when implementing STORE/PLACE, RECORD_TUNABLES, SYNC_RUNTIME,
  LOCALIZE_COMPONENTS (alias), or any claim about where components live / what
  tunables are.
---

# Inventory honesty

## Do

- Treat `data/library.json` + `data/inventory.json` as the source of truth for
  what the lab owns and what is currently tracked (`GET /library`, `GET /inventory`).
- Implement inventory verbs only when the lab stack can actually track storage.
- Until then, keep scaffold `NotImplementedError` → `refused` / `NOT_IMPLEMENTED`.
- Prefer confirming holding/presence from real session + gripper/vision state over guesses.
- Document in capabilities what you truly support; do not advertise inventory you cannot enforce.
- Declare per-tunable metadata in the library (authored JSON — doctor/load
  **fail hard**; nothing silently backfills missing capabilities):
  - `recordable: true` → `RECORD_TUNABLES` may overwrite from the world
    (pose writes **`nominal_pose`**, never a shadow `reported_pose`).
  - `recordable: false` → **`set_at_init` is required**; SYNC sets that value.
- When `features.runtime_sync` is true, `RECORD_TUNABLES` + `SYNC_RUNTIME` must
  be in `supported_primitives`, and every non-`nominal_pose` tunable must declare
  `recordable` explicitly (doctor strict mode).
- Wire `RECORD_TUNABLES` (and `SYNC_RUNTIME` = RECORD(recordable) + SET(set_at_init)
  for inventory tags). Edge is not READY until SYNC completes.
- If the host is constructed **inside a running asyncio loop** (coordinator /
  uvicorn in-process), **schedule** boot `SYNC_RUNTIME` with
  `loop.create_task(...)` — do not leave `runtime_sync` stuck at `pending`
  ("deferred forever"). Outside a loop, `asyncio.run(...)` is fine.
- Log with prefix `[lab_init]` when scheduling / skipping / failing boot SYNC.
- Mock/sim may implement RECORD as copy-from-state/JSON; real edges must measure.
- `LOCALIZE_COMPONENTS` is a **deprecated alias** of RECORD for `nominal_pose` only;
  prefer RECORD_TUNABLES / SYNC_RUNTIME. Default inventory scope: `localize != false`
  and `placement` in `{table, storage}`.

## Do not

- Do not fake "stored" / "placed" success to make certify green.
- Do not invent slot maps or AprilTag locations without a source of truth.
- Do not silently no-op STORE/PLACE — refusal is better than a lie.
- Do not keep a separate edge `active_catalog.json`; active tags are inventory keys.
- Do not put fixed overview / stereo / scan cameras in inventory merely so they
  “initialize” — scene localization cameras are usually bench infrastructure
  (see `camera-bringup`), not science-camera inventory bring-up.
- Do not push library/inventory/layout into the coordinator’s
  `coordinator_data/` tree — that store is VC + working FSM only; lab people
  author library/inventory here on the edge.
- Do not invent `reported_*` shadow tunables; recording overwrites the real tunable.
- Do not advertise READY / accept motion before SYNC_RUNTIME has succeeded.
- Do not leave boot SYNC "deferred" when an event loop is already running —
  Twin will sit on Initializing lab… forever.
- Do not silently backfill / infer missing library `capabilities` or `recordable`
  metadata at load time — fix the authored JSON so doctor fails for the real cause.

## Related

- Holding confirmation (e.g. `CONFIRM_HOLDING_TAG`) may use session state + gripper
  when that is all the hardware exposes — say so in the result (`source` field).
- Design brief: Cloud Labs `docs/RECORD_TUNABLES_AND_SYNC_RUNTIME.md`.
"""


def skill_file_map() -> dict[str, str]:
    """Map relative edge paths → file contents for ``init``."""
    return {
        ".agents/README.md": AGENTS_README,
        ".agents/skills/cloudlabs-context/SKILL.md": CLOUDLABS_CONTEXT_SKILL,
        ".agents/skills/edge-contract/SKILL.md": EDGE_CONTRACT_SKILL,
        ".agents/skills/measurables-tensors/SKILL.md": MEASURABLES_TENSORS_SKILL,
        ".agents/skills/camera-bringup/SKILL.md": CAMERA_BRINGUP_SKILL,
        ".agents/skills/latency-channels/SKILL.md": LATENCY_CHANNELS_SKILL,
        ".agents/skills/kernels-optimize/SKILL.md": KERNELS_OPTIMIZE_SKILL,
        ".agents/skills/optimize-pipeline-debug/SKILL.md": OPTIMIZE_PIPELINE_DEBUG_SKILL,
        ".agents/skills/tunables-hold-for-measure/SKILL.md": TUNABLES_HOLD_FOR_MEASURE_SKILL,
        ".agents/skills/frames-calibration/SKILL.md": FRAMES_CALIBRATION_SKILL,
        ".agents/skills/inventory-honesty/SKILL.md": INVENTORY_HONESTY_SKILL,
    }


def write_agent_skills(edge_root: Path) -> None:
    """Write the standard ``.agents/skills`` tree under ``edge_root``."""
    root = edge_root.resolve()
    for rel, content in skill_file_map().items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
