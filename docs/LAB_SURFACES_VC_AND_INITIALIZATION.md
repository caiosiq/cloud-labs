# Lab surfaces, version control tiers, and initialization policy

**Status:** Surfaces + catalog pins + init policy **implemented (mock-first)**; publish/approve workflow shipped  
**Last updated:** 2026-07-11  
**Audience:** cloud-labs maintainers, experimentalists, operators of shared benches  

**Related docs:**

- [`PROGRAMMABLE_LAB_VISION.md`](./PROGRAMMABLE_LAB_VISION.md) — OPU analogy, three layers
- [`EXECUTION_MODES.md`](./EXECUTION_MODES.md) — imperative / compiled / closed-loop + lease
- [`CONTROL_RUNTIME_AND_VERSIONING.md`](./CONTROL_RUNTIME_AND_VERSIONING.md) — local ControlManager, runtime vs configuration
- [`Run_CloudLab_Scripts.md`](./Run_CloudLab_Scripts.md) — SDK usage
- [`SESSION_KERNELS.md`](./SESSION_KERNELS.md) — session TorchScript packages
- [`CONTROL_REPOS_AND_INVENTORY_PLAN.md`](./CONTROL_REPOS_AND_INVENTORY_PLAN.md) — multi-repo local VC (implemented on mock)

> **Problem this doc solves:** Track 1 (hardware VC) and Track 2 (scripted jobs) were merged on one operations page. Operators saw git-like **“uncommitted changes”** blockers while trying to **browse branches** or **watch jobs**. That is an architectural mistake, not a small UX bug.

---

## 1. Executive summary

Cloud-labs exposes **three surfaces** with **one queue** (Job Manager + session lease):

| Surface | Role | Mutates hardware? | Edits local VC? | Needs lease? |
|---------|------|-------------------|-----------------|--------------|
| **Landing** (`/`) | Brand entry + surface links | No | No | No |
| **Twin UI** (`/twin`) | Direct control + local version control | Yes (when leased) | Yes | **Yes** |
| **Operations** (`/operations`) | Job tracking + live mirror | **No** | **No** | No |
| **Catalog** (`/catalog`) | Remote approved configuration pins | No | No (read-only) | No |

**Version control has two tiers:**

- **Local VC** — file-backed control repos on the bench deployment (`ControlManager`). Commit, branch, fork, stash. Private to the operator or lab group.
- **Remote catalog** — curated, **owner-approved** configuration pins. **Not** auto-populated when someone creates a local repo or uses the bench. **Publish** = submit request → owner approves → pin appears in catalog.

**Initialization** for scripts and jobs defaults to **`force_reconcile`**: acquiring the lease means this run’s declared baseline wins; prior manual drift on the bench is overwritten by reconcile primitives, not blocked by dirty-tree git rules.

---

## 2. The queue as single arbiter

```text
                         ┌─────────────────────────┐
                         │  Job Manager + Lease    │
                         │  (one holder per bench) │
                         └───────────┬─────────────┘
                                     │
              acquire lease          │           read-only observe
         (Twin UI, script, job)     │           (Operations)
                                     │
         ┌───────────────────────────┼───────────────────────────┐
         ▼                           │                           ▼
   Twin UI (/twin)                   │                    Operations (/operations)
   DIRECT CONTROL                    │                    JOB TRACKING ONLY
   • primitives when leased           │                    • job queue + status
   • local VC graph                 │                    • telemetry + trace
   • stash / commit / fork          │                    • twin mirror (same renderer)
   • optimization staging           │                    • what was sent (job spec)
                                     │                    • NO control, NO local VC UI
```

### 2.1. Rules

1. **Twin UI** is the only interactive surface that may issue primitives. It must **request and hold a session lease** before motors, optimization, teleop, or other mutations run.
2. **Scripts and jobs** acquire the lease as their holder (`sdk:…`, `job:…`, `ui:…`). While held, Twin UI controls are disabled (read-only telemetry still visible).
3. **Operations** never acquires a lease and never submits primitives. It polls `GET /api/lab-state` and `GET /api/jobs/*` to **mirror** what a leased Twin UI would show.
4. **Catalog** is read-only browsing of **remote approved pins** and backend metadata. Choosing a pin does not move hardware; it informs script/job `snapshot` or operator prep on Twin UI.

