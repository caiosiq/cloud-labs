# RECORD_TUNABLES, set_at_init, and SYNC_RUNTIME

**Branch:** `josh/record-tunables-sync-runtime`  
**Status:** design locked for implementation; Phase 2 must not blindly overwrite lab-automation fixes  
**Audience:** cloud-labs maintainers + anyone merging with the lab PC / deathray collision work  

This document is the conflict-resolution brief. When reconciling with lab changes (especially collision solving on the real edge), read this first and prefer these contracts over ad-hoc `reported_pose` / boot-scan shims.

---

## 1. Problem we are fixing

Real edges could advertise health / serve lab-state **without** seeding tunables from the physical world. Mock and simulation hid the gap: their worlds already contain poses at spawn. Inventory answered “which tags exist,” not “what are the tunable values right now.”

`LOCALIZE_COMPONENTS` + parallel `reported_pose` were a partial answer that split “commanded” and “reported” pose. The correct model is one tunable value, seeded or overwritten from truth when recording.

---

## 2. Mental model (normative)

### 2.1 `RECORD_TUNABLES`

- Args: one or more `(tag_id, tunable_path)` pairs (batching allowed).
- Effect: for each pair whose schema says `recordable: true`, **measure** the physical/world value and **overwrite** that tunable in runtime.
- Pose recording writes **`nominal_pose`**, not a shadow `reported_pose`.
- Callable **any time** after READY (and during SYNC).
- Refuse if the tunable is not `recordable`.

### 2.2 Non-recordable tunables → `set_at_init`

- Every tunable with `recordable: false` (or omitted → treat as **not** recordable unless we default pose to true) **must** declare `set_at_init: <value>` in the component/library schema.
- At sync time those tunables are **set** to that standard value (not measured).
- Validation: library/catalog doctor fails if a non-recordable tunable lacks `set_at_init`.

### 2.3 `SYNC_RUNTIME` (macro)

Conceptual expansion (implementation may fold steps; the meaning is this composition):

```text
SYNC_RUNTIME  ≡
    for each inventory component C:
        for each tunable T on C:
            if T.recordable:
                RECORD_TUNABLES(C, T)     # overwrite from world
            else:
                SET_TUNABLE(C, T, T.set_at_init)   # standard seed
```

- **Must run successfully once** before the edge is READY / the lab is usable for motion.
- Mock/sim may implement RECORD as “copy current world/JSON into the tunable” and SET as writing `set_at_init`.
- Real edge implements RECORD via scan/encoders/etc.; SET still applies schema defaults.

### 2.4 READY gate

| Phase | Meaning |
|-------|---------|
| Starting | Process up; `/health` may be ok; **not** READY for motion |
| Syncing | `SYNC_RUNTIME` in progress |
| Ready | Sync succeeded; motion / hard checkout allowed |
| Sync failed | Stay not-ready; refuse MOVE/STORE/… with an honest reason |

Coordinator should not treat an attached HTTP edge as fully usable until readiness says synced (exact wire field TBD: e.g. lab-state `runtime_sync: {status, completed_at}` or capabilities flag).

### 2.5 No `reported_pose` tunable

- Delete / stop inferring `PoseReadout` + `tunables.reported_pose`.
- Live teleop streams may still *stream* samples; they do not invent a second catalog tunable.
- Legacy `LOCALIZE_COMPONENTS` → deprecated **alias** of `RECORD_TUNABLES` for `nominal_pose` only (migration window).

---

## 3. Schema sketch

Per tunable under library / inferred capabilities:

```json
"nominal_pose": {
  "widget": "TablePose",
  "recordable": true
},
"exposure_time_ms": {
  "widget": "FloatRange",
  "recordable": true
},
"open_loop_knob": {
  "widget": "FloatRange",
  "recordable": false,
  "set_at_init": 0.0
}
```

Rules:

1. `recordable: true` → must be implementable by `RECORD_TUNABLES` on that edge.  
2. `recordable: false` → `set_at_init` **required**.  
3. `SYNC_RUNTIME` only touches inventory-scoped components (same membership idea as today’s localize scope; inventory `localize: false` may still opt a tag out of RECORD during sync — decide in Phase 1 and document in EDGE_LIBRARY).

---

## 4. Phased work (this branch)

### Phase 1 — Language + schema (cloud-labs) — **start here**

