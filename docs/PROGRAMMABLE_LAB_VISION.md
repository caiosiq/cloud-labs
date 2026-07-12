# Programmable lab vision — from digital twin to optical OPU

**Status:** Phase A specification; **Phase B–D implemented (mock-first); Phase C Job Manager + lease complete on mock; Phase E objective compiler implemented; Phase F catalog + session kernels (mock); Phase G surfaces + catalog pins shipped**  
**Last updated:** 2026-07-11  
**Audience:** cloud-labs maintainers, experimentalists writing scripts, anyone extending backends or the SDK  

**Related docs:**

- [`EXECUTION_MODES.md`](./EXECUTION_MODES.md) — imperative vs compiled job vs closed-loop; exclusive session lock
- [`Run_CloudLab_Scripts.md`](./Run_CloudLab_Scripts.md) — Python SDK (`connect` / `prepare` / `run_optimize`)
- [`SESSION_KERNELS.md`](./SESSION_KERNELS.md) — author-defined TorchScript packages (`session.*`)
- [`FUTURE_STEPS_DISTRIBUTED_AND_SDK.md`](./FUTURE_STEPS_DISTRIBUTED_AND_SDK.md) — pip SDK extract, 3-tier topology, then real/kernels/fluent backlog
- [`ENSEMBLE_OPTIMIZATION.md`](./ENSEMBLE_OPTIMIZATION.md) — closed-loop optimization (variables / objective / solver)
- [`CONTROL_RUNTIME_AND_VERSIONING.md`](./CONTROL_RUNTIME_AND_VERSIONING.md) — RuntimeManager, ControlManager, configuration commits
- [`OBJECTIVE_GRAPH.md`](./OBJECTIVE_GRAPH.md) — declarative objective graphs / compiler (Phase E)
- [`universal_component_architecture.md`](./universal_component_architecture.md) — control plane vs data plane, component model
- [`capability_contract.md`](./capability_contract.md) — catalog JSON, primitives, hardware boundary
- [`CLOUDLAB_CONTRACT.md`](./CLOUDLAB_CONTRACT.md) — cloud-labs ↔ `lab_automation` split
- [`../backend/lab_model/README.md`](../backend/lab_model/README.md) — tunables / measurables today
- [`../backend/lab_communicator/README.md`](../backend/lab_communicator/README.md) — mock / real backends

> **Naming:** **`ControlManager`** (cloud-labs) = configuration version history.  
> **`lab_communicator`** = runtime bridge to hardware (mock, real, future named backends).  
> **`lab_automation`** (sibling repo) = low-level drivers and legacy strategies.  
> Do **not** conflate experiment VC with optimization **session** mechanics.

---

## 1. Executive summary

Cloud-labs today is an excellent **operator interface**: move components on a breadboard, watch cameras, run ensemble optimization, and version configuration through git-like control repos.

The next strategic step is to make the same lab a **programmable execution backend** — an **Optical Processing Unit (OPU)** — addressable from Python scripts, batch jobs, and notebooks, with the UI remaining the debugger and trust layer.

The guiding analogy:

| Quantum stack | Cloud-labs target |
|---------------|-------------------|
| **Qiskit** (author on laptop) | **`cloudlabs` Python SDK** — build recipes, math on measurables, control flow |
| **Transpiler + IBM Runtime** | **Job compiler + orchestration** — validate, lower macros, assign edge compute |
| **IBM Quantum device** | **Named lab backend** — motors, cameras, robot, local low-latency loops |

Scripts do not “run on the laptop” in the sense of moving mirrors locally. They **author** work; the **orchestration layer** schedules it; the **edge layer** executes primitives and tight feedback loops next to hardware.

This document defines the **vision**, the **three architectural layers**, and the **three-pillar component model** that every script, job, UI panel, and catalog entry must share.

---

## 2. The Qiskit ↔ optical OPU analogy

### 2.1. What transfers cleanly

