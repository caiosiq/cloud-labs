# Migration roadmap — UC Edge Contract

**Status:** Phase 0–1, 3–5 on `develop/caio-edge-contract` (+ `robot-deathray/cloudlabs_edge` scaffold); Phase 2 mock-as-edge process still open  
**Depends on:** [`EDGE_CONTRACT_AND_UC_LIVE_PLANE.md`](./EDGE_CONTRACT_AND_UC_LIVE_PLANE.md)  
**Order principle (yours):** freeze language → fix cloud-labs middle → set language skeleton on lab-automation → only then wire real hardware adapters.

This roadmap names **phases**, **deliverables**, and **likely files**. Paths are current-repo best estimates; names may shift slightly when we implement, but the ownership should not.

---

## North-star sequence (one page)

```text
Phase 0   Freeze UC + Edge Contract v1 (docs + JSON schemas only)
    ↓
Phase 1   Developer kit: `cloudlabs-edge` (scaffold + doctor + check + certify)
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

**Status (branch `develop/caio-edge-contract`):** **done** for schemas + Wiki/README pointers. Runtime validation in `catalog/schema.py` deferred to Phase 1+.

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
| Edit | [`backend/lab_model/coordinator/catalog/schema.py`](../backend/lab_model/coordinator/catalog/schema.py) — document/validate live_channel bindings (schema only first) |
| Edit | Wiki [`frontend/wiki/guides/04-primitives.md`](../frontend/wiki/guides/04-primitives.md) — “what is not a primitive” + wire vs analysis note |
| Edit | [`mock_backend/README.md`](../mock_backend/README.md) — teaching edge entrypoint |

### Exit criteria

- A stranger can implement an edge from schemas + docs without reading `real/communicator.py`.
- Twin “needs feed” is answered only with `START_LIVE_FEED` + wire form of `camera_image`.

---

## Phase 1 — Developer kit: edge scaffold + automated conformance

**Status (branch `develop/caio-edge-contract`):** **done** for package + stub profile. CI vs mock edge deferred to Phase 2.

### Goal

Lab teams get a **package** that creates `cloudlabs_edge/` and **checks** that an edge is self-consistent and Cloud-Labs-compatible — without cloud-labs importing their drivers.

### Deliverables

Distributable **`cloudlabs-edge-dev`** (`packages/cloudlabs_edge_dev/`):

1. **Scaffold CLI** — `cloudlabs-edge init ./cloudlabs_edge` creates folder skeleton + stub handlers.  
2. **Doctor** — `cloudlabs-edge doctor [--path ./cloudlabs_edge]`: kit version drift, schemas present, local tree/schema pin (no live edge).  
3. **Conformance suite** — talks to a running edge URL (`cloudlabs-edge check` / `scripts/ops/edge_conformance.py`):
   - `GET /capabilities` validates against schema
   - `START_LIVE_FEED` → stream URL returns JPEG-ish bytes; `END_LIVE_FEED` stops
   - `START_TELEOP` → WS accepts flat jog schema; `END_TELEOP` cleans up
   - `EVAL_KERNEL` / `RECORD_MEASURABLES` return `epoch_ms` (+ optional `latch_quality`)
   - Tier A WS fixtures are **flat-only** (nested teleop payloads must error)
   - refuse test: unknown primitive → structured error
4. **Certify** — `cloudlabs-edge certify <url> --out report.json`: doctor + conformance gate **before** coordinator `edge.base_url` registration.  
5. **Contract version pin** — edge must report `contract_version` `1.0.0`.
6. **Reference stub** — `cloudlabs-edge serve-stub` implements the contract for local checks.

### Files (cloud-labs)

| Action | Path |
|--------|------|
| Add | [`packages/cloudlabs_edge_dev/`](../packages/cloudlabs_edge_dev/) |
| Add | `packages/cloudlabs_edge_dev/src/cloudlabs_edge_dev/{cli,scaffold,stub_server,conformance,doctor,certify,schemas_path}.py` |
| Add | [`scripts/ops/edge_conformance.py`](../scripts/ops/edge_conformance.py) |
| Edit | [`packages/cloudlabs/README.md`](../packages/cloudlabs/README.md) — link to edge-dev |

### Exit criteria

- `cloudlabs-edge init` + `serve-stub` passes `--profile stub` conformance. **(met)**
- `cloudlabs-edge doctor` + `certify` gate a lab edge before coordinator registration. **(met)**
- CI in cloud-labs runs conformance against **mock edge** (Phase 2).

---

## Phase 2 — Mock edge becomes the gold-standard contract implementation

**Status:** **done** for teaching cutover — [`mock_backend/cloudlabs_edge/`](../mock_backend/cloudlabs_edge/) is the Edge Contract face (filled `init` skeleton); teaching physics stays in `mock_backend/src/mock_backend/host/`. In-tree `lab_communicator/mock` deleted. Default coordinator path remains **in-process** `MockLabCommunicator` via `EdgeClient`; optional `python -m mock_backend` serves `cloudlabs_edge` on `:8100`.

**Simulation:** [`simulation_edge/cloudlabs_edge/`](../simulation_edge/cloudlabs_edge/) (`sim.default`) ports Josh’s MuJoCo process host into the same bookkeeping layout; soft pose mode by default, `SIMULATION_EDGE_MUJOCO=1` for the viewer. Serve with `python -m simulation_edge` on `:8120`.

### Goal

Teaching/CI path runs **as an edge process** implementing v1, not as “fat in-process communicator forever.” (In-process mock may remain as a fast-dev shortcut behind the same EdgeClient interface.)

### Deliverables

1. Refactor today’s mock path into something that exposes Edge Contract endpoints. **(met — `mock_backend/server/app.py`)**  
2. Reuse existing mock physics (`ensemble`, teleop sim, synthetic cameras) **behind** host adapters. **(met — `mock_backend/host/`)**  
3. Conformance suite green on mock. **(target: `cloudlabs-edge check http://127.0.0.1:8100 --profile stub`)**