### 2.2. Why Operations reuses the twin renderer

The digital twin canvas on `/operations` uses the **same** `render.js` + `lab-state.js` stack as Twin UI so operators can verify that queued work matches physical motion. That is **observation**, not control. A read-only shield blocks canvas edits; job progress syncs `pendingCommands` highlights using the same path as the reconcile runner.

---

## 3. Twin UI — direct control (`/twin`)

**Purpose:** Human operator explores, nudges, aligns, commits, and stages optimizations.

**Includes:**

- Optical table canvas (editable when leased and not in view-only modes)
- Component panels, teleop, recipes, command console
- **Local** control graph: repos, branches, commits, stash, soft preview, step-by-step apply on bench
- Ensemble optimization staging (submits closed-loop **jobs** when run)

**Does not include:**

- Remote catalog publish/approval workflow (future: “request publish” action may start here)
- Global job queue administration beyond “my run” (see Operations)

**Lease UX (target, not fully implemented):**

- Banner: `Bench leased by job:…` / `sdk:…` when another holder is active
- Gray out motor sliders and primitive buttons when unleased or foreign lease
- Explicit **“Take control”** acquires lease (may queue behind active job)

---

## 4. Operations — job tracking only (`/operations`)

**Purpose:** Follow what the system is doing: queued tasks, active job telemetry, and a trustworthy live twin mirror.

**Includes (target):**

| Panel | Content |
|-------|---------|
| Backends | Active backend id, lease holder, `active_job_id`, system status |
| Jobs | Queue, status, progress, cancel |
| Job telemetry | Closed-loop trace, compiled DAG step list, raw job JSON |
| Digital twin | Read-only mirror of bench state |

**Must not include (refactor target):**

- Repo / branch picker for experiment design
- Reconcile plan preview / submit (moved to Catalog or Twin UI)
- Closed-loop JSON submit for ad-hoc runs (scripts or Twin UI instead)
- Stash / commit / dirty-tree error banners
- “Snapshot current bench” actions

**May show (read-only context):**

- Job `snapshot_ref` or `spec.finalize_checkout` — which pin this run initialized from
- Init phase in job progress: `Reconciling hardware to commit a1f3c2… (step 3/12)`
- Optional drift hint: `runtime differs from applied node` (informational only)

### 4.1. Current implementation gap

Phase C.1 placed a **Configuration** panel on `/operations` (repo picker, reconcile preview, job submit). That predates this spec. **Treat it as temporary.** Next UI phase removes VC actions from Operations and restores the split described here.

---

## 5. Catalog — remote approved pins (`/catalog`)

**Purpose:** Study the lab’s **published** capabilities and pick initialization baselines — a **store**, not a control room.

**Includes (target):**

- **Backends** — `mock.default`, `real.<bench_id>`, …; communicator type; key **parameters** (resolution, ranges, safety)
- **Remote pins** — approved configuration snapshots (repo id, branch, commit, display name, metadata, catalog hash)
- Diff **between commits** (catalog entries), not runtime-vs-commit dirty checks
- Actions: **Copy pin id** / **Use in script** (generates snapshot JSON), **Request access** to backend (if policy requires)

**Does not include:**

- Every local `control/` repo on disk
- Live bench dirty state
- Hardware mutation or reconcile execution
- Automatic listing when an operator creates a local repo

### 5.1. Local vs remote — critical distinction

| | **Local VC** | **Remote catalog** |
|---|-------------|-------------------|
| **Storage** | `{LAB_VIEW_PATH}/control/<repo_id>/` | Separate approved store (TBD: DB table or `catalog/pins/`) |
| **Who creates** | Operator on Twin UI; script `commit_configuration` | **Owner** after approving a publish request |
| **Visibility** | Anyone with filesystem/API access to deployment | All users browsing `/catalog` |
| **Bench use** | `load_snapshot(local_repo, branch)` | `load_snapshot(catalog_pin_id)` or equivalent |
| **Creating a bench** | Does **not** create a catalog entry | N/A |

**Publish is not `git push`.** Flow:

```text
Operator: commit locally on Twin UI
        → request_publish(commit_id, message, target_backend?)
        → pending queue for system owner
Owner:   review diff / metadata → approve | reject
        → approved commit becomes a CatalogPin (remote)
```

