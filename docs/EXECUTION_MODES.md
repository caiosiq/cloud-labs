# Execution modes — how scripts, jobs, and the bench interact

**Status:** Phase A specification; **Phase B–D implemented (mock-first); Job Manager + lease + closed-loop jobs on mock; Phase G surfaces shipped; session kernels — see [`SESSION_KERNELS.md`](./SESSION_KERNELS.md)**  
**Last updated:** 2026-07-11  
**Audience:** SDK authors, backend maintainers, anyone running scripts or batch jobs on shared hardware  

**Related docs:**

- [`EDGE_CONTRACT_AND_UC_LIVE_PLANE.md`](./EDGE_CONTRACT_AND_UC_LIVE_PLANE.md) — Edge Contract + UC live plane
- [`../packages/cloudlabs/README.md`](../packages/cloudlabs/README.md) — imperative Python SDK (`connect` / primitives)
- [`SESSION_KERNELS.md`](./SESSION_KERNELS.md) — author-defined TorchScript packages
- [`ENSEMBLE_OPTIMIZATION.md`](./ENSEMBLE_OPTIMIZATION.md) — closed-loop ensemble session (implemented)
- [`LAB_SURFACES_VC_AND_INITIALIZATION.md`](./LAB_SURFACES_VC_AND_INITIALIZATION.md) — Twin / Operations / Catalog, VC, init policy
- Wiki Learn — OPU / three faces
- [`../backend/lab_model/language/domain/holding.py`](../backend/lab_model/language/domain/holding.py) — `system_status` constants
- [`../backend/lab_model/coordinator/state/state_machine.py`](../backend/lab_model/coordinator/state/state_machine.py) — primitive refusals

> **Critical rule (Phase B+):** **Every** hardware-affecting entry path — UI, imperative HTTP script, compiled batch job, closed-loop optimizer — must acquire an **exclusive session lease** on exactly one **backend** before issuing primitives. See §4.

---

## 1. Executive summary

Cloud-labs will support **three execution modes**. They differ in **where control flow runs**, **how much data crosses the network**, and **how tightly the feedback loop is coupled** to hardware.

| Mode | Control flow | Typical use | Bandwidth profile |
|------|----------------|-------------|-------------------|
| **Imperative** | Client (Python loop) | Prototyping, interactive notebooks | Low frequency commands; selective tensor fetch |
| **Compiled DAG** | Edge + orchestrator | Overnight sweeps, LLM recipes, unattended pipelines | Job payload up; telemetry down |
| **Closed-loop** | Edge kernel | Ensemble optimization, stabilization, fast eval loops | Scalars/trace out; tensors stay on edge |

All three modes share:

- The same **primitive IR** (`POST /api/command` JSON today).
- The same **runtime JSON** (`GET /api/lab-state`).
- The same **refusal rules** (`system_status`, holding, stored parts).
- The same **exclusive session lease** (**Job Manager** — mock shipped) — fixing the “split personality” bug where HTTP scripts and batch jobs could interleave on real hardware.

---

## 2. Type definitions (normative)

These types are **specification targets**. Fields marked *(today)* exist in some form; others are **planned**.

```python
from enum import Enum
from typing import Any, Literal, Optional
from dataclasses import dataclass
from datetime import datetime

class ExecutionMode(str, Enum):
    IMPERATIVE = "imperative"
    COMPILED_DAG = "compiled_dag"
    CLOSED_LOOP = "closed_loop"

BackendId = str          # "mock.default" | "real.<bench_id>" | "sim.<profile>"
JobId = str              # UUID or monotonic id issued by Job Manager
LeaseId = str            # Short-lived exclusive lock token

@dataclass(frozen=True)
class SessionLease:
    """Exclusive right to mutate a backend via primitives."""

    lease_id: LeaseId
    backend_id: BackendId
    holder: str            # "ui:<session>" | "sdk:<client_id>" | "job:<job_id>"
    mode: ExecutionMode
    issued_at: datetime
    expires_at: datetime     # renewed by heartbeat while holder is alive
    snapshot_ref: Optional[str]  # control repo/branch/commit pinned at acquire

@dataclass(frozen=True)
class JobRecord:
    """Orchestration envelope for compiled and long-running work."""

    job_id: JobId
    mode: ExecutionMode
    backend_id: BackendId
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    lease_id: Optional[LeaseId]
    submitted_at: datetime
    started_at: Optional[datetime]
    finished_at: Optional[datetime]
    trace_uri: Optional[str]   # execution log, optimization trace, artifacts
    error: Optional[str]
```

