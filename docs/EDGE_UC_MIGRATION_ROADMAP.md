# Migration roadmap — UC Edge Contract (no implementation yet)

**Status:** planning only  
**Depends on:** [`EDGE_CONTRACT_AND_UC_LIVE_PLANE.md`](./EDGE_CONTRACT_AND_UC_LIVE_PLANE.md)  
**Order principle (yours):** freeze language → fix cloud-labs middle → set language skeleton on lab-automation → only then wire real hardware adapters.

This roadmap names **phases**, **deliverables**, and **likely files**. Paths are current-repo best estimates; names may shift slightly when we implement, but the ownership should not.

---

## North-star sequence (one page)

```text
Phase 0   Freeze UC + Edge Contract v1 (docs + JSON schemas only)
    ↓
Phase 1   Developer kit: `cloudlabs-edge` package (scaffold + conformance tests)
    ↓
Phase 2   Mock edge implements contract (reference gold standard)
    ↓
Phase 3   Coordinator speaks only contract southbound (middle-man cleanup)
    ↓
Phase 4   Twin + SDK use same client language (aliases OK, side doors die)
    ↓
Phase 5   Lab repo: empty `cloudlabs_edge/` + capabilities stub (language only)
    ↓
Phase 6   Lab repo: adapters map contract → existing OpticalExperiment / recorder / teleop
    ↓
Phase 7   Retire in-tree RealLabCommunicator imports; registry points at edge URL
    ↓
Phase 8   Latch/epoch + stream binding polish; MoveIt stays behind lab adapters
```

**Do not** jump to Phase 6/7 because MoveIt is scary. Language-first is what makes MoveIt cheap later.

---

## Phase 0 — Freeze the language (docs + schemas, still no runtime rewrite)

### Goal

Everyone agrees what is legal to say. No new verbs without a primitive. No live bytes without a measurable/tunable channel binding.

### Deliverables

1. Treat §12 of `EDGE_CONTRACT_AND_UC_LIVE_PLANE.md` as **locked** (tunable live samples; coordinator proxy + LAN fallback; execute-only `EVAL_KERNEL`; bench monotonic `epoch_ms`; flat Tier A WS; hardware-aware latch).
2. Machine-readable **Edge Contract v1** schema files (new), including **flat** teleop WS message schemas (reject nested hot-path payloads).
3. Catalog rules for `wire` / `live_channel` / `requires_primitive` on measurables (spec text + schema).
4. Document latch policy fields: `latch_quality`, optional per-device `capture_latency_ms` when HW trigger unavailable.

### Likely files (cloud-labs)

| Action | Path |
|--------|------|
| Edit | [`docs/EDGE_CONTRACT_AND_UC_LIVE_PLANE.md`](./EDGE_CONTRACT_AND_UC_LIVE_PLANE.md) |
| Add | `schemas/edge_contract/v1/capabilities.schema.json` |
| Add | `schemas/edge_contract/v1/execute_request.schema.json` |
| Add | `schemas/edge_contract/v1/execute_response.schema.json` |
| Add | `schemas/edge_contract/v1/epoch_packet.schema.json` |
| Add | `schemas/edge_contract/v1/README.md` (human summary of endpoints) |
| Edit | [`backend/lab_model/catalog/schema.py`](../backend/lab_model/catalog/schema.py) — document/validate live_channel bindings (schema only first) |
| Edit | Wiki [`frontend/wiki/guides/04-primitives.md`](../frontend/wiki/guides/04-primitives.md) — “what is not a primitive” + wire vs analysis note |
| Edit | [`backend/lab_communicator/README.md`](../backend/lab_communicator/README.md) — point at contract; mark in-tree real as legacy path |

### Exit criteria

- A stranger can implement an edge from schemas + docs without reading `real/communicator.py`.
- Twin “needs feed” is answered only with `START_LIVE_FEED` + wire form of `camera_image`.

---

## Phase 1 — Developer kit: edge scaffold + automated conformance

### Goal

Lab teams get a **package** that creates `cloudlabs_edge/` and **checks** that an edge is self-consistent and Cloud-Labs-compatible — without cloud-labs importing their drivers.

### Deliverables

New distributable (name TBD; call it **`cloudlabs-edge-dev`** or part of `packages/cloudlabs`):