- **Authoring vs execution.** Writing `qc.h(0)` does not rotate a physical qubit on your laptop. Writing `mirror.motor[1] += 0.1` must not assume immediate hardware motion unless a **session** is bound to a **backend** with an **exclusive lease** (see [`EXECUTION_MODES.md`](./EXECUTION_MODES.md)).
- **Backends.** `IBMQ.get_backend("ibm_kyoto")` maps to `cloudlabs.connect(backend="real.chicago_bench_1")`. Backends are **communicator profiles**, not just `MOCK` vs `REAL`.
- **Transpilation.** Abstract operations (touch-and-go, bounds, metric graphs) lower to **primitives** the edge understands.
- **Version anchor.** Experiments need a known starting configuration — implemented today as **control repos / branches / commits** ([`CONTROL_RUNTIME_AND_VERSIONING.md`](./CONTROL_RUNTIME_AND_VERSIONING.md)); scripts must expose `load_snapshot(repo, branch | commit)`.

### 2.2. What is different from quantum (do not ignore)

| Concern | Quantum | Optical bench |
|---------|---------|---------------|
| State size | Small Hilbert space | **Megapixel tensors**, high-rate scopes |
| Control loop | Discrete gates | **Continuous** motors, teleop, holding |
| Human in loop | Rare | **Common** — knobs, cameras, alignment by eye |
| Safety | Cryo / pulse limits | **Physical collision**, gripper, beam paths |
| Data plane | Pulses + counts | **Images, waveforms**, files on disk |

Therefore: measurables need a **tensor contract**, but bandwidth rules require **where** math runs (laptop vs edge) to be explicit per execution mode — not an implementation detail.

### 2.3. Non-goals (vision scope)

- Replacing the UI with scripts only.
- Running arbitrary unreviewed user Python on the edge without a sandbox story (staged later).
- Collapsing `lab_automation` into cloud-labs (repos stay decoupled per [`CLOUDLAB_CONTRACT.md`](./CLOUDLAB_CONTRACT.md)).

---

## 3. Three architectural layers

```text
┌──────────────────────────────────────────────────────────────────────────┐
│ LAYER 1 — AUTHORING (client: laptop, notebook, CI)                       │
│                                                                          │
│  • cloudlabs Python package (future): Lab, ComponentRef, JobBuilder        │
│  • Builds: primitives, control flow, objective/metric graphs           │
│  • May be imperative (step-by-step) or declarative (compiled job DAG)    │
│  • Does NOT hold hardware locks; submits work to Layer 2                 │
└───────────────────────────────┬──────────────────────────────────────────┘
                                │ HTTPS / in-process API
                                │  + session lease token (required)
┌───────────────────────────────▼──────────────────────────────────────────┐
│ LAYER 2 — ORCHESTRATION (cloud-labs backend: FastAPI + lab_model)        │
│                                                                          │
│  • Job Manager: queue, lease, status, telemetry fan-out (mock shipped) │
│  • RuntimeManager: live JSON working tree (today: LabCommunicator state) │
│  • ControlManager: configuration commits, branches, snapshots            │
│  • Transpiler / preflight: bounds, path resolve, macro expansion         │
│  • Primitive dispatch: POST /api/command → orchestration modules       │
│  • Refusal layer: system_status, holding, stored parts (state_machine)   │
└───────────────────────────────┬──────────────────────────────────────────┘
                                │ in-process calls (same machine) or RPC
┌───────────────────────────────▼──────────────────────────────────────────┐
│ LAYER 3 — EDGE EXECUTION (lab_communicator + lab_automation)             │
│                                                                          │
│  • Named backend: mock | real.<bench_id> | sim.<profile> (future)        │
│  • Actuation: motors, robot, stages                                      │
│  • Capture: camera frames, scopes, power readbacks → MeasurableTensor    │
│  • Closed-loop kernels: ensemble eval, stabilization (low latency)       │
│  • Persists runtime JSON; optional disk artifacts (images, traces)       │
└──────────────────────────────────────────────────────────────────────────┘
```

### 3.1. Layer 1 — Authoring (mapping today → target)

| Concept | Today | Target |
|---------|-------|--------|
| UI commands | `POST /api/command` from browser | Same primitive JSON; UI holds no special privilege |
| Scripts | [`Run_CloudLab_Scripts.md`](./Run_CloudLab_Scripts.md) sketch | `pip install cloudlabs`; typed `LabSession` |
| Recipes | Recipe editor, `import_json.md` | Compiled to same job/primitive IR |
| Analysis | External notebooks | `MeasurableTensor` resolve / symbolic graph |