**Runtime JSON extensions** on `GET /api/lab-state` (mock shipped):

```json
{
  "system_status": "IDLE",
  "session_lease": {
    "lease_id": "lease_01J...",
    "backend_id": "mock.default",
    "holder": "job:job_01J...",
    "mode": "closed_loop",
    "expires_at": "2026-07-10T18:00:00Z"
  },
  "active_job_id": "job_01J..."
}
```

When no lease is held, `session_lease` is `null`.
---

## 3. The three execution modes

### 3.1. Mode A — Imperative

**Definition:** A Python process on a laptop or lab PC drives the experiment **step by step**: issue primitive → wait for runtime settle → read measurables → branch in user code.

**Examples:**

- Move mirror, wait `idle`, capture camera to HDF5.
- Loop 10 powers; if median &lt; threshold, call `OPTIMIZE`.
- Notebook exploration before committing to a batch job.

**Control flow location:** **Authoring machine** (client loop).

**Data flow:**

```text
┌─────────────┐    primitive + lease token     ┌──────────────────┐
│ Python SDK  │ ─────────────────────────────► │ cloud-labs API   │
│ (imperative)│ ◄───────────────────────────── │ (orchestration)  │
└─────────────┘    lab-state, refusal, telemetry └────────┬─────────┘
       │                                                  │
       │  optional: fetch MeasurableTensor               ▼
       │  (explicit pull — not every poll)        ┌──────────────────┐
       └──────────────────────────────────────────│ edge communicator │
                                                  └──────────────────┘
```

**Bandwidth rules (imperative):**

| Data | Direction | Policy |
|------|-----------|--------|
| Primitives | Client → orchestrator | **Low rate** (human/script scale); **requires lease** |
| `lab-state` JSON | Orchestrator → client | Poll at 2–10 Hz; **no raw image bytes** in JSON |
| Full tensors | Edge → client | **On demand only** via dedicated fetch API; never per idle poll |
| Analysis / plots | Client local | Default location for heavy post-processing |

**SDK shape (target):**

```python
with cloudlabs.session(backend="real.chicago_bench_1", mode="imperative") as lab:
    lab.load_snapshot(repo="optics-twin", branch="main")
    lab.move("tag_20", x=10, y=0, rotation=0)
    lab.wait_until_idle()
    frame = lab.measurable("tag_22", "camera_image").resolve()  # MeasurableTensor
```

**Mapping today:**

| Target | Today |
|--------|-------|
| Transport | `POST /api/command`, `GET /api/lab-state` |
| Wait | Client polls `system_status` until `IDLE` / not `BUSY` |
| Lease | **Implemented** — §4 |
| Tensor fetch | `GET /api/components/{tag}/measurables/{field}/tensor` (**Phase D**) |
| SDK | `lab.measurable(tag, field).resolve()` (**Phase D**) |
| Package | `cloudlabs` (`pip install -e ./packages/cloudlabs`) |

---

### 3.2. Mode B — Compiled DAG (batch job)

**Definition:** Client submits a **complete job envelope** once; orchestrator validates/transpiles; edge executes the sequence with minimal per-step client round-trips.

**Examples:**

- LLM-generated or imported recipe compiled to primitives.
- Parameter sweep: 50 configurations × capture × log.
- Multi-hour pipeline with retries and structured job trace.

**Control flow location:** **Orchestrator + edge** (client disconnected or passively monitoring).

**Data flow:**

```text
┌─────────────┐   submit JobSpec (DAG + snapshot + kernels)   ┌──────────────────┐
│ Python / UI │ ─────────────────────────────────────────────► │ Job Manager      │
└─────────────┘                                                │ + transpiler     │
       ▲                                                       └────────┬─────────┘
       │ telemetry (WebSocket / poll job status)                       │
       │ downsampled scalars, step index, logs                          ▼
       └─────────────────────────────────────────────────── ┌──────────────────┐
                                                            │ edge execution   │
                                                            └──────────────────┘
```

**Bandwidth rules (compiled DAG):**