1. **Scaffold CLI** — `cloudlabs-edge init ./cloudlabs_edge` creates folder skeleton + stub handlers.  
2. **Conformance suite** — talks to a running edge URL:
   - `GET /capabilities` validates against schema
   - every `supported_primitives` entry is callable with dry-run or safe probe args (policy: mock mode / refuse destructive)
   - `START_LIVE_FEED` → stream URL returns JPEG-ish bytes; `END_LIVE_FEED` stops
   - `START_TELEOP` → WS accepts jog schema; `END_TELEOP` cleans up
   - `EVAL_KERNEL` / `RECORD_MEASURABLES` return `epoch_ms` (+ optional `latch_quality`) when required
   - Tier A WS fixtures are **flat-only** (conformance fails nested teleop payloads)
   - refuse test: unknown primitive → structured error
3. **Contract version pin** — edge must report `contract_version` cloud-labs accepts.

### Likely files (cloud-labs)

| Action | Path |
|--------|------|
| Add | `packages/cloudlabs_edge_dev/` (or `packages/cloudlabs/src/cloudlabs/edge_dev/`) |
| Add | `.../scaffold/templates/**` (main.py stub, adapters/, contract stubs) |
| Add | `.../conformance/test_capabilities.py` |
| Add | `.../conformance/test_execute_roundtrip.py` |
| Add | `.../conformance/test_live_feed_arming.py` |
| Add | `.../conformance/test_teleop_ws.py` |
| Add | `.../conformance/test_epoch_latch.py` |
| Add | `scripts/ops/edge_conformance.py` — CLI wrapper |
| Edit | [`packages/cloudlabs/README.md`](../packages/cloudlabs) / root docs — how labs run conformance |

### Exit criteria

- `cloudlabs-edge init` + stub server passes a “stub profile” of conformance.
- CI in cloud-labs runs conformance against **mock edge** (Phase 2).

---

## Phase 2 — Mock edge becomes the gold-standard contract implementation

### Goal

Teaching/CI path runs **as an edge process** implementing v1, not as “fat in-process communicator forever.” (In-process mock may remain as a fast-dev shortcut behind the same EdgeClient interface.)

### Deliverables

1. Refactor today’s mock path into something that exposes Edge Contract endpoints.  
2. Reuse existing mock physics (`mock/ensemble.py`, teleop sim, synthetic cameras) **behind** adapters.  
3. Conformance suite green on mock.

### Likely files (cloud-labs)

| Action | Path |
|--------|------|
| Heavy edit / split | [`scripts/ops/mock_edge_agent.py`](../scripts/ops/mock_edge_agent.py) — align with contract routes |
| Edit | [`backend/lab_communicator/mock/communicator.py`](../backend/lab_communicator/mock/communicator.py) |
| Edit | [`backend/lab_communicator/mock/primitives.py`](../backend/lab_communicator/mock/primitives.py) |
| Edit | [`backend/lab_communicator/mock/ensemble.py`](../backend/lab_communicator/mock/ensemble.py) |
| Add | `backend/lab_communicator/mock/edge_app.py` (or under `packages/…`) — FastAPI app: `/capabilities`, `/execute`, streams |
| Edit | [`backend/lab_communicator/shared/communicator_factory.py`](../backend/lab_communicator/shared/communicator_factory.py) — factory becomes “local mock” vs “remote edge client” |
| Edit | [`backend/lab_communicator/runtime_mode.py`](../backend/lab_communicator/runtime_mode.py) — MuJoCo remains overlay; document vs contract |
| Add tests | `backend/tests/test_edge_contract_mock.py` |

### Exit criteria

- Language scripts + Twin against mock work with coordinator → mock edge contract (even if temporarily dual-running).
- Conformance 100% on mock.

---

## Phase 3 — Coordinator middle-man: only UC southbound

### Goal

cloud-labs server stops being a special-case teleop/video host that bypasses the edge. All mutations go through primitives; all live bytes come from capability channels after arming.

### Deliverables

1. **`EdgeClient`** module used by coordinator for every southbound call.  
2. Backend registry gains `edge` connection info (URL, contract version).  
3. Dedicated Twin routes become **aliases** that only build primitive bodies + optionally attach to stream URLs from capabilities (no second semantics).  
4. When edge attached: Tier A/B terminate on edge; **coordinator proxies** streams by default (lightweight route-through / chunked), with optional LAN direct when discovery says same secure LAN.  
5. Latch/`epoch_ms` (+ `latch_quality`) required on observe/probe responses from edge.  
6. No `/kernel/probe` southbound — only `/execute` + `EVAL_KERNEL`.