### 3.2. Layer 2 — Orchestration (mapping today → target)

| Concept | Today | Target |
|---------|-------|--------|
| Live state | `GET /api/lab-state` ← `LabCommunicator.get_lab_state()` | + `session_lease` / active job fields on mock |
| Primitives | `lab_model/orchestration/*`, `lab_model/primitives/*` | Unchanged entry points; gated by lease |
| Optimization session | `system_status=OPTIMIZING`, `optimization_session` | Instance of **closed-loop** mode (see execution doc) |
| VC | `ControlManager`, `/api/control/*` | Script API: `load_snapshot(...)` / `catalog_pin=` |
| Concurrency | `system_status` refusals only | **+ Job Manager exclusive lease** (`/api/jobs/lease/*`) |

### 3.3. Layer 3 — Edge (mapping today → target)

| Concept | Today | Target |
|---------|-------|--------|
| Backends | `MockLabCommunicator`, `RealLabCommunicator` | **Backend registry** with stable IDs |
| Ensemble eval | `lab_model/optimization/session.py` + `lab_communicator/*/ensemble.py` | Standard edge kernel for closed-loop jobs |
| Capture | Image paths in `measurables.camera_image` | **`MeasurableTensor`** + metadata (**Phase D implemented**) |
| Hardware | `OpticalExperiment` in `lab_automation` | Unchanged; catalog-driven per contract |

---

## 4. Three-pillar component model

Every **lab component** (catalog entry + runtime JSON slice) is described by three **orthogonal** pillars. Scripts, UI, transpiler, and hardware validators must use the same vocabulary.

```text
┌─────────────────────────────────────────────────────────────────────────┐
│                         LAB COMPONENT (tag_id)                          │
├─────────────────────────────────────────────────────────────────────────┤
│ 1. TUNABLES — commanded intent (read/write)                             │
│    What the operator or script may set.                                 │
│    Stored in runtime: statecontrol.tunables.*                            │
│    Examples: nominal_pose, nominal_motor_positions, laser_power_setpoint │
├─────────────────────────────────────────────────────────────────────────┤
│ 2. MEASURABLES — observed dynamic state (read-only at API boundary)     │
│    What the bench + environment produce; depends on tunables & coupling.  │
│    Stored in runtime: statecontrol.measurables.*                        │
│    Examples: camera_image, output_power_readback_mw, temperature_c      │
│    Target type: MeasurableTensor (see §4.4)                             │
├─────────────────────────────────────────────────────────────────────────┤
│ 3. PARAMETERS — static hardware / catalog truth (read-only, immutable)  │
│    What characterizes the device; used for capability checks & UI.      │
│    Stored in catalog: capabilities.parameters.* (target schema)         │
│    Today (legacy): properties.* display fields — migrate to parameters  │
└─────────────────────────────────────────────────────────────────────────┘
```

### 4.1. Tunables

**Definition:** Values the control system **commands**. Mutations flow through **primitives** (or RuntimeManager mutation kinds), never direct JSON patching from clients.

| Field | Type | Notes |
|-------|------|-------|
| **Storage path** | `statecontrol.tunables.<leaf>` | Per [`backend/lab_model/domain/component.py`](../backend/lab_model/domain/component.py) |
| **Catalog declaration** | `capabilities.statecontrol.tunables[field]` | `min`, `max`, `unit`, `default`, `widget` (UI-only) |
| **Optimization variables** | `VariableRef.path` e.g. `tunables.nominal_motor_positions.1` | Ensemble spec; preflight resolves paths |
| **Versioned in VC?** | **Yes** — configuration commits include tunables | Excludes `placement.mode` runtime overlay per ensemble design |

**Script access (target API):**

```python
comp = lab.component("tag_20")
comp.tunables.nominal_motor_positions[1] = 0.5  # records intent; issues primitive on commit
```

**Boundary rule:** Tunables are **Configuration** in VC glossary ([`CONTROL_RUNTIME_AND_VERSIONING.md`](./CONTROL_RUNTIME_AND_VERSIONING.md) §2.2).

### 4.2. Measurables

