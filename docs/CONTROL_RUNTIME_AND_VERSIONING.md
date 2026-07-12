# Control, runtime, and version control — design proposition

**Status:** proposal (brainstorming — no implementation yet)  
**Last updated:** 2026-06-24  
**Audience:** cloud-labs maintainers, UROP, anyone designing experiment workflows  

**Canonical doc** for: Universal Component naming (Configuration / Observations / Setup / Runtime), **RuntimeManager**, **ControlManager** (version history — *not* `lab_automation`'s experiment manager), and phased implementation.

**Related docs:**

- [`LAB_SURFACES_VC_AND_INITIALIZATION.md`](./LAB_SURFACES_VC_AND_INITIALIZATION.md) — local VC (this doc) vs remote catalog pins; Twin UI vs Operations
- [`universal_component_architecture.md`](./universal_component_architecture.md) — UC model, glossary extension, Phase 10+ roadmap hook
- [`ENSEMBLE_OPTIMIZATION.md`](./ENSEMBLE_OPTIMIZATION.md) — generalized multi-variable `OPTIMIZE` (variables / objectives / solvers), normalization, touch-and-go, implementation roadmap
- [`../backend/lab_model/ARCHITECTURE.md`](../backend/lab_model/ARCHITECTURE.md) — current platform map (managers = planned)
- [`../backend/lab_model/README.md`](../backend/lab_model/README.md) — tunables, measurables, telemetry today
- [`primitive_ui_contract.md`](./primitive_ui_contract.md) — read-only panels vs primitives
- [`../backend/lab_communicator/README.md`](../backend/lab_communicator/README.md) — mock / real / mujoco bridge (unchanged role)

> **Naming:** **`ControlManager`** (cloud-labs) = configuration version history and branch graph.  
> **`OpticalExperiment`** / experiment manager (`lab_automation`) = low-level robot, cameras, and hardware procedures.  
> Do **not** use “ExperimentManager” for VC in this repo.

---

## 1. Executive summary

We want **experiments as version-controllable objects**: save configuration commits, fork branches, travel between nodes in a graph, and attach **observations** (lab receipts) without conflating intent with measurement.

Two new **shared** modules in `lab_model` (communicator-agnostic) implement this **without rewriting mock / real / MuJoCo subclasses**:

| Manager | Owns | Time scale |
|---------|------|------------|
| **RuntimeManager** | Live **runtime** JSON (working tree) — all mutations typed and centralized | Seconds |
| **ControlManager** | **Configuration** DAG, observation pins, setups, branches | Minutes–days |

**Communicators** keep doing hardware: `_primitive_*` hooks only. Changes concentrate in **`lab_communicator/base.py`** (wire RuntimeManager into existing orchestration) and **`main.py`** (ControlManager API). Mock, real, and MuJoCo inherit the new behavior automatically.

**Development order:** mock first (Phases 0–5). **Exit gate (Phase 6):** same ControlManager store and APIs work with mock, real, and MuJoCo.

---

## 2. Glossary

Today “state” is overloaded. Use these terms in docs, UI, and APIs.

### 2.1. Runtime

> The live lab JSON the UI polls — the **working tree**.

**Today:** `LabCommunicator.current_state` (`GET /api/lab-state`).

**Contains:** configuration slice + observations slice + telemetry + `system_status` + `holding` + process fields (`optimization_step`, …).

**Not versioned directly.** Checkouts **project into** runtime. Telemetry is ephemeral (sessions, not VC nodes).

**UI rule (target):** the browser reads runtime to decide what to show; **writes** to bench-affecting fields should go through **RuntimeManager** mutation kinds (see §4), which for normal operation means **primitives**.

### 2.2. Configuration

> Ensemble of **commanded intent** (all tunables + holding intent).

- Per component: `statecontrol.tunables`
- Top-level: `holding` when it encodes commanded in-air pose (exclude `requires_operator_confirm` — runtime-only)

**Excludes:** measurables, telemetry, `system_status`.

**Stored as:** configuration **commits** in ControlManager (branch graph nodes).

### 2.3. Observations

> Ensemble of **recorded lab receipts** (all measurables).

- Per component: `statecontrol.measurables`

**Excludes:** tunables, telemetry.

Keep JSON key **`measurables`** inside components; **Observations** is the aggregate/document name.

**Not branchable** — pin, compare with tolerance, re-record; never “checkout” physics from an old PNG.

### 2.4. Setup

> **Configuration + observations** at a point in history (Git **tag**).

Named checkpoints: `after-alignment-v3`, `golden-recipe-run`. Observations optional.

### 2.5. Naming migration

| Old / ambiguous | New term |
|-----------------|----------|
| “Save state” | “Save configuration” or “Save setup” |
| `states/*.json` full dump | **Removed** (2026-06-25) — use configuration commits under `control/` |
| Recipe `_golden.json` | **Setup** (config for replay + obs for drift) |
| `session_last_lab_state.json` | Recovery checkpoint — not VC |
| `OpticalExperiment` | **`lab_automation` hardware** — not ControlManager |

---

## 3. Design principles

### 3.1. Communicator-agnostic ControlManager

ControlManager, projections, diff, reconcile planning, and file store live in **`lab_model`**. No imports from mock / real / MuJoCo.

Communicators only execute **reconcile plans** (primitive sequences) on hard checkout and existing **record** hooks.

### 3.2. Shared RuntimeManager — no per-backend rewrite

**Goal:** mock, real, and MuJoCo gain RuntimeManager semantics by changing **shared** code only.

```text
lab_model/state/runtime_manager.py     ← NEW: typed mutations, lock, audit
lab_model/state/control_manager.py     ← NEW: VC store + graph
lab_communicator/base.py               ← WIRE: delegate commits / set_lab_state
lab_communicator/mock|real|mujoco/     ← UNCHANGED hooks (primitives.py, etc.)
main.py                                ← ControlManager routes; GET runtime via manager
```

**Pattern:**

1. `LabCommunicator.__init__` creates or receives a **RuntimeManager** holding a reference to the runtime dict (today `self.current_state`).
2. Existing orchestrators in `base.py` call `runtime_manager.apply(...)` instead of mutating dict + ad hoc `commit_*` directly (commit helpers remain — invoked *inside* the manager).
3. `get_lab_state()` → `runtime_manager.snapshot()` (deep copy under lock).
4. `set_lab_state()` → `runtime_manager.apply(AdministrativeLoad, ...)`.
5. Primitive success paths → `runtime_manager.apply(PrimitiveCommit, ...)`.
6. Subclasses **do not** subclass RuntimeManager; they inherit wired `base.py`.

Optional thin overrides remain (`_apply_loaded_pose_to_hardware`, `_persist_state`) — hardware side only.

### 3.3. Runtime mutation kinds (single-writer)

All runtime writes go through RuntimeManager with an explicit **kind**:

| Kind | Source | Primitive dispatch? | Examples |
|------|--------|----------------------|----------|
| **PrimitiveCommit** | Orchestration after successful primitive | Yes | `commit_move_to_breadboard`, teleop commits |
| **ObservationCommit** | After `RECORD_MEASURABLES` | Yes | `commit_observed_measurables` |
| **ProcessTransition** | State machine | Internal | `system_status`, null measurables on BUSY |
| **TelemetryCommit** | TeleOp / live feed session | Via teleop primitives | `telemetry.teleop.active` |
| **AdministrativeLoad** | Load saved workspace | No (audited) | `set_lab_state`, legacy import |
| **RecoveryPatch** | Session reconciliation | No (audited) | checkpoint restore |
| **BootHydrate** | Real scan / mock seed | No (boot-only) | initial poses from vision |
| **ProjectionApply** | ControlManager checkout | No (VC) | soft/hard configuration apply |

This makes the UI contract **enforceable**: bench-affecting intent changes are **PrimitiveCommit** or **ProjectionApply** derived from configuration diff → reconcile plan.

**Today (gap):** runtime is mutated from many paths without a single gate; this doc describes the target.

### 3.4. Reversible intent via primitive projection

Version branches live in **configuration space**.

- `MOVE_*`, `SET_*`, storage primitives → reconcile as themselves.
- **`OPTIMIZE`** → reconcile uses **final `nominal_pose` / motor setpoints only**, not optimizer trace. Intermediate steps are never versioned. Multi-variable **ensemble** sessions are specified in [`ENSEMBLE_OPTIMIZATION.md`](./ENSEMBLE_OPTIMIZATION.md).
- **`RECORD_MEASURABLES`** → observations pin, not configuration commit (unless user saves **setup**).

**Optimization metadata (non-reconcile):** configuration commits may carry a sibling `metadata.optimization` block per tag (`placement_mode`, `last_optimization_score`, optional `last_optimized_pose`). This annotates *how* a pose was reached and powers UI golden highlights; it does **not** participate in `configuration_diff()` or `plan_reconcile()`. Legacy `tunables.placement` is stripped from the versioned configuration slice and excluded from diffs. One-time backfill: `POST /api/control/backfill-optimization-metadata`.

### 3.5. Checkout modes

| Mode | RuntimeManager | Communicator |
|------|----------------|--------------|
| **Soft** | `ProjectionApply(VIEW)` or client overlay | No motion |
| **Hard** | Apply config + enqueue reconcile | Executes primitive plan |

Phase 3 UI: soft only. Phase 5: hard on mock. Phase 6: real + MuJoCo.

### 3.6. Manual commit UX

- **Commit** = user presses **Save configuration** → ControlManager `commit(extract_configuration(runtime))`.
- **Save setup** = configuration commit + observations pin.
- Auto-commit on every primitive: deferred (optional audit log later).

### 3.7. Branch graph UX

```text
        obs*
         |
    C ───●─── D ─── HEAD (main)
         |
         └── E ─── F   (branch: try-secondary-mirror)
```

- Node = configuration commit.
- Fork = new branch from parent node.
- Travel (now) = soft checkout — UI shows selected configuration.
- Travel (later) = hard checkout — reconcile primitive sequence.

---

## 4. Architecture

### 4.1. Layer diagram

```text
┌────────────── UI ──────────────┐
│ poll GET /api/lab-state        │
│ branch graph, Save config      │
└───────────────┬────────────────┘
                │
┌───────────────▼────────────────────────────────────────────┐
│ main.py                                                     │
│   ControlManager API (commits, graph, checkout, diff)       │
│   primitive dispatch (unchanged entry)                      │
└───────────────┬──────────────────────────┬─────────────────┘
                │                          │
┌───────────────▼──────────────┐  ┌────────▼─────────────────┐
│ ControlManager (lab_model)   │  │ RuntimeManager (lab_model)│
│ configurations/, setups/     │  │ sole runtime write gate   │
│ observations/, blobs/        │  │ wraps current_state dict  │
└───────────────┬──────────────┘  └────────┬─────────────────┘
                │ projections / checkout    │
                └──────────────┬──────────────┘
                               │
┌──────────────────────────────▼─────────────────────────────┐
│ LabCommunicator (base.py) — shared wiring only              │
│   orchestration → runtime_manager.apply(PrimitiveCommit)    │
│   set_lab_state → runtime_manager.apply(AdministrativeLoad) │
└──────────────────────────────┬─────────────────────────────┘
                               │ _primitive_* only
        ┌──────────────────────┼──────────────────────┐
        │                      │                      │
   mock/                  real/                  mujoco/
   (unchanged hooks)      (unchanged hooks)     (unchanged hooks)
        │                      │                      │
        └──────────────────────┴──────────────────────┘
                               │
                    lab_automation OpticalExperiment
                    (hardware — NOT ControlManager)
```

### 4.2. ControlManager responsibilities

- Persist under `{LAB_VIEW_PATH}/control/` (or `experiments/` — pick one at implementation):
  - `configurations/*.json`, `observations/*.json`, `setups/*.json`, `blobs/`, `refs.json` (branch HEADs)
- `commit(message, branch)` ← extract configuration from RuntimeManager snapshot
- `fork(branch, parent_id)`, `history(experiment_id)`, `diff(from, to)`
- `pin_observations(configuration_id)` ← extract observations from runtime
- `plan_reconcile(current_cfg, target_cfg)` → command envelopes
- `checkout(id, mode)` → calls RuntimeManager + optional primitive schedule

**Does not** talk to xArm, MuJoCo, or cameras directly.

### 4.3. RuntimeManager responsibilities

- Own runtime dict + lock (today `_state_lock` moves here or is shared)
- `snapshot()` for API reads
- `apply(mutation)` — only entry for writes; invokes existing `commits.py` helpers internally
- `extract_configuration()` / `extract_observations()` delegates to projection module
- Optional: mutation log ring buffer for debug

**Does not** persist VC files.

### 4.4. Document shapes (stored commits)

See prior sections in git history; shapes unchanged:

- `cloud_labs_configuration` — commit node
- `cloud_labs_observations` — pin linked to `configuration_id`
- `cloud_labs_setup` — named tag

### 4.5. Frontend (conceptual)

| Surface | Role |
|---------|------|
| **Control panel** | Branch, HEAD, Save configuration / setup |
| **Timeline / graph** | Nodes, branches, observation pins |
| **Diff panel** | Configuration delta vs HEAD |
| **Checkout bar** | Soft view now; Apply on bench later |

Canvas: ghost ← configuration; solid ← observations; stale badge when viewing non-HEAD node.

---

## 5. Phased roadmap and actionables

### How to read

| Column | Meaning |
|--------|---------|
| **Phase** | Ordered milestone |
| **Exit** | Done check |
| **Communicators** | Must pass at phase end |

---

### Phase 0 — Vocabulary and manager ADR

**Actionables:**

- [x] Canonical doc: this file (`CONTROL_RUNTIME_AND_VERSIONING.md`).
- [ ] Adopt glossary in UI copy guidelines: Runtime, Configuration, Observations, Setup.
- [ ] Document **ControlManager ≠ OpticalExperiment** in [`README.md`](../README.md) (see §2.5).
- [ ] Update [`universal_component_architecture.md`](./universal_component_architecture.md) glossary + Phase 10 hook.
- [ ] Update [`backend/lab_model/ARCHITECTURE.md`](../backend/lab_model/ARCHITECTURE.md) planned managers section.
- [ ] Decide: `holding` in configuration? (**Yes**, minus `requires_operator_confirm`.)
- [ ] Decide: default control repo id (`default`).
- [ ] Decide: store path `control/` vs `experiments/`.

**Exit:** team aligned; no “ExperimentManager” for VC in new docs.

**Communicators:** n/a

---

### Phase 1 — Projections, schemas, manager interfaces (pure lab_model)

**Actionables:**

- [ ] `lab_model/state/projections.py` — `extract_configuration`, `extract_observations`, `build_setup`.
- [ ] `lab_model/state/diff.py` — configuration diff, observation diff (tolerances).
- [ ] `lab_model/state/reconcile.py` — `plan_reconcile` rules (optimize → target tunables).
- [ ] `lab_model/state/runtime_manager.py` — **interface + mutation kinds**; unit tests with dict fixture (no communicator).
- [ ] `lab_model/state/control_manager.py` — **interface** for store/graph (in-memory impl for tests).
- [ ] Pydantic models for stored document kinds.
- [ ] Tests on mock `lab_state.json` fixtures.

**Exit:** projections + diff + reconcile plan tested; RuntimeManager/ControlManager interfaces documented.

**Communicators:** none

---

### Phase 2 — Wire RuntimeManager in base.py (shared only)

**Actionables:**

- [ ] Implement RuntimeManager.apply() calling existing `commits.py` / `snapshot.py` helpers.
- [ ] Route `LabCommunicator.set_lab_state` → `AdministrativeLoad`.
- [ ] Route primitive orchestration commits → `PrimitiveCommit` (one primitive path at a time if incremental).
- [ ] `get_lab_state()` → `runtime_manager.snapshot()`.
- [ ] **Do not edit** mock/real/mujoco `communicator.py` except if compile breaks (expect none).
- [ ] Regression tests: existing mock primitive tests pass unchanged.

**Exit:** all backends inherit runtime single-writer; behavior parity with today on mock.

**Communicators:** mock (+ existing test suite)

---

### Phase 3 — ControlManager store + API (mock)

**Actionables:**

- [ ] File-backed ControlManager under `{LAB_VIEW_PATH}/control/`.
- [ ] `POST /api/control/{id}/configurations` — manual commit from runtime snapshot.
- [ ] `GET /api/control/{id}/history`, `GET .../configurations/{cid}`, `GET .../diff`.
- [ ] `POST /api/control/{id}/branches` — fork.
- [ ] `refs.json` branch HEAD pointers.
- [ ] Reject commits when `system_status` is `BUSY` / `OPTIMIZING`.

**Exit:** API creates commits and branches on mock lab.

**Communicators:** mock

---

### Phase 4 — UI: Save + branch graph + soft checkout (mock)

**UX backlog (bugs + redesign):** [`CONTROL_PANEL_UX_BACKLOG.md`](./CONTROL_PANEL_UX_BACKLOG.md)

**Actionables:**

- [ ] **Save configuration** → ControlManager commit.
- [ ] Control panel: branch selector, HEAD, timeline/graph.
- [ ] Soft checkout: view node configuration (client overlay or `ProjectionApply(VIEW)` — implement Q3 decision).
- [ ] Banner: “Viewing commit X — not live HEAD”.
- [ ] Relabel legacy Save/Load state.

**Exit:** operator commits, forks, travels graph on mock.

**Communicators:** mock

---

### Phase 5 — Observations pins + setups (mock)

**Actionables:**

- [ ] Pin observations after `RECORD_MEASURABLES`.
- [ ] Blob store for `camera_image` paths.
- [ ] **Save setup** UI.
- [ ] Observation markers on timeline; drift compare vs setup.

**Exit:** full setup workflow on mock.

**Communicators:** mock

---

### Phase 6 — Hard checkout + reconcile (mock)

**Actionables:**

- [ ] Implement `plan_reconcile` executor via existing `execute_validated_command`.
- [ ] `POST /api/control/{id}/checkout` — `{ configuration_id, mode: hard }`.
- [ ] UI: Apply on bench + primitive preview list.
- [ ] RuntimeManager: `ProjectionApply(HARD)` + ProcessTransition during reconcile.

**Exit:** mock walks between configuration nodes via primitives.

**Communicators:** mock

---

### Phase 7 — Communicator parity (real + MuJoCo)

**Actionables:**

- [ ] **Real:** hard checkout reconcile through existing `RealLabCommunicator` primitives; `_apply_loaded_pose_to_hardware` on reconcile moves only.
- [ ] **Real:** observation blobs from `camera_captures/`.
- [ ] **MuJoCo:** soft checkout; hard checkout for supported primitives (`MOVE_COMPONENT` first); 409 with clear report for unsupported plan steps.
- [ ] **MuJoCo:** runtime mode switch policy (HEAD preserved across mock↔mujoco).
- [ ] Same configuration files work across all three backends.
- [ ] Document in [`README.md`](../README.md).

**Exit:** ControlManager + RuntimeManager unchanged across mock, real, MuJoCo; hard checkout works where primitives exist.

**Communicators:** **mock + real + mujoco** (required gate)

---

### Phase 8 — Migration and polish (optional)

**Actionables:**

- [ ] Migrate `states/*.json` → configuration commits.
- [ ] Recipe golden → setup documents.
- [ ] Optional mutation audit log (not auto-commit).
- [ ] Export/import control bundle zip.
- [ ] Command console: `commit`, `checkout`, `branch`.

**Exit:** legacy paths retired or thin wrappers.

**Communicators:** all

---

## 6. Dependency overview

```text
Phase 0 (vocabulary + ADR)
    │
    ▼
Phase 1 (projections, interfaces)
    │
    ▼
Phase 2 (RuntimeManager in base.py)  ◄── shared logic; no backend rewrites
    │
    ▼
Phase 3 (ControlManager API)
    │
    ▼
Phase 4 (UI graph + soft checkout)     ┐
Phase 5 (observations + setups)      │ mock trunk
Phase 6 (hard checkout)              ┘
    │
    ▼
Phase 7 (real + mujoco parity)       ◄── required exit
    │
    ▼
Phase 8 (migration)
```

---

## 7. Open questions

| ID | Question | Lean |
|----|----------|------|
| Q1 | `placement.mode` in diff? | **No** — stored in `metadata.optimization`; excluded from reconcile diff |
| Q2 | Control repo id | `default` first |
| Q3 | Soft checkout server vs client | Client overlay first; optional `runtime.view_configuration_id` later |
| Q4 | Reconcile path | Direct diff to target |
| Q5 | Stale observations on soft view | Badge; null on hard only |
| Q6 | MuJoCo unsupported reconcile steps | Fail plan validation upfront |
| Q7 | Catalog drift | `catalog_hash` on commit |
| Q8 | Store directory name | `control/` (emphasizes ControlManager) vs `experiments/` |

---

## 8. Success criteria

1. **RuntimeManager** is the only runtime write gate; wired via **base.py** only.
2. **ControlManager** owns configuration history; never imports communicators.
3. Operator: Save configuration, branch graph, soft travel on **mock**.
4. Observations pins + setups on mock.
5. Hard checkout on mock; then **real + MuJoCo** with same store/API.
6. Docs consistently use **ControlManager** (not ExperimentManager) and **Configuration / Observations / Setup / Runtime**.

---

## 9. Summary

| Concept | Contains | Versioned? | Checkout? |
|---------|----------|------------|-------------|
| **Runtime** | Live JSON (all pillars + process) | No | — |
| **Configuration** | Tunables (+ holding intent); reconcile slice omits `placement` | Yes (DAG) | Soft / hard |
| **Configuration metadata** | Optimization outcomes (`metadata.optimization`) | Yes (on commit doc) | Soft preview + hard apply (display) |
| **Observations** | Measurables | Pins | Compare / re-record |
| **Setup** | Config + obs | Tags | View / export |

**ControlManager** = Git for configuration. **RuntimeManager** = single writer for the working tree. **Communicators** = hardware executors only. **`OpticalExperiment`** = unrelated; stays in `lab_automation`.