| Data | Direction | Policy |
|------|-----------|--------|
| JobSpec | Client → orchestrator | **Once** at submit (includes snapshot ref, primitive list, optional kernel blobs — see [`SESSION_KERNELS.md`](./SESSION_KERNELS.md)) |
| Step telemetry | Orchestrator → client | **Downsampled** (step id, scalars, loss, thumbnail refs) |
| Full tensors | Edge → storage | Written to **job artifact store**; client downloads after job or on milestone |
| Mid-job client → edge | **Forbidden** for hardware primitives unless lease **transferred** to monitoring-only |

**JobSpec contents (target IR):**

```json
{
  "schema_version": 1,
  "backend_id": "real.chicago_bench_1",
  "mode": "compiled_dag",
  "snapshot": { "repo_id": "optics-twin", "commit": "abc123" },
  "steps": [
    { "primitive": "MOVE_COMPONENT", "target_id": "tag_20", "parameters": {} },
    { "primitive": "RECORD_MEASURABLES", "target_id": "tag_22", "parameters": {} }
  ],
  "limits": { "max_duration_s": 3600, "max_artifact_bytes": 1e10 }
}
```

**Mapping today:**

| Target | Today |
|--------|-------|
| Job Manager | **Implemented** (mock) — `POST /api/jobs/submit`, `GET /api/jobs/{id}`, Operations `/operations` |
| Declarative recipes | Recipe editor (UI); JSON import doc; compiled DAG mode on Job Manager |
| Telemetry | Job `progress` + optimization trace; poll via `wait_for_job` |
| Lease | **Implemented** — `/api/jobs/lease/*` — §4 |
| Session kernels | Inline `kernel_packages` on submit — [`SESSION_KERNELS.md`](./SESSION_KERNELS.md) |

---

### 3.3. Mode C — Closed-loop

**Definition:** A **tight feedback kernel** runs on the edge: propose tunables → capture measurables → compute loss → repeat, without per-eval client round-trips.

**Examples:**

- Ensemble optimization ([`ENSEMBLE_OPTIMIZATION.md`](./ENSEMBLE_OPTIMIZATION.md)).
- Future: beam stabilization, adaptive optics loop.

**Control flow location:** **Edge** (`lab_model/execution/optimization/session.py` + communicator backend).

**Data flow:**

```text
┌─────────────┐  OPTIMIZE (ensemble spec) + lease    ┌──────────────────┐
│ Client / UI │ ────────────────────────────────────► │ orchestration    │
└─────────────┘                                       │ system_status=   │
       ▲                                              │   OPTIMIZING     │
       │ optimization_session.trace (poll)           └────────┬─────────┘
       │ scalars: eval, loss, best_loss, terms, values          │
       └────────────────────────────────────────────────────────▼
                                                    ┌──────────────────┐
                                                    │ edge macro loop  │
                                                    │ (many evals/s)   │
                                                    │ tensors local    │
                                                    └──────────────────┘
```

**Bandwidth rules (closed-loop):**

| Data | Direction | Policy |
|------|-----------|--------|
| Spec | Client → orchestrator | **Once** at session start (`variables`, `objective`, `solver`) |
| Per-eval tensors | Stay on edge | Camera frames **must not** stream to client each eval |
| Per-eval telemetry | Orchestrator → client | **Scalars + trace rows** in `optimization_session` / `last_ensemble_optimization` |
| Objective math | Edge default | Metric registry + future compiled graphs execute **locally** |
| Custom DL model | Edge | Session / catalog TorchScript packages ([`SESSION_KERNELS.md`](./SESSION_KERNELS.md)); inference next to camera driver |

**Trace row shape (today, extended):**

```json
{
  "eval": 12,
  "loss": 0.0412,
  "best_loss": 0.0388,
  "terms": { "term_tag_22_camera_image": 0.02 },
  "block_id": "block_main",
  "u": [0.41, 0.52],
  "values": { "v_tag_20_m1": 1.23, "v_tag_19_x": -116.5 }
}
```

**Mapping today:**

| Target | Today |
|--------|-------|
| Entry | `POST /api/jobs/submit` closed-loop `OPTIMIZE`, or `POST /api/command` `action=OPTIMIZE` |
| Status | Job record + `system_status=OPTIMIZING`, `optimization_session` |
| Edge loop | `run_ensemble_optimization` in `lab_model/execution/optimization/session.py` |
| Live UI | Optimization mode telemetry panel; Operations job progress |
| Lease | **Job/session lease** (export session packages → release → job acquires) — §4 |
| SDK | `lab.run_cobyla(...)` / `lab.run_optimize(...)` |

---

## 4. Exclusive session lease (mandatory — fixes split personality)