### Likely files (cloud-labs)

| Action | Path |
|--------|------|
| Add | `backend/lab_model/edge/client.py` — execute, capabilities cache, stream **proxy** attach |
| Add | `backend/lab_model/edge/stream_proxy.py` — WS/MJPEG relay (no BGR re-encode) |
| Add | `backend/lab_model/edge/registry.py` — resolve backend → edge endpoint |
| Edit | [`schemas/backends.json`](../schemas/backends.json) — `edge: { base_url, contract_version, … }` |
| Edit | [`backend/lab_model/backends/`](../backend/lab_model/backends) (dispatch / RequestLab) |
| Edit | [`backend/main.py`](../backend/main.py) — `_proxy_to_edge`, teleop routes (~1393–1620), live-feed, MJPEG, WS, `/api/command`, lab-state |
| Edit | [`backend/lab_model/primitives/dispatch.py`](../backend/lab_model/primitives/dispatch.py) |
| Edit | [`backend/lab_model/primitives/registry.py`](../backend/lab_model/primitives/registry.py) — http hints point to command + stream attach |
| Edit | [`backend/lab_model/orchestration/teleop.py`](../backend/lab_model/orchestration/teleop.py) |
| Edit | [`backend/lab_model/orchestration/live_feed.py`](../backend/lab_model/orchestration/live_feed.py) |
| Edit | [`backend/lab_model/orchestration/teleop_session_ws.py`](../backend/lab_model/orchestration/teleop_session_ws.py) |
| Edit | [`backend/lab_model/orchestration/measurables_record.py`](../backend/lab_model/orchestration/measurables_record.py) — epoch |
| Edit | [`backend/lab_communicator/base.py`](../backend/lab_communicator/base.py) — shrink toward “in-process edge adapter” or EdgeClient-only |

### Explicit deprecation targets (behavior, not necessarily delete day-one)

| Today | Becomes |
|-------|---------|
| Twin-only teleop HTTP that skips edge | Alias → `START_TELEOP` / `TELEOP_*` via EdgeClient |
| Ad-hoc `get_video_feed_status` style helpers | Capability query + live_feed channel state |
| Coordinator-owned MJPEG from in-process real video when edge exists | Edge stream from capabilities |
| `/api/kernels/eval` as special snowflake | Alias → `EVAL_KERNEL` execute |

### Exit criteria

- With mock edge remote: teleop + live feed + command + OPTIMIZE all hit the same edge process.
- No coordinator code path calls lab_automation.

---

## Phase 4 — Clients: Twin ≡ SDK language

### Goal

UI is not a second product. Same primitives, same measurable resolve, same arming rules. Twin may hide kernel authoring UX; it may not invent feeds.

### Deliverables

1. Shared JS or generated client bindings that mirror SDK verb names where practical.  
2. Widgets bind only to capability channels after START_*.  
3. Lab-state poll documented as Tier C overview only.  
4. Language scripts gain a small “live plane” example later (optional; not blocking).

### Likely files (cloud-labs)

| Action | Path |
|--------|------|
| Edit | [`frontend/js/api/teleop.js`](../frontend/js/api/teleop.js) |
| Edit | [`frontend/js/api/teleop-session-ws.js`](../frontend/js/api/teleop-session-ws.js) |
| Edit | [`frontend/js/api/live-feed.js`](../frontend/js/api/live-feed.js) |
| Edit | [`frontend/js/api/teleop-live-pose.js`](../frontend/js/api/teleop-live-pose.js) |
| Edit | [`frontend/js/widgets/mjpeg-viewer.js`](../frontend/js/widgets/mjpeg-viewer.js) |
| Edit | [`frontend/js/widgets/jpeg-poll.js`](../frontend/js/widgets/jpeg-poll.js) |
| Edit | [`frontend/js/state/lab-state.js`](../frontend/js/state/lab-state.js) — overview vs live |
| Edit | [`frontend/js/ui/control-panel.js`](../frontend/js/ui/control-panel.js) if it assumes old routes |
| Edit | [`packages/cloudlabs/src/cloudlabs/client.py`](../packages/cloudlabs/src/cloudlabs/client.py) — execute + subscribe helpers; epoch on probe |
| Edit | Wiki guides 04, 05, 08/09 connecting — live plane story |
| Edit | [`scripts/language/README.md`](../scripts/language/README.md) |