Local branches and forks remain local until explicitly published and approved.

---

## 6. Version control in scripting

Scripts interact with VC through **four classes** of operation:

### 6.1. Pin (read)

```python
lab.load_snapshot("laser-cavity", "main")           # local repo head
lab.load_snapshot(catalog_pin="laser-cavity-main")  # remote approved pin
```

Sets `snapshot_ref` on the session lease / job record. Does not move hardware.

### 6.2. Apply (mutate hardware + pointer)

```python
lab.reconcile_hardware()  # default policy: force_reconcile
```

Or: job `compiled_dag` init step + `finalize_checkout` in job runner (same semantics).

### 6.3. Record (local write)

```python
lab.commit_configuration(message="after sweep 12")
lab.fork_branch("sweep-line", parent_id=...)
lab.stash(message="manual alignment before lunch")
```

**Local tier only.** Never auto-appears on remote catalog.

### 6.4. Publish (request remote)

```python
lab.request_publish(commit_id, message="propose golden cavity Jul 2026")
```

Creates approval workflow; owner promotes to CatalogPin on accept.

### 6.5. What scripts do not do

- Implicit push to remote on connect
- Block on dirty table when `initialization_policy=force_reconcile`
- Replace Twin UI for interactive stash/commit during manual sessions

---

## 7. Initialization policies

When a script, job, or leased Twin session starts work against a declared `snapshot`, the backend applies an **initialization policy**:

| Policy | Behavior | Default for |
|--------|----------|-------------|
| **`force_reconcile`** | Plan reconcile from **current physical state → target commit**; run steps; finalize pointer. Dirty vs applied **does not block**. | Jobs, overnight runs, SDK production |
| **`stash_and_start`** | Stash current bench to local stash (or ephemeral branch); then reconcile to target. | Interactive notebooks (opt-in) |
| **`strict`** | Fail immediately if runtime differs from target commit beyond tolerance. | Audits, safety-critical checks |

### 7.1. Queue rule (normative)

**Dirty-vs-applied conflicts matter only when two actors compete without a clear initialization step.**

When a holder **acquires the lease** and starts a job/script with `force_reconcile`:

1. Prior manual tweaks are **discarded** relative to this run (motors move to target).
2. Stashes saved **explicitly** in earlier sessions remain in local VC for later recall — they are not auto-restored.

### 7.2. Dirty guard scope (today vs target)

**Today:** `POST /api/control/{repo}/checkout` with `mode: "hard"` blocks on `dirty_working_table` even when `preview: true`. That blocks Operations preview, SDK `reconcile_hardware()` preview, and catalog-style planning.

**Target:**

| Caller | `preview: true` | execute reconcile |
|--------|-----------------|-------------------|
| Twin UI manual apply (no force) | Allowed | Blocked if dirty (stash/commit first) |
| Twin UI with operator confirm force | Allowed | Allowed |
| Job / SDK `force_reconcile` | Allowed | Allowed (lease required) |
| Catalog browse | N/A (no live bench diff) | N/A |

---

## 8. Job envelope and initialization (target shape)

```json
{
  "mode": "compiled_dag",
  "holder": "sdk:notebook-12",
  "backend_id": "mock.default",
  "initialization_policy": "force_reconcile",
  "snapshot": {
    "source": "local",
    "repo_id": "laser-cavity",
    "branch": "main",
    "commit": "86f37bd6..."
  },
  "steps": [ "... reconcile primitives ..." ],
  "finalize_checkout": {
    "repo_id": "laser-cavity",
    "configuration_id": "86f37bd6...",
    "branch": "main"
  }
}
```

Remote pin variant:

```json
"snapshot": {
  "source": "catalog",
  "pin_id": "golden-cavity-2026-07",
  "commit": "86f37bd6..."
}
```

Job runner executes init reconcile as visible steps (Operations telemetry). User logic (DAG body or closed-loop) runs only after init succeeds or is skipped (already matched).

---

## 9. Open decisions