- Add `RECORD_TUNABLES`, `SYNC_RUNTIME` to `PrimitiveId` / registry / schemas / dispatch.
- Macro module: sync expands to RECORD + SET (`set_at_init`) paths.
- Catalog: `recordable` + `set_at_init` validation; remove `PoseReadout` / `reported_pose` inference.
- Domain write: recording pose → `nominal_pose`.
- Edge contract JSON schemas under `schemas/edge_contract/v1/`.

### Phase 2 — Edges (careful)

- **mock + sim first** on this branch.
- **deathray / lab-automation:** do **not** blindly overwrite lab PC fixes (boot scan, collision work).  
  - Port contract surface (`RECORD` / `SYNC` / READY) behind clear adapters.  
  - Treat lab-local boot localize as a candidate *implementation* of `SYNC_RUNTIME` RECORD half, not a parallel API.  
  - See §6 conflict guide.

### Phase 3 — Coordinator + Twin + SDK

- Refresh-pose UX → RECORD / SYNC wrappers.
- Attach / READY awareness.
- Collapse dual-pose UI; SDK helpers `record_tunables` / `sync_runtime`.

### Phase 4 — Docs + fixtures + skills

- Onboarding primitive table, `EDGE_LIBRARY_AND_INVENTORY.md`, wiki primitives.
- Migrate `lab_state.json` fixtures off `reported_pose`.
- Skills: extend `inventory-honesty` + light `cloudlabs-context` (see §5).

---

## 5. Skills (prefer update)

### Extend `inventory-honesty`

Source of truth: `packages/cloudlabs_edge_dev/src/cloudlabs_edge_dev/agent_skills.py` → edges’ `.agents/skills/inventory-honesty/`.

Add:

- Tunables declare `recordable` or `set_at_init`.
- Wire `RECORD_TUNABLES` (overwrite; pose → `nominal_pose`).
- `SYNC_RUNTIME` = RECORD(recordable) + SET(set_at_init); required before READY.
- Mock/sim may copy JSON/state for RECORD.
- Do not invent `reported_*` shadow tunables.
- `LOCALIZE_COMPONENTS` only as temporary alias.

### Lightly extend `cloudlabs-context`

One orientation line: edge READY means `SYNC_RUNTIME` completed, not merely `/health`.

Do **not** create a separate skill unless `inventory-honesty` becomes unreadable.

---

## 6. Merging with the lab (conflict guide for you + Codex)

Lab side (expected hotspots — verify on lab `git status` / log):

- Boot-time scan / get-pose of inventory (physical sync you already fixed).
- Collision / path planning around reconcile or radial motion.
- Possible edits under `robot-deathray/cloudlabs_edge/` and lab_automation OpticalExperiment.

**When merging this branch into / with lab work:**

| Conflict area | Prefer |
|---------------|--------|
| Boot scan that fills poses | Keep lab’s **measurement** code; wrap it as `RECORD_TUNABLES` / `SYNC_RUNTIME` RECORD half |
| Writing `reported_pose` | Retarget writes to **`nominal_pose`**; drop dual field |
| New collision ordering / planner | Keep lab collision logic; do not delete for sync-runtime |
| New primitives in capabilities/dispatch | Union: add RECORD/SYNC **and** keep lab primitives |
| READY / health | READY only after SYNC; health can stay “process up” |
| Mock/sim-only files | Prefer this branch’s contract implementation |

**Do not** reimplement lab collision solves in cloud-labs mock.  
**Do** make the contract so lab boot sync becomes the real edge’s `SYNC_RUNTIME`.

Suggested merge checklist:

1. Diff lab deathray vs `origin/josh/radial-motion-on-cloudlabs-edge` (or whatever lab pushed).  
2. List files that touch scan / pose / localize / collision.  
3. For each: classify as (a) measurement for RECORD, (b) collision, (c) unrelated.  
4. Re-bind (a) to RECORD/SYNC; leave (b) intact; merge (c) normally.

---

## 7. Primary file touch list (Phase 1 focus)

- `backend/lab_model/language/primitives/{ids,registry,schemas,dispatch}.py`
- `backend/lab_model/language/primitives/macros/` (new sync_runtime)
- `backend/lab_model/language/domain/component.py`
- `backend/lab_model/coordinator/catalog/schema.py`
- `schemas/edge_contract/v1/{library,capabilities,inventory}.schema.json`
- Later phases: `backend/main.py` refresh-pose, mock/sim edges, frontend pose-refresh, SDK, `agent_skills.py`

---

## 8. Decisions locked in Phase 1