### Exit criteria

- Any Twin teleop/live action has a one-line SDK equivalent using the same primitive names.
- Grep for undefined feed helpers in frontend finds none (or only deprecated shims).

---

## Phase 5 — Lab-automation: language skeleton only (no deep hardware rewrite)

### Goal

robot-deathray grows a `cloudlabs_edge/` that **speaks** the contract and passes a **stub/safe** conformance profile — still may refuse real motion — **before** you touch MoveIt or rewrite drivers.

### Deliverables (in lab repo, not cloud-labs)

```text
robot-deathray/cloudlabs_edge/
  main.py                 # serves contract; mostly NotImplemented/safe stubs
  contract_version.txt
  capabilities.json       # static or generated; lists planned primitives
  adapters/               # empty or echo stubs
  README.md               # “implement adapters next”
```

Run cloud-labs **conformance** against this stub (expect many “refused” that are still schema-valid, or a `profile: skeleton`).

### Likely files

| Repo | Action | Path |
|------|--------|------|
| robot-deathray | Add | `cloudlabs_edge/**` (scaffold from Phase 1 kit) |
| robot-deathray | Edit | root `README.md`, `CLOUDLAB_CONTRACT.md` — point to Edge Contract v1 |
| cloud-labs | Edit | [`schemas/backends.json`](../schemas/backends.json) — real.default `edge.base_url` for when stub runs |
| cloud-labs | Doc | this roadmap + communicator README “real path = external edge” |

### Exit criteria

- Lab repo compiles/runs edge stub independently.
- Conformance “skeleton profile” green (schema + refuse shape), without requiring motors to move.

**Stop here until language review is signed off.** Only then Phase 6.

---

## Phase 6 — Lab-automation: connect adapters to existing code

### Goal

Map UC primitives to **current** OpticalExperiment / recorder / LiveControlSession / wifi steppers. Still no MoveIt requirement.

### Deliverables

| Adapter | Maps to (deathray today) |
|---------|---------------------------|
| `adapters/motion.py` | `*_cloudlab` pick/hover/place/scan_rotate |
| `adapters/motors.py` | `wifi_stepper*.move_motor` |
| `adapters/vision.py` | recorder TCP CAP/GET_JPEG; BGR for kernels |
| `adapters/teleop.py` | `LiveControlSession` + pose publish |
| `kernel_host.py` | local TorchScript using edge BGR |
| `latch.py` | epoch bundling + HW trigger / latency compensation for RECORD/EVAL |

### Likely files

| Repo | Path |
|------|------|
| robot-deathray | `cloudlabs_edge/adapters/*.py` |
| robot-deathray | thin wrappers around `managers/experiment_manager.py`, `managers/live_control.py`, `managers/recorder_capture_helpers_cloudlab.py`, `scripts/recorder_cam_laser_align_cloudlab.py` |
| robot-deathray | update `CLOUDLAB_CONTRACT.md` for epoch + wire/analysis |
| cloud-labs | conformance “hardware profile” optional markers |
| cloud-labs | **shrink** [`backend/lab_communicator/real/`](../backend/lab_communicator/real/) to deprecated shim or delete imports |

### Explicit cloud-labs files to stop calling into lab_automation

| Path | Fate |
|------|------|
| `backend/lab_communicator/real/communicator.py` | Replace with EdgeClient-backed stub or remove |
| `backend/lab_communicator/real/primitives.py` | Logic moves to lab `adapters/` |
| `backend/lab_communicator/real/teleop_bridge.py` | → lab `teleop.py` |
| `backend/lab_communicator/real/video.py` | → lab `vision.py` |
| `backend/lab_communicator/real/ensemble.py` | → lab ensemble adapter or edge OPTIMIZE host |
| `backend/lab_communicator/real/coordinate_frames.py` | → lab only |
| `backend/lab_communicator/real/scan.py` | → lab motion/vision |
| `backend/lab_communicator/real/optimization.py` | → lab or drop legacy |