**Definition:** Values the lab **observes**. Scripts and optimizers **read** measurables; they do not assign them (except via `RECORD_MEASURABLES` / capture primitives that refresh observations from hardware).

| Field | Type | Notes |
|-------|------|-------|
| **Storage path** | `statecontrol.measurables.<field>` | e.g. `camera_image`, scalar readbacks |
| **Catalog declaration** | `capabilities.statecontrol.measurables[field]` | Declares type, units, capture primitive |
| **Objective sources** | `objective.terms[].source.path` | Ensemble metrics read measurables, not tunables |
| **Versioned in VC?** | **No** — observations pin / compare only | Receipts, not branchable intent |

**Boundary rule:** Measurables are **Observations** in VC glossary (§2.3). Checking out an old commit does **not** restore old physics in a camera frame.

**Today:** Camera measurables often store **references** (`path`, `format`, `timestamp`) to PNGs on disk — not inline tensors. Optimization uses a **metric registry** ([`lab_model/optimization/metrics`](../backend/lab_model/optimization/metrics/)) over derived scalars (centroid, normalized power).

**Target:** Every measurable materializes as a **`MeasurableTensor`** (§4.4) when a script or edge kernel needs numerical data.

### 4.3. Parameters

**Definition:** **Static** facts about a component instance — manufacturer limits, sensor geometry, supported modes. Parameters answer: *can this backend run my pipeline?* without touching hardware.

| Field | Type | Notes |
|-------|------|-------|
| **Storage (target)** | `capabilities.parameters.<name>` in **catalog only** | Never in per-session runtime mutations |
| **Today (legacy)** | `properties.*` on catalog row | Display specs; **migrate** to `parameters` with typed schema |
| **Runtime copy** | Optional read-only mirror in `GET /api/lab-state` for UI | Derived from catalog at boot; not user-editable |

**Example parameters (oscilloscope):**

```json
{
  "max_sample_rate_hz": 1e9,
  "voltage_range_v": [-5.0, 5.0],
  "channel_count": 4,
  "manufacturer": "Keysight",
  "model": "MSOX3024T"
}
```

**Boundary rule:** Parameters are **not** tunables (you don't "set" max sample rate during an experiment) and **not** measurables (they don't change when you move a mirror).

### 4.4. MeasurableTensor (target contract — summary)

Full wire format lives in a future `MEASURABLE_TENSOR_SPEC.md`. Vision-level shape:

```python
@dataclass(frozen=True)
class MeasurableTensor:
    """Canonical numerical carrier for a measurable at analysis boundary."""

    data: np.ndarray | LazyRef          # materialized or edge-resident
    dtype: str                          # e.g. "float32"
    shape: tuple[int, ...]
    axes: dict[str, str]                # {"y": "pixel", "x": "pixel", "c": "channel"}
    units: dict[str, str]               # per-axis or scalar unit
    domain: Literal["spatial", "time", "scalar", "spectrum", "other"]
    tag_id: str
    field: str                          # catalog measurable field id
    provenance: dict[str, Any]          # capture_id, timestamp, backend_id
```

**Symbolic vs materialized:** At authoring time, `comp.measurables.camera_image` may be a **handle** (graph node). Materialization policy depends on **execution mode** ([`EXECUTION_MODES.md`](./EXECUTION_MODES.md)).

---

## 5. Backends and reproducibility (vision hooks)

### 5.1. Backend registry (target)

A **backend** is a named, discoverable execution target:

```python
BackendId = str  # e.g. "mock.default", "real.chicago_bench_1", "sim.mujoco_cavity_v2"
```

| Backend ID | Communicator class | Purpose |
|------------|-------------------|---------|
| `mock.default` | `MockLabCommunicator` | Synthetic landscape, CI, UI dev |
| `real.<bench_id>` | `RealLabCommunicator` | Physical table profile |
| `sim.<profile>` | Future simulation bridge | Repeatable physics without hardware |

**Surfaces:** **Landing** (`/`) = Cloud Labs entry. **Twin UI** (`/twin`) = direct control + local VC. **Operations** (`/operations`) = job monitor only. **Catalog** (`/catalog`) = remote approved pins — see [`LAB_SURFACES_VC_AND_INITIALIZATION.md`](./LAB_SURFACES_VC_AND_INITIALIZATION.md).