### 4.1. Problem statement *(historical — lease now ships on mock)*

The SDK / Twin lease model asked whether scripts and UI can share the robot. **Before Phase B:**

- Primitives were refused when `system_status` is `BUSY` or `OPTIMIZING` ([`state_machine.py`](../backend/lab_model/coordinator/state/state_machine.py)).
- There was **no backend-level exclusive lock**.
- A **compiled job** and an **imperative HTTP script** could interleave primitives on `real.<bench>` if both saw `IDLE`.
- **UI clicks** and **notebook cells** used the same `POST /api/command` path — last-writer-wins on a real table is **unsafe**.

This was the **split personality bug**: two personalities (Job Manager vs bare HTTP client) talking to one body (hardware) without a single arbiter. **Today on mock:** exclusive lease via `/api/jobs/lease/*` + Job Manager; real-bench hardening still follows the same contract.

### 4.2. Rule (normative, Phase B+)

> **Exclusive Session Lock:** At most **one** active `SessionLease` per `BackendId`. Every primitive dispatch — UI, SDK imperative, job runner, closed-loop entry — must present a valid `lease_id` that matches the active lease. Otherwise: **HTTP 409** with `detail="backend_locked"` and the current `holder`.

**Corollaries:**

1. **`cloudlabs.connect()`** acquires a lease (`POST /api/jobs/lease/acquire`) and attaches `lease_id` to every command.
2. **UI** acquires a lease when entering “control mode” on a backend, or piggybacks on operator login session — design TBD; **never** silent unlimited access on real benches.
3. **Batch jobs** acquire lease at `job.start()`; hold until `succeeded|failed|cancelled`.
4. **Closed-loop** `OPTIMIZE` runs **inside** the job’s lease (or imperative session’s lease); `system_status=OPTIMIZING` is a **sub-state**, not a substitute for lease.
5. **Mock backends** may relax lease (`MOCK_SINGLE_USER=1`) for CI; **real backends must not.**

### 4.3. Lease lifecycle

```text
acquire(backend_id, holder, mode, snapshot_ref?)
    → lease_id, expires_at
    → runtime.session_lease populated
    → UI: show banner "Held by sdk:notebook-12" / disable conflicting controls

heartbeat(lease_id) every T seconds
    → extends expires_at

release(lease_id)
    → session_lease cleared
    → if system_status was OPTIMIZING, abort or complete per policy

expire(lease_id)
    → automatic release; optional hardware safe-stop primitive
```

**Expiry defaults (proposal):**

| Holder | TTL | Heartbeat |
|--------|-----|-----------|
| UI session | 30 min | user activity |
| Imperative SDK | 10 min | client heartbeat every 60 s |
| Batch job | `max_duration_s` in JobSpec | orchestrator internal |
| Closed-loop | min(job TTL, solver `max_total_evals` bound) | eval progress |

### 4.4. Interaction with `system_status`

`system_status` *(today)* in [`holding.py`](../backend/lab_model/language/domain/holding.py):

| Value | Meaning | Lease interaction |
|-------|---------|-------------------|
| `IDLE` | No primitive in flight | Lease **may** be held (exclusive intent without motion) |
| `BUSY` | Single primitive executing | Requires lease |
| `OPTIMIZING` | Closed-loop session | Requires lease; sub-state of closed-loop mode |
| `HOLDING` | Robot holding part | Requires lease; refusals for conflicting moves |
| `TELEOP` | Per-component live control | **Policy TBD:** teleop may require lease or per-tag grant |

**Refusal order (target):**

1. Valid `lease_id` for this `backend_id`?
2. Existing `state_machine` refusals (`refuse_if_busy`, holding, stored, …)?
3. Transpiler static limits (bounds, rate)?

### 4.5. API sketch (planned)