| # | Question | Proposal (draft) |
|---|----------|------------------|
| 1 | Who approves publish requests? | Per-backend **owner** role (config flag); mock: auto-approve |
| 2 | Where do CatalogPins live? | `catalog/pins.json` or DB table separate from `control/` |
| 3 | Catalog route | **`/catalog`** third page (keeps separation obvious) |
| 4 | Mock lease relax | Twin UI may skip lease on mock via config; **real: never** |
| 5 | Post-job commit | Opt-in `on_success: commit_configuration` hook on job spec |
| 6 | Tensor artifacts | Edge disk path + URI in job log (not inline JSON) |

---

## 10. Implementation phases

Phases are ordered to fix the architectural clash before adding remote catalog.

### Phase G.1 — Document alignment (this doc)

- [x] Freeze surfaces, VC tiers, initialization policy
- [x] Cross-link from `EXECUTION_MODES.md`, `PROGRAMMABLE_LAB_VISION.md`, `CONTROL_RUNTIME_AND_VERSIONING.md`, `Run_CloudLab_Scripts.md`

### Phase G.2 — Backend: initialization policy

- [x] Add `initialization_policy` to job submit + SDK `connect()` / `reconcile_hardware()`
- [x] Bypass `dirty_working_table` when `force_reconcile` + valid lease
- [x] Allow `preview: true` hard checkout without dirty block (plan-only path)
- [x] Auto-prepended init step: jobs with `snapshot` + `force_reconcile` plan reconcile before user steps
- [x] Tests: policy + catalog pins store

### Phase G.3 — UI: split Operations from control

- [x] Remove Configuration panel (repo/branch/reconcile submit) from `/operations`
- [x] Operations: backends + jobs + telemetry + read-only twin only
- [x] Twin UI: lease banner, block mutations when foreign holder (real benches)
- [x] Link from Operations → Twin UI and → Catalog

### Phase G.4 — Catalog page (remote read-only)

- [x] `GET /api/catalog/pins` — approved pins only
- [x] `/catalog` UI: backends + remote pin list
- [x] Copy-to-clipboard snapshot JSON for scripts

### Phase G.5 — Publish workflow (local → remote)

- [x] `POST /api/catalog/publish-requests` from API / SDK
- [x] `POST /api/catalog/publish-requests/{id}/approve` (owner)
- [x] `POST /api/catalog/publish-requests/{id}/reject` (owner + reason)
- [x] Mock backends auto-approve on submit
- [x] Twin UI publish-request action (control graph toolbar)
- [x] Catalog page pending-requests review panel (real backends)

### Phase G.6 — SDK VC helpers

- [x] `commit_configuration`, `fork_branch`, `stash`, `pop_stash`, `drop_stash`
- [x] `request_publish(commit_id, …)` (via `request_publish()`)
- [x] `load_snapshot(catalog_pin=…)` when pin source implemented

### Phase G.7 — Polish and real bench

- [x] Post-job optional `on_success.commit_configuration` hook on job spec
- [x] Twin UI optimization **Run** stage: reconcile-before-run snapshot + optional commit-after-run (`on_success`)
- [x] Operations job telemetry: `init` / `post_commit` phases and commit outcome
- [x] Shared edge image metrics in `lab_model/optimization/metrics/image_features.py` (real ensemble)
- [x] Real bench objective planner: camera capture tag resolve + laser readback (`objective_measurements.py`)
- [x] Strict real preflight: `LASER_SOURCE` for power terms, catalog camera for centroid
- [x] `lab_model/optimization/README.md` — ensemble home (replaces removed `backend/lab_automation/` loop)
- [ ] Real invasive touch-and-go blocks on ensemble router (deferred)

---

## 11. Glossary (additions)

| Term | Meaning |
|------|---------|
| **Twin UI** | Interactive control surface at `/twin`; requires lease |
| **Landing** | Cloud Labs entry at `/` |
| **Operations** | Job monitor at `/operations`; read-only |
| **Catalog** | Remote approved pins at `/catalog`; read-only store |
| **Local VC** | ControlManager repos under deployment `control/` |
| **CatalogPin** | Owner-approved remote configuration reference |
| **Publish request** | Ask to promote a local commit to CatalogPin |
| **Initialization policy** | How a run handles bench drift vs snapshot (`force_reconcile`, …) |
| **force_reconcile** | Default: reconcile to snapshot without dirty guard |

---

*End of spec. Implementation PRs must reference this doc when touching `/operations`, checkout dirty guard, or catalog/publish APIs.*