### 5.2. Configuration snapshot (target)

Before any non-mock job or imperative session on a shared bench:

```python
lab = cloudlabs.connect(backend="real.chicago_bench_1")
lab.load_snapshot(repo_id="optics-twin", branch="golden-cavity-2026-07")  # local
lab.load_snapshot(catalog_pin="laser-cavity-main")  # remote approved pin
lab.reconcile_hardware()  # default: force_reconcile under lease
```

**Local VC** (commits, forks, stash) stays on the deployment `control/` tree. **Remote catalog** pins are owner-approved only — `request_publish`, not auto-created when the bench is used. Maps to **ControlManager** checkout + session reconciliation on Twin UI.

---

## 6. Relationship to existing subsystems

| Subsystem | Role in programmable lab |
|-----------|-------------------------|
| **Primitive JSON** (`action`, `target_id`, `parameters`) | Lowest-level orchestration IR; UI and SDK share it |
| **Ensemble optimization** | First **closed-loop** product with trace + metrics |
| **Metric registry** | Seed of **transpiler** for objective math; extend with ops / kernels |
| **Preflight** (`optimize_ensemble` dry-run paths) | Seed of **static validation** pass |
| **lab-state polling** | Seed of **job telemetry** for imperative + closed-loop |
| **Control repos** | **Snapshot anchor** for reproducible scripts |
| **Capability catalog** | Declares tunables, measurables, primitives; gains **parameters** |

---

## 7. Phased delivery (cross-reference)

Phase A (this document + [`EXECUTION_MODES.md`](./EXECUTION_MODES.md)) — **document & align**.

| Phase | Deliverable | Depends on |
|-------|-------------|------------|
| **B** | Imperative `cloudlabs` package + **session lease** | Execution modes §3 (**implemented** mock-first) |
| **C** | Job object + operations dashboard | Job Manager (**implemented** mock — `/api/jobs/*`, Operations UI) |
| **D** | `MeasurableTensor` in communicators | Vision §4.4 (**implemented**) |
| **G** | Surfaces split + init policy + catalog/publish | [`LAB_SURFACES_VC_AND_INITIALIZATION.md`](./LAB_SURFACES_VC_AND_INITIALIZATION.md) (**shipped**) |
| **E** | Declarative objective graphs / compiler | Ensemble + metrics (**MVP implemented** — see [`OBJECTIVE_GRAPH.md`](./OBJECTIVE_GRAPH.md)) |
| **F** | Edge-bundled + session TorchScript kernels | Catalog + [`SESSION_KERNELS.md`](./SESSION_KERNELS.md) (**mock**) |

---

## 8. Open decisions (Phase A)

- [ ] **Package name:** `cloudlabs` vs `cloud_labs` vs namespace under `backend/`?
- [ ] **Parameters schema:** new `capabilities.parameters` vs migrate `properties` in place?
- [ ] **Backend ID authority:** who registers `real.<bench_id>` — env file, catalog, or ops DB?
- [x] **Reconcile policy:** default **`force_reconcile`** for jobs/scripts; Twin UI manual checkout may block on dirty — see surfaces doc §7
- [x] **Symbolic measurables:** lazy graph deferred; Phase E ships field-based objective compiler + preflight (see [`OBJECTIVE_GRAPH.md`](./OBJECTIVE_GRAPH.md))

---

## 9. Glossary

| Term | Meaning |
|------|---------|
| **OPU** | Optical Processing Unit — metaphor for the physical bench as a compute device |
| **Backend** | Named lab execution profile (communicator + config) |
| **Primitive** | Atomic commanded action (`MOVE_COMPONENT`, `OPTIMIZE`, …) |
| **Job** | Orchestration unit with id, status, lease, and telemetry (`/api/jobs/*`) |
| **Session lease** | Exclusive right to issue primitives on a backend (`/api/jobs/lease/*`) |
| **Snapshot** | ControlManager commit or branch head projected into runtime |
| **MeasurableTensor** | Typed numerical observable with metadata |
| **Transpiler** | Lowers authored script/job to validated primitive + edge compute IR |

---

*End of Phase A vision spec. Implementation PRs must link here and to [`EXECUTION_MODES.md`](./EXECUTION_MODES.md).*