### Likely files (cloud-labs)

| Action | Path |
|--------|------|
| Add | [`mock_backend/`](../mock_backend/) — `cloudlabs_edge/` contract face + `src/mock_backend/host/` + `lab_view/` |
| Edit | [`scripts/ops/mock_backend_agent.py`](../scripts/ops/mock_backend_agent.py) — poll-attach jobs; prefer `python -m mock_backend` |
| Edit | [`schemas/backends.json`](../schemas/backends.json) — `mock.default` → `mock_backend/lab_view` |
| Delete | `backend/lab_communicator/{mock,real,mujoco}/`, `base.py`, factory |

### Exit criteria

- Language scripts + Twin against mock work with coordinator → mock edge (in-process or HTTP).
- Conformance green on `python -m mock_backend`.

---

## Phase 3 — Coordinator middle-man: only UC southbound

**Status (branch `develop/caio-edge-contract`):** **done** for EdgeClient + Twin alias wiring + HTTP stream proxy. Phase-2 mock-as-edge process still optional; default remains in-process. TeleOp duplex WS still coordinator-hosted until edge capabilities expose WS (Phase 3b / 4).

### Goal

cloud-labs server stops being a special-case teleop/video host that bypasses the edge. All mutations go through primitives; all live bytes come from capability channels after arming.

### Deliverables

1. **`EdgeClient`** module used by coordinator for southbound mutations (poll > HTTP `edge.base_url` > in-process).  
2. Backend registry gains `edge` connection info (URL, contract version, `lan_direct_ok`).  
3. Dedicated Twin routes (teleop / live-feed / kernels/eval / command) are **aliases** that build primitive bodies and call EdgeClient.  
4. When HTTP edge configured: Tier B streams **proxied** by coordinator (no BGR re-encode); `lan_direct_ok` reserved for later LAN shortcut.  
5. `epoch_ms` / `latch_quality` surfaced from edge execute results when present.  
6. No `/kernel/probe` southbound — only `/execute` + `EVAL_KERNEL` (via EdgeClient).

### Files (cloud-labs)

| Action | Path |
|--------|------|
| Add | [`backend/lab_model/execution/edge/client.py`](../backend/lab_model/execution/edge/client.py) |
| Add | [`backend/lab_model/execution/edge/stream_proxy.py`](../backend/lab_model/execution/edge/stream_proxy.py) |
| Add | [`backend/lab_model/execution/edge/endpoint.py`](../backend/lab_model/execution/edge/endpoint.py) |
| Edit | [`schemas/backends.json`](../schemas/backends.json) — `edge: { base_url, contract_version, lan_direct_ok }` |
| Edit | [`backend/lab_model/coordinator/backends/registry.py`](../backend/lab_model/coordinator/backends/registry.py) |
| Edit | [`backend/main.py`](../backend/main.py) — `_southbound_execute`, teleop/live-feed/stream/command/eval |
| Add | [`backend/tests/test_edge_client.py`](../backend/tests/test_edge_client.py) |

### Explicit deprecation targets (behavior, not necessarily delete day-one)

| Today | Becomes |
|-------|---------|
| Twin-only teleop HTTP that skips edge | Alias → `START_TELEOP` / `TELEOP_*` via EdgeClient |
| Ad-hoc `get_video_feed_status` style helpers | Capability query + live_feed channel state |
| Coordinator-owned MJPEG from in-process real video when edge exists | Edge stream from capabilities (proxied) |
| `/api/kernels/eval` as special snowflake | Alias → `EVAL_KERNEL` via EdgeClient |

### Exit criteria

- With HTTP edge or poll-attached agent: teleop + live feed + command + EVAL hit the same EdgeClient path. **(met for poll/HTTP/in-process resolve)**
- No coordinator code path calls lab_automation. **(still true — only `lab_communicator/real/`)**
- Full OPTIMIZE + duplex TeleOp WS on remote edge: Phase 2 mock edge + Phase 3b.

---

## Phase 4 — Clients: Twin ≡ SDK language

**Status (branch `develop/caio-edge-contract`):** **done** for shared JS client + Python live-plane verbs + Tier C docs. Widgets already gated on `START_*`; TeleOp duplex WS remains a transport under `teleopGoto` (not a second language).

### Goal