| Endpoint | Purpose |
|----------|---------|
| `POST /api/session/acquire` | `{ backend_id, holder, mode, snapshot? }` → `SessionLease` |
| `POST /api/session/heartbeat` | `{ lease_id }` → new `expires_at` |
| `POST /api/session/release` | `{ lease_id }` |
| `POST /api/jobs/lease/acquire` | **Implemented (Phase B)** — same contract as session acquire |
| `POST /api/jobs/lease/release` | **Implemented (Phase B)** |
| `POST /api/jobs/lease/heartbeat` | **Implemented (Phase B)** |
| `POST /api/command` | **Requires** header `X-CloudLabs-Lease: <lease_id>` or body field when backend is locked |
| `POST /api/jobs/submit` | **Implemented (Phase C)** — creates `JobRecord`, acquires lease on start |
| `GET /api/jobs/{id}` | **Implemented (Phase C)** — status + progress telemetry |
| `GET /api/jobs` | **Implemented (Phase C)** — list recent jobs |
| `POST /api/jobs/{id}/cancel` | **Implemented (Phase C)** — best-effort cancel |
| `GET /api/backends` | **Implemented (Phase C)** — operations dashboard backend catalog |
| `GET /api/kernels` | **Implemented (Phase F mock)** — edge kernel catalog |

---

## 5. Lab surfaces (Twin, Operations, Catalog)

**Canonical policy:** [`LAB_SURFACES_VC_AND_INITIALIZATION.md`](./LAB_SURFACES_VC_AND_INITIALIZATION.md).

Three pages share **one queue** (Job Manager + session lease). They differ in what the operator may **do**, not in what hardware exists.

| Surface | URL | Role | Lease? |
|---------|-----|------|--------|
| **Twin UI** | `/twin` | Direct control + **local** VC | **Yes** |
| **Landing** | `/` | Brand entry / surface picker | No |
| **Operations** | `/operations` | Job tracking + read-only twin mirror | No |
| **Catalog** | `/catalog` (planned) | Remote **approved** pins + backend metadata | No |

### 5.1. Operations — job tracking only (target)

[`/operations`](../frontend/operations.html) is a **control room monitor**, not a second Twin UI.

**Includes:**

- **Backends panel** — per `BackendId`: communicator type, lease holder, `active_job_id`, health
- **Jobs panel** — queue, status, progress, cancel
- **Job telemetry** — closed-loop trace, compiled DAG step list, raw job JSON
- **Digital twin** — read-only mirror (same renderer as Twin UI)

**Excludes (refactor from Phase C.1):** repo/branch picker, reconcile preview/submit, stash/commit, dirty-tree blockers. Those belong on **Twin UI** (local VC) or **Catalog** (remote pins).

### 5.2. Twin UI — direct control

[`/`](../frontend/index.html) is the only interactive surface that issues primitives. Requires lease; shows local control graph (repos, branches, commits, stash). Optimization staging and step-by-step reconcile run here.

### 5.3. Catalog — remote store (planned)

Read-only browse of **owner-approved** configuration pins and backend parameters. Local `control/` repos do **not** auto-appear. Publish = `request_publish` → owner approves → `CatalogPin`.

### 5.4. Jobs panel (**implemented — Phase C.2**)

Operations dashboard at [`/operations`](../frontend/operations.html):

| Column | Source |
|--------|--------|
| Job id | `JobRecord` |
| Mode | `ExecutionMode` |
| Status | queued / running / … |
| Progress | step index, eval count, best loss |
| Holder | `SessionLease.holder` |
| Actions | cancel |

**Live digital twin (C.2):** Center panel reuses Twin UI modules (`render.js`, `lab-state.js`, `store.js`, `guides.js`) with the same `#optical-table` canvas. Read-only shield blocks edits; lab-state polls at 500ms (100ms during `OPTIMIZING`). Running jobs sync `store.pendingCommands` / `pendingActions` from job progress — same highlight path as reconcile runner and command console.

**Job telemetry:** Closed-loop trace table (`trace_tail`, `best_loss`); compiled DAG step list from `job.spec.steps` + `progress.step_index`.

**Auto-follow:** When `active_job_id` is set on the backend, operations selects that job automatically.

### 5.5. Initialization policy (default: `force_reconcile`)

Scripts and jobs declare a `snapshot` and **`initialization_policy`**. Default **`force_reconcile`**: acquire lease, reconcile bench to pinned commit, **ignore** dirty-vs-applied git blockers. Twin UI manual checkout may still require stash/commit unless operator confirms force.

See [`LAB_SURFACES_VC_AND_INITIALIZATION.md`](./LAB_SURFACES_VC_AND_INITIALIZATION.md) §7.

### 5.6. Hardware reconcile as a visible macro (Phase B+)

**Problem:** ``lab.reconcile_hardware()`` (checkout / branch run from Twin UI or SDK) can issue long motor and alignment sequences. Real stages exhibit **hysteresis, backlash, and environmental drift** — invisible background reconcile can jam a table or leave micron-scale alignment error before user script code runs.