1. **`recordable` omitted:** `nominal_pose` defaults **true**; other fields default **false** for SYNC planning. Catalog validation **hard-fails** only when `recordable: false` lacks `set_at_init`; omitted `recordable` on legacy fields is a **warning**.  
2. **Inventory scope for SYNC:** tags with `localize: false` are skipped; `placement` in `{table, storage}` (same as LOCALIZE defaults). Library `recordable` decides RECORD vs SET per tunable.  
3. **READY wire field:** Phase 3 — prefer lab-state / agent `runtime_sync: {status}` and optional capabilities `features.runtime_sync`. Phase 1 stamps via optional `lab.set_runtime_sync_status` when present.  
4. **SET during SYNC:** uses existing setters (`SET_EXPOSURE`, `SET_MOTOR_SETPOINT`, `SET_LASER_OUTPUT`, nominal_pose apply) — not a new SET_TUNABLE primitive yet.

## 8b. Phase 1 landed (this branch)

- `PrimitiveId.RECORD_TUNABLES` / `SYNC_RUNTIME`; LOCALIZE is a macro alias → RECORD(`nominal_pose`)
- Schemas + dispatch + `macros/sync_runtime.py`
- Catalog inference: `recordable: true` on pose/motors/exposure; no new `reported_pose`
- Edge contract library/capabilities schema notes; skills updated
- Tests: `backend/tests/test_sync_runtime_plan.py`

**Phase 2 landed (mock/sim):**
- Edge `data/library.json`: explicit `recordable` / `set_at_init` (no silent backfill)
- `capabilities.json`: `RECORD_TUNABLES`, `SYNC_RUNTIME`, `features.runtime_sync`
- Host boot runs `SYNC_RUNTIME`; lab-state exposes `runtime_sync.status`
- Dispatch refuses motion/teleop until `runtime_sync.status == ready`
- Mock scan / RECORD write **`nominal_pose`** (not `reported_pose`)
- Escape hatch: `CLOUDLABS_SKIP_RUNTIME_SYNC=1`

**Phase 2 deathray landed (careful wrap):**
- `robot-deathray/cloudlabs_edge`: capabilities + library annotations
- `adapters/runtime_ready.py` + dispatch gate
- `RECORD_TUNABLES` / `SYNC_RUNTIME` wrap `scan_components_cloudlab` → **`nominal_pose`**
- Boot SYNC on FastAPI startup; lab-state exposes `runtime_sync`
- Radial / clearance / motion adapters left intact

**Phase 3 (coordinator refuse + Twin overlay) landed:**
- Coordinator hard-refuses gated primitives until `runtime_sync.status=ready` (fail-closed if missing)
- `GET /api/lab-state` exposes derived `lab_initialization`
- Twin blocking overlay: **Initializing lab…** (phases from composed flags)
- Mock/sim boot holds `running` ~2s (`CLOUDLABS_MOCK_INIT_DELAY_S`, default `2`; tests use `0`)

**Twin Refresh Pose → RECORD (landed):**
- Twin apply path: `POST /api/command` `RECORD_TUNABLES` (`tunable_paths: ["nominal_pose"]`)
- Offers GET remains preview-only; legacy `POST /api/lab-state/refresh-pose` wraps the same RECORD

**SDK helpers (landed):**
- `lab.record_tunables` / `lab.record_nominal_poses` / `lab.sync_runtime`
- `lab.lab_initialization` / `lab.wait_until_lab_ready`
- Fluent: `lab.components.tag_X.record_pose()` / `.record_tunables(...)`

**Still later:** real lab smoke.

---

## 9. Debug lines (must land with the feature)

Prefix: **`[lab_init]`** (edges + coordinator logger `lab_init` + Twin `console.info`).

- Edge boot: `runtime_sync status=pending|running|ready|failed … errors=N`
- Per RECORD: `RECORD_TUNABLES tag=… path=… ok|refuse`
- Edge refuse: `refuse primitive=… runtime_sync=… (lab not initialized)`
- Coordinator: status-change only on heartbeat / `GET /api/lab-state` via `lab_initialization.note_lab_state`
- Coordinator schedule: `schedule action=… REFUSED|ok` → HTTP 409 `lab_not_initialized`
- Twin: `[lab-init] phase=… ready=…` on change only (not every poll) + blocking overlay
- Doctor: non-recordable tunable missing `set_at_init` → fail

---

*Last updated: coordinator hard refuse + Twin lab-init overlay.*