UI is not a second product. Same primitives, same measurable resolve, same arming rules. Twin may hide kernel authoring UX; it may not invent feeds.

### Deliverables

1. Shared JS client (`frontend/js/cloudlabs`) mirroring SDK verb names.  
2. Widgets bind only to capability channels after START_* (enforced + documented).  
3. Lab-state poll documented as Tier C overview only.  
4. Language script `07_live_plane.py` + Python `start_live_feed` / `start_teleop` / …

### Files (cloud-labs)

| Action | Path |
|--------|------|
| Add | [`frontend/js/cloudlabs/client.js`](../frontend/js/cloudlabs/client.js), `index.js` |
| Edit | [`frontend/js/api/live-feed.js`](../frontend/js/api/live-feed.js), [`teleop.js`](../frontend/js/api/teleop.js) |
| Edit | [`frontend/js/primitives/record-measurables.js`](../frontend/js/primitives/record-measurables.js) |
| Edit | [`frontend/js/state/lab-state.js`](../frontend/js/state/lab-state.js) — Tier C |
| Edit | [`packages/cloudlabs/src/cloudlabs/client.py`](../packages/cloudlabs/src/cloudlabs/client.py) — live plane verbs |
| Edit | Wiki [`04-primitives.md`](../frontend/wiki/guides/04-primitives.md) |
| Add | [`scripts/language/07_live_plane.py`](../scripts/language/07_live_plane.py) |

### Exit criteria

- Any Twin teleop/live action has a one-line SDK equivalent using the same primitive names. **(met)**
- Grep for undefined feed helpers in frontend finds none (or only deprecated shims). **(met — wiki names `get_feed` as forbidden only)**

---

## Phase 5 — Lab-automation: language skeleton only (no deep hardware rewrite)

**Status:** **done** — scaffolded with `cloudlabs-edge init` into `robot-deathray/cloudlabs_edge/` (no OpticalExperiment / driver imports). HTTP handlers = `cloudlabs_edge_dev.stub_server`; `adapters/` are Phase-6 placeholders.

### Goal

robot-deathray grows a `cloudlabs_edge/` that **speaks** the contract and passes a **stub/safe** conformance profile — still may refuse real motion — **before** you touch MoveIt or rewrite drivers.

### Deliverables (in lab repo, not cloud-labs)

```text
robot-deathray/cloudlabs_edge/
  main.py, dispatch.py, latch.py, kernel_host.py, contract.py, SKELETON.md
  capabilities.json       # full planned supported_primitives
  adapters/               # motion, motors, vision, live_feed, teleop, tunables, optimize, observe
  bench/layout.json
```

(`cloudlabs-edge init` writes the full function skeleton; Phase 5 HTTP = reference stub.)

Generated by::

```powershell
pip install -e ./packages/cloudlabs_edge_dev
cloudlabs-edge init ../robot-deathray/cloudlabs_edge --backend-id real.default --force
```

### Files

| Repo | Action | Path |
|------|--------|------|
| robot-deathray | Add | `cloudlabs_edge/**` via `cloudlabs-edge init` |
| robot-deathray | Edit | root `README.md`, `CLOUDLAB_CONTRACT.md` — Edge Contract pointer |
| cloud-labs | Edit | [`schemas/backends.json`](../schemas/backends.json) — `real.default.edge` + notes for `:8100` |
| cloud-labs | Edit | `packages/cloudlabs_edge_dev` scaffold text for Phase 5/6 |

### Exit criteria

- Lab repo runs edge independently (`uvicorn main:app --port 8100`). **(scaffold ready)**
- Conformance `skeleton` (and reference `stub`) green without motors. **(verify locally below)**

**Stop here until language review is signed off.** Only then Phase 6.

---

## Phase 6 — Lab-automation: connect adapters to existing code

### Goal

Map UC primitives to **current** OpticalExperiment / recorder / LiveControlSession / wifi steppers. Still no MoveIt requirement.

### Deliverables

| Adapter | Maps to (deathray today) |
|---------|---------------------------|
| `adapters/motion.py` | `*_cloudlab` pick/hover/place |
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

**Status (partial):** In-tree `real/` / `mock/` / `mujoco/` **deleted**. Coordinator boots mock via `mock_backend`; `real.default` unavailable until `edge.base_url` points at lab `cloudlabs_edge`.

### Goal

Backends are just registry rows; communicators are not a cloud-labs monorepo feature.

### Deliverables

1. `schemas/backends.json` (and runtime registry) fully describe edge endpoints. **(in progress)**  
2. In-tree `real/` removed or reduced to “legacy unsupported.” **(met — deleted)**  
3. Docs/Wiki Backends explain external edge.  
4. Offline wiki pack note: mock-only still fine.

### Likely files

| Action | Path |
|--------|------|
| Edit | [`schemas/backends.json`](../schemas/backends.json) |
| Edit | backend startup / [`backend/lab_model/coordinator/backends/`](../backend/lab_model/backends) |
| Delete | `backend/lab_communicator/{real,mock,mujoco}/` **(done)** |
| Edit | [`mock_backend/README.md`](../mock_backend/README.md) |
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