**Rule:** Reconcile must be a **monitored primitive macro**, never a silent side effect:

- Job dashboard and imperative SDK show explicit telemetry: ``Status: Reconciling bench coordinates…``, step index, per-axis deltas, timeout/failure reason.
- User scripts block at ``load_snapshot()`` / ``reconcile_hardware()`` until reconcile completes or surfaces a typed error — main experiment code must not start on a failed baseline.
- Reconcile steps should respect approach-direction policy where catalog parameters define it (future: ``parameters.stage.backlash_compensation``).

### 5.7. Post-job commit hook (**G.7**)

After a **succeeded** job (not cancelled), an optional ``on_success`` block may snapshot the bench into local VC:

```json
{
  "mode": "closed_loop",
  "on_success": {
    "commit_configuration": {
      "repo_id": "laser-cavity",
      "branch": "main",
      "message": "after overnight sweep"
    }
  }
}
```

Failures are recorded in ``job.result.post_commit_error`` without failing the job. Telemetry phase: ``post_commit``.

**Twin UI:** Optimization mode stage **Run** exposes checkboxes for reconcile-to-applied-commit (snapshot + ``force_reconcile``) and optional post-job commit; Results and the live status bar show commit id or error when present.

---

## 6. Where compute runs (decision matrix)

| Computation | Imperative | Compiled DAG | Closed-loop |
|-------------|------------|--------------|-------------|
| User Python `if` / loops | Client | DAG pre-planned | N/A (solver loop only) |
| Primitive dispatch | Orchestrator | Orchestrator | Orchestrator (once) |
| Metric / loss | Client *or* edge | Edge if in spec | **Edge only** |
| Full image CNN (future) | Client fetch + local GPU default | Edge if kernel bundled | **Edge required** for in-loop |
| CSV / HDF5 archive | Client | Job artifacts service | Job artifacts service |
| VC commit of final tunables | Operator save | ``on_success.commit_configuration`` job hook (**G.7**) | Runtime commit at session end; optional VC hook via job spec |

**Default rule:** If it runs **inside an eval loop** (&lt; 100 ms budget), it **must** run on the **edge**. If it runs **once per minute**, client-side is fine.

---

## 7. Migration from today

| Today | Gap | Target mode behavior |
|-------|-----|----------------------|
| UI ensemble optimization | **Job submit** (`/api/jobs/submit`) | Closed-loop inside lease + job record (**implemented**) |
| UI other primitives | No lease | UI acquires lease on real backend |
| `GET /api/lab-state` poll | No `session_lease` field | Expose lease + `active_job_id` (**implemented**) |
| Recipe editor | UI-only sequence | Export as compiled DAG job |
| Mock communicator | Safe to share | Lease optional (config flag) |

**Implemented recently (closed-loop telemetry):** `optimization_session.trace` with `eval`, `loss`, `values` — use as **Job telemetry prototype** ([`optimize_ensemble.py`](../backend/lab_model/execution/orchestration/optimize_ensemble.py)).

**Implemented recently (live poll):** Mock ensemble releases state lock between evals so `lab-state` updates during `OPTIMIZING` — required for closed-loop monitoring.

---

## 8. Open decisions (Phase A)

- [ ] **Lease storage:** runtime JSON only vs Job Manager DB?
- [ ] **UI without lease on mock:** default allow for dev ergonomics?
- [ ] **Teleop vs lease:** does teleop require exclusive backend lease or per-tag capability tokens?
- [ ] **Job cancel:** best-effort stop vs guaranteed safe retract primitive?
- [ ] **Imperative + DAG coexistence:** can a held lease switch `ExecutionMode` mid-session?
- [ ] **WebSocket vs poll** for job telemetry v1?

---

## 9. Glossary

| Term | Meaning |
|------|---------|
| **Imperative mode** | Client-driven step-by-step primitives |
| **Compiled DAG** | Submitted multi-step job executed with minimal client contact |
| **Closed-loop** | Edge-resident tight feedback kernel (optimization, servos) |
| **Session lease** | Exclusive backend mutation token |
| **Job Manager** | Orchestration component owning queue, lease, job status |
| **Telemetry path** | Downsampled status to clients (parallel to fast control path) |
| **Split personality bug** | HTTP clients bypassing job queue → interleaved hardware commands |

---

*End of Phase A execution spec. Implementation of Phase B (SDK + lease) must not ship without §4.*