### Exit criteria

- Real bench: Twin teleop + live feed + move + RECORD + EVAL_KERNEL via edge URL.
- cloud-labs tree has **zero** `import lab_automation` / `OpticalExperiment`.
- Full conformance profile green on real (with safety gates).

---

## Phase 7 — Registry, packaging, retire legacy

### Goal

Backends are just registry rows; communicators are not a cloud-labs monorepo feature.

### Deliverables

1. `schemas/backends.json` (and runtime registry) fully describe edge endpoints.  
2. In-tree `real/` removed or reduced to “legacy unsupported.”  
3. Docs/Wiki Backends explain external edge.  
4. Offline wiki pack note: mock-only still fine.

### Likely files

| Action | Path |
|--------|------|
| Edit | [`schemas/backends.json`](../schemas/backends.json) |
| Edit | backend startup / [`backend/lab_model/backends/`](../backend/lab_model/backends) |
| Delete or archive | large parts of `backend/lab_communicator/real/` |
| Edit | [`backend/lab_communicator/README.md`](../backend/lab_communicator/README.md) |
| Edit | [`docs/LAB_SURFACES_VC_AND_INITIALIZATION.md`](./LAB_SURFACES_VC_AND_INITIALIZATION.md) |
| Edit | Wiki connecting / backends guides |

---

## Phase 8 — Hardening (sync, bandwidth, MoveIt insulation)

### Goal

Polish the live plane; MoveIt becomes an internal lab change.

### Deliverables

1. Epoch on all stream frames + **hardware-aware** latch on RECORD/EVAL (`latch_quality`).  
2. Teleop JPEG profile in capabilities (scale/Q/fps).  
3. Ban dual-poll science patterns in SDK helpers (one-shot latch helpers).  
4. Enforce flat Tier A WS in conformance (nested → fail).  
5. Lab motion adapter swapped to MoveIt **without** cloud-labs PR.

### Likely files

| Repo | Path |
|------|------|
| both | latch/epoch in edge + client display of age |
| robot-deathray | `adapters/motion.py` only for MoveIt |
| cloud-labs | none required for MoveIt |

---

## Cross-cutting: who does what when

| Phase | cloud-labs | Lab repo (deathray) | Dev kit |
|-------|------------|---------------------|---------|
| 0 | Specs/schemas/docs | Read-only consumer | — |
| 1 | Build kit + CI harness | — | Scaffold + conformance |
| 2 | Mock edge gold | — | Run vs mock |
| 3–4 | Coordinator + Twin + SDK | — | Keep green |
| 5 | Registry stub URL | **Skeleton edge** | Conformance skeleton |
| 6–7 | Remove real imports | **Adapters** | Conformance hardware |
| 8 | Minor client polish | MoveIt behind adapter | Regression |

---

## What we deliberately do *not* do early

- Rewrite lab_automation motion for MoveIt before Phase 5–6.  
- Add new Twin-only video endpoints “just for latency.”  
- Keep growing `RealLabCommunicator` as the integration surface.  
- Require every backend to implement every primitive on day one — **capabilities** + conformance profiles (skeleton / mock / hardware).

---

## Suggested milestone demos (for advisors / yourselves)

| After phase | Demo |
|-------------|------|
| 0–1 | Show schemas + `init` scaffold + failing conformance on empty folder |
| 2 | Mock edge process: Twin teleop via contract |
| 3–4 | Same script and Twin button → same primitive log line |
| 5 | Deathray stub edge passes skeleton conformance |
| 6 | Real camera JPEG + motor jog via deathray edge; cloud-labs has no lab_automation import |
| 8 | Latch demo: RECORD returns matching epoch for image handle + motors; MoveIt swap plan on one adapter file |

---

## Immediate next action (still no product code)

1. Phase 0 decisions are **locked** in `EDGE_CONTRACT_AND_UC_LIVE_PLANE.md` §12 — no further product debate needed to start schemas.  
2. Draft `schemas/edge_contract/v1/*.schema.json` including flat teleop WS schemas.  
3. Sketch package layout for `cloudlabs_edge_dev` on paper / this doc only.  
4. **Do not** open robot-deathray adapters until Phase 5.

When you want implementation to start, say which phase to begin (recommended: **Phase 0 schemas + Phase 1 package skeleton**).
