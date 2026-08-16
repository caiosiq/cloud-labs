# Ensemble optimization — design & implementation roadmap

**Status:** design approved; **Phases 1–3 implemented** (mock end-to-end + operator docs/UI) — Phase 4 real bench pending  
**Last updated:** 2026-07-03  
**Audience:** cloud-labs maintainers, UROP, anyone extending `OPTIMIZE` beyond single-tag strategies  

**Related docs:**

- [`LAB_SURFACES_VC_AND_INITIALIZATION.md`](./LAB_SURFACES_VC_AND_INITIALIZATION.md) — Runtime vs Configuration; commits
- [`EDGE_CONTRACT_AND_UC_LIVE_PLANE.md`](./EDGE_CONTRACT_AND_UC_LIVE_PLANE.md) — edge / UC boundary
- [`EDGE_UC_MIGRATION_ROADMAP.md`](./EDGE_UC_MIGRATION_ROADMAP.md) — hardware edge phases
- [`../backend/lab_model/README.md`](../backend/lab_model/README.md) — tunables / measurables
- [`../backend/lab_model/execution/orchestration/optimize.py`](../backend/lab_model/execution/orchestration/optimize.py) — ensemble OPTIMIZE orchestrator

> **Naming:** **`ControlManager`** (cloud-labs) owns configuration history.  
> **`lab_automation`** (`OpticalExperiment`) runs the inner optimization loop on hardware.  
> Do not conflate experiment VC with optimization session mechanics.

---

## 1. Executive summary

Today, `OPTIMIZE` is **one component, one strategy, one scalar score**. Fine alignment of an optical bench is inherently **multi-variable and multi-component**: several mirror motors, optional robot-held poses, one or more cameras, and power readouts—all coupled through the same beam.

**Ensemble optimization** replaces monolithic strategy names with three explicit layers:

| Layer | Question |
|-------|----------|
| **Variables** | Which tunables may change, on which tags, in what physical units? |
| **Objective** | What scalar loss do we minimize from measurables? |
| **Solver** | How do we propose the next settings (block COBYLA, coordinate descent, …)? |

The run executes as **one cloud-labs primitive** → **one `OPTIMIZING` session** → **one inner macro loop in `lab_automation`** → **one `commit_optimization_ensemble_complete` at the end**. Intermediate steps do not touch version control.

Three **physical** constraints are first-class in the design (not afterthoughts):

1. **Heterogeneous normalization** — mirror motors (±3°) and robot translations (±50 mm) must not share raw numeric space in COBYLA; the engine maps all variables to a **unit hypercube** \([0,1]^N\) for the solver and **denormalizes** before actuation.
2. **Touch-and-go** — robot-held pose changes (`physical_type: invasive_discrete`) must **release the gripper, settle, then measure**, or the loss reflects clamping stress, not relaxed alignment. Re-engagement is **lazy** (see §7.2)—never squeeze the part again unless the solver requests a new pose.
3. **Holding-pattern clearance** — during **continuous-only** blocks (mirrors, stages), the robot arm must be **fully disengaged, retracted to a safe home pose, and clear of the optical path**. A gripper still clamping a lens while mirrors iterate causes beam occlusion and static-clamping distortion that invalidates mirror alignment when the part finally relaxes.

---

## 2. What exists today (baseline)

```text
POST /api/command  { action: OPTIMIZE, target_id, parameters: { strategy: NEWTON|COBYLA, ... } }
       │
       ▼
run_optimize_component()          # lab_model/execution/orchestration/optimize.py
       │  null measurables (one tag)
       │  system_status = OPTIMIZING
       │  optimization_target_id = tag
       ▼
lab_automation strategy run       # NewtonPlacementStrategy_cloudlab, CobylaAlignmentStrategy_cloudlab
       │
       ▼
commit_optimization_complete()    # one tag, placement.mode + score
```

**Gaps relative to ensemble design:**

| Area | Today | Target |
|------|-------|--------|
| Scope | Single `target_id` | Explicit `variables[]` across many tags |
| Objective | Inside strategy code | Declarative `objective` spec |
| Solver | Strategy name | `solver.type` + blocks |
| VC | Per-tag metadata (recent) | Per-tag metadata + shared `session_id` |
| Physical model | Implicit in strategies | `physical_type`, normalization, touch-and-go |

Legacy `strategy: NEWTON` remains supported as a **preset** that compiles to `(variables, objective, solver)`.

---

## 3. Architecture

### 3.1 Layer diagram

```text
┌─ UI / scripts ─────────────────────────────────────────────────────┐
│  Alignment session panel  OR  POST OPTIMIZE (ensemble payload)      │
└───────────────────────────────┬────────────────────────────────────┘
                                │
┌─ cloud-labs (session lifecycle) ───────────────────────────────────┐
│  validate spec · dry-run resolve all variable paths · layout @ x₀    │
│  null_measurables(scope ∪ objective.sources)                         │
│  system_status = OPTIMIZING · optimization_session = { id, … }     │
│  await lab_model.execution.optimization.run_ensemble_optimization(spec, x₀)    │
│  commit_optimization_ensemble_complete(best_x, loss, metadata)       │
│  system_status = IDLE                                                │
└───────────────────────────────┬────────────────────────────────────┘
                                │
┌─ bench edge (inner macro, many evals) ─────────────────────────────┐
│  lab_model.execution.optimization.session — block COBYLA loop                  │
│  lab_model.execution.optimization.metrics — shared loss registry (pure math)   │
│  lab_communicator backend — capture + apply tunables (I/O)         │
└───────────────────────────────┬────────────────────────────────────┘
```

### 3.2 Separation of concerns

| Concern | Owner |
|---------|--------|
| Git / configuration commits | **ControlManager** — only final tunables after operator saves |
| Runtime JSON (tunables + measurables) | **RuntimeManager** — one commit at session end |
| Optimization metadata (`metadata.optimization`) | Extracted on **Save configuration**; not a diff driver |
| Loss math (metric registry) | **`lab_model/execution/optimization/metrics`** — shared pure functions |
| COBYLA loop, normalization | **`lab_model/optimization`** (`session`, `solvers`, `normalize`) |
| Measurement capture (NumPy, scalars) | **`lab_communicator`** backend (`evaluate_loss` impl) |
| Session entry/exit, primitive API | **`lab_model/orchestration`** |
| Hardware actuation | **`lab_communicator`** (`mock/` then `real/`) |

> **Naming:** External **`lab_automation`** (sibling repo) = legacy strategies + `OpticalExperiment`.  
> Do **not** confuse with the removed `backend/lab_automation/` folder — ensemble code now lives under **`lab_model/execution/optimization/`**.

---

## 4. Evaluation contract (macro session)

### 4.1 Rules (locked)

| Rule | Decision |
|------|----------|
| Granularity | One `OPTIMIZE` command = one `OPTIMIZING` session |
| Pre-flight | All variable/objective paths **resolved on live state** before session lock (§5.1) |
| Inner loop location | `lab_model.execution.optimization.run_ensemble_optimization` (worker thread on bench edge) |
| Per-eval primitives | **No** `POST /api/command` per COBYLA step |
| Measurables during loop | Staging / fast updates for UI; formal receipts at measure points |
| VC / dirty | **No** configuration diff until operator commits bench |
| Exit commit | **Single** `commit_optimization_ensemble_complete` |
| Bench lock | Whole table `OPTIMIZING`; other motion primitives refused |
| Failure default | `keep_best: true` — commit best-so-far unless `rollback_on_fail: true` |

### 4.2 Session state (runtime JSON extensions)

```json
{
  "system_status": "OPTIMIZING",
  "optimization_session": {
    "id": "hex",
    "mode": "ensemble",
    "scope": ["tag_m1", "tag_m2"],
    "eval": 17,
    "best_loss": 0.142
  },
  "optimization_step": 17
}
```

Legacy fields (`optimization_target_id`, single-tag) remain for **legacy strategy** runs until deprecated.

### 4.3 Telemetry

| Channel | Content | Rate |
|---------|---------|------|
| **Fast (WebSocket / SSE)** | `{ eval, loss, terms, u, block_id }` | Every eval |
| **Slow (polled lab-state)** | `optimization_step`, `best_loss` | Throttled (e.g. every 5 evals) |

---

## 5. Variables (`VariableRef`)

Each scalar degree of freedom in the search vector:

```json
{
  "id": "v_m1_m1",
  "tag_id": "tag_m1",
  "path": "tunables.nominal_motor_positions.1",
  "kind": "continuous",
  "physical_type": "continuous",
  "unit": "deg",
  "bounds": { "min": -3.0, "max": 3.0 },
  "delta": true,
  "step_hint": 0.05
}
```

| Field | Meaning |
|-------|---------|
| `path` | Dot path under `statecontrol` (same convention as VC); must pass **pre-flight resolver** (§5.1) |
| `bounds` | Physical units; if `delta: true`, relative to session \(x_0\) |
| `physical_type` | `"continuous"` or `"invasive_discrete"` (see §7) |
| `touch_and_go` | Required when `invasive_discrete` |

**Supported paths (v1):**

- `tunables.nominal_motor_positions.<motor_id>`
- `tunables.nominal_pose.x` | `.y` | `.rotation`

### 5.1 Path resolver & pre-flight validation (locked)

Dynamic path strings (`"tunables.nominal_motor_positions.1"`) are easy to typo in JSON. A mid-session failure after `system_status = OPTIMIZING` leaves the bench locked with a half-applied session.

**Rule:** Before any session lock, `run_optimize_ensemble` **dry-runs** every `variables[].path` and every `objective` measurable path against the **live runtime state** for the declared `tag_id`s.

| Step | When | Outcome |
|------|------|---------|
| Parse | API / orchestrator entry | Reject unknown path grammar (not in allowlist) |
| Resolve | Pre-flight on live state | For each tag, `get` current value; prove path exists and type is numeric |
| Bind | After pre-flight only | Build typed getter/setter handles; no string splitting during eval loop |

Implementation: `VariablePathResolver` in `lab_model/execution/optimization/paths.py` (Phase 1). Failures return **400 at the API boundary** with `{ path, tag_id, reason }`—never enter `OPTIMIZING`.

**Unit tests (Deliverable 1.10):** typo paths (`tunable.nominal_pose`), wrong motor index, missing tag, non-numeric leaf—all must fail pre-flight without mutating `system_status`.

---

## 6. Unit-hypercube normalization

### 6.1 Problem

COBYLA step sizes are numeric. Mixing ±3° motors with ±50 mm translations in one vector causes the solver to behave as if millimeters and degrees are comparable.

### 6.2 Solution

Before the solver runs:

```text
physical bounds [lo_i, hi_i]  →  normalize  →  u_i ∈ [0, 1]
```

The solver **only** sees `u ∈ [0,1]^N` and bounds `[(0,1)] * N`.

Before each hardware step:

```text
u  →  denormalize  →  x_physical  →  ActuatorRouter
```

Implementation lives in `NormalizedSearchSpace` (`lab_automation` or shared `lab_model/execution/optimization/` module — see Phase 1).

### 6.3 Solver knobs in normalized space

| Parameter | Typical value | Meaning |
|-----------|---------------|---------|
| `rhobeg_u` | 0.05 | Initial simplex ~5% of hypercube |
| `rhoend_u` | 0.002 | Convergence tolerance in u-space |
| `trust_region_u` | 0.15–0.25 | Max \|Δu\| per block step |

JSON `bounds` stay in **operator units** (deg, mm); operators never hand-normalize.

---

## 7. Physical types & touch-and-go

### 7.1 `physical_type: continuous`

Motors, piezos, motorized stages—no gripper clamping artifact.

```text
apply setpoint → settle(settle_ms) → capture / meter → loss
```

Evals can run back-to-back within a block.

### 7.2 `physical_type: invasive_discrete`

Robot moves a **held** component’s pose. Measuring while gripped sees **pre-relaxation** alignment; after release the mount **micro-shifts**.

**Touch-and-go sub-routine (per eval that changes invasive setpoints):**

```text
1. Re-engage (lazy) — gripper acquire + approach ONLY if this eval needs a new pose
                      (see re_engage_on_demand below; skip if already at target while released)
2. Move             — place part at candidate pose (gripping during transit only)
3. Release          — open gripper; arm retracts toward safe clearance pose
4. Settle           — wait for mechanical relaxation (longer than continuous)
5. Measure          — camera + power (loss computed here only)
6. Hold released    — do NOT re-clamp; robot stays clear until next invasive move is requested
```

**Lazy re-engagement (default, locked):** Eager re-clamping after every measurement causes unnecessary wear and **backlash drift**—each grip cycle can nudge the mount on the breadboard. Default behavior:

| Flag | Default | Meaning |
|------|---------|---------|
| `re_engage_on_demand` | `true` | After measure, part stays released; arm retracted. Re-engage only when the solver proposes a **new** target for an invasive variable in that block. |
| `re_engage_after_measure` | `false` | Legacy eager mode; opt-in only for fixtures that require continuous holding between evals. |

When the invasive block **ends** or the solver **switches blocks**, the robot **never** re-engages—it remains disengaged and clear (see §7.3).

```json
{
  "id": "v_lens_y",
  "tag_id": "tag_lens",
  "path": "tunables.nominal_pose.y",
  "physical_type": "invasive_discrete",
  "unit": "mm",
  "bounds": { "min": -50.0, "max": 50.0 },
  "delta": true,
  "touch_and_go": {
    "required": true,
    "gripper_tag": "tag_gripper",
    "safe_home_tag": "tag_gripper",
    "settle_ms_after_move": 250,
    "settle_ms_after_release": 450,
    "settle_ms_after_reengage": 150,
    "re_engage_on_demand": true,
    "re_engage_after_measure": false,
    "measure_only_while_released": true
  }
}
```

**Batching:** Multiple invasive variables sharing one `gripper_tag` (e.g. pose x and y) use **one** release cycle per eval.

### 7.3 Block lifecycle & robot clearance (locked)

**Continuous blocks (mirrors, motorized stages):** Before the first eval of a continuous-only block, `ActuatorRouter` must:

```text
release all grippers on scope → retract arm to safe_home → verify optical path clear → run block
```

While this block runs, the robot **must not** hold any optimized component. Reasons:

| Hazard | Consequence |
|--------|-------------|
| **Beam occlusion** | Gripper or arm clips the path to camera / power meter |
| **Static clamping distortion** | Lens under stress during mirror tuning; release afterward micro-shifts and **invalidates** mirror alignment |

**Invasive blocks:** The robot **only** approaches and engages when an `invasive_discrete` block **actively owns** the solver lifecycle. Entry: re-engage on first new pose in block. Exit: release + retract; **no** final re-clamp.

**Block ordering (recommended):** Run **continuous blocks first** (mirrors), **invasive blocks last**—invasive evals cost ~3–5× wall time, and mirror alignment is meaningless if the held part relaxes afterward.

**Mixed variable_ids in one block:** Discouraged. If allowed, router still enforces: continuous setpoints apply first with arm **cleared**; invasive touch-and-go runs **once** at end of apply phase **only** for variables whose normalized targets changed—never with arm holding the part during mirror moves within the same eval.

---

## 8. Objectives (`ObjectiveSpec`)

Objectives map **measurables** (not variables) to a scalar loss. The camera and power meter are often **not** in the variable list.

**Phase E authoring:** Use field-based **objective graphs** (`version: 1`) compiled to this runtime shape — see [`OBJECTIVE_GRAPH.md`](./OBJECTIVE_GRAPH.md). Twin UI and `POST /api/optimization/compile` call the same compiler.

### 8.1 `weighted_sum` (reference scenario)

```json
{
  "type": "weighted_sum",
  "minimize": true,
  "terms": [
    {
      "id": "centroid_rms_px",
      "weight": 1.0,
      "source": {
        "tag_id": "tag_cam",
        "kind": "derived_centroid",
        "from": "measurables.camera_image",
        "target_px": { "x": 512.0, "y": 384.0 }
      },
      "metric": "rms_distance_px"
    },
    {
      "id": "power_intensity",
      "weight": 0.35,
      "source": {
        "tag_id": "tag_pm",
        "kind": "measurable_scalar",
        "path": "measurables.last_optimization_score",
        "normalize": { "min": 0.0, "max": 1.0 }
      },
      "metric": "one_minus_normalized"
    }
  ]
}
```

```text
L = w_c · RMS_px(centroid, target) + w_p · (1 - norm_power)
```

Lower is better.

### 8.2 Objective metrics (plugin registry in `lab_model/execution/optimization/metrics`)

| `metric` | Description |
|----------|-------------|
| `rms_distance_px` | \(\sqrt{(x-x^*)^2 + (y-y^*)^2}\) |
| `one_minus_normalized` | \(1 - \mathrm{clip}(value, min, max)\) |
| *(future)* `fringe_contrast`, `roi_intensity`, … | Register via `@register_metric` |

Implementation: `metrics/registry.py` + one module per metric. Mock and real backends call `evaluate_weighted_sum()` after producing measurement dicts locally.

### 8.3 Edge execution contract (data gravity)

**Rule:** Raw camera frames and high-rate captures stay on the **bench edge** (same process as `RealLabCommunicator` + external `lab_automation` drivers). Cloud/UI receives **scalars only** (`best_loss`, per-term contributions, `eval`).

```text
Cloud-labs JSON  →  metric: "rms_distance_px"  (intent only)
Bench backend    →  capture → NumPy / scalar dict  (lab-specific I/O)
Shared metrics   →  evaluate_weighted_sum(measurements, objective)  (identical math everywhere)
Session loop     →  COBYLA proposes next u  (lab_model.execution.optimization.session)
```

| Layer | Package | Sends/stores raw frames? |
|-------|---------|---------------------------|
| UI / HTTP poll | frontend, `main.py` | No — scalars only |
| Orchestration | `lab_model/orchestration` | No — session bookkeeping |
| Metrics | `lab_model/execution/optimization/metrics` | No — receives small dicts |
| Backend | `lab_communicator/mock|real/ensemble` | Yes — local RAM only |

Future **`cloudlabs-optimization-core`** pip package (optional): extract `lab_model/execution/optimization/metrics` + `session` + `solvers` for external `lab_automation` installs without full cloud-labs checkout.

---

## 9. Solvers (`SolverSpec`)

### 9.1 `block_cobyla` (v1 default)

Optimizes subsets of variables sequentially; each block runs COBYLA in normalized subspace.

```json
{
  "type": "block_cobyla",
  "max_total_evals": 80,
  "keep_best": true,
  "rollback_on_fail": false,
  "settle_ms": 120,
  "normalization": {
    "space": "unit_hypercube",
    "per_dimension": [0.0, 1.0]
  },
  "blocks": [
    {
      "id": "block_mirrors",
      "variable_ids": ["v_m1_m1", "v_m1_m3", "v_m2_m1", "v_m2_m3"],
      "max_evals": 30,
      "trust_region_u": 0.2,
      "rhobeg_u": 0.05,
      "rhoend_u": 0.002,
      "passes": 2
    },
    {
      "id": "block_lens_relaxed",
      "variable_ids": ["v_lens_y"],
      "max_evals": 15,
      "trust_region_u": 0.15,
      "passes": 1
    }
  ],
  "constraints": [
    { "type": "no_collision", "enabled": true },
    { "type": "max_delta_from_start", "enabled": true }
  ]
}
```

---

## 10. Reference command payload (Scenario B + optional arm)

Full example combining four mirror motors, optional invasive lens Y, centroid + power objective:

```json
{
  "action": "OPTIMIZE",
  "target_id": "tag_m1",
  "parameters": {
    "mode": "ensemble",
    "session_label": "two-mirror centroid + power",

    "variables": [
      {
        "id": "v_m1_m1",
        "tag_id": "tag_m1",
        "path": "tunables.nominal_motor_positions.1",
        "kind": "continuous",
        "physical_type": "continuous",
        "unit": "deg",
        "bounds": { "min": -3.0, "max": 3.0 },
        "delta": true
      },
      {
        "id": "v_m1_m3",
        "tag_id": "tag_m1",
        "path": "tunables.nominal_motor_positions.3",
        "kind": "continuous",
        "physical_type": "continuous",
        "unit": "deg",
        "bounds": { "min": -3.0, "max": 3.0 },
        "delta": true
      },
      {
        "id": "v_m2_m1",
        "tag_id": "tag_m2",
        "path": "tunables.nominal_motor_positions.1",
        "kind": "continuous",
        "physical_type": "continuous",
        "unit": "deg",
        "bounds": { "min": -3.0, "max": 3.0 },
        "delta": true
      },
      {
        "id": "v_m2_m3",
        "tag_id": "tag_m2",
        "path": "tunables.nominal_motor_positions.3",
        "kind": "continuous",
        "physical_type": "continuous",
        "unit": "deg",
        "bounds": { "min": -3.0, "max": 3.0 },
        "delta": true
      },
      {
        "id": "v_lens_y",
        "tag_id": "tag_lens",
        "path": "tunables.nominal_pose.y",
        "kind": "continuous",
        "physical_type": "invasive_discrete",
        "unit": "mm",
        "bounds": { "min": -50.0, "max": 50.0 },
        "delta": true,
        "touch_and_go": {
          "required": true,
          "gripper_tag": "tag_gripper",
          "safe_home_tag": "tag_gripper",
          "settle_ms_after_move": 250,
          "settle_ms_after_release": 450,
          "settle_ms_after_reengage": 150,
          "re_engage_on_demand": true,
          "re_engage_after_measure": false
        }
      }
    ],

    "objective": {
      "type": "weighted_sum",
      "minimize": true,
      "terms": [
        {
          "id": "centroid_rms_px",
          "weight": 1.0,
          "source": {
            "tag_id": "tag_cam",
            "kind": "derived_centroid",
            "from": "measurables.camera_image",
            "target_px": { "x": 512.0, "y": 384.0 }
          },
          "metric": "rms_distance_px"
        },
        {
          "id": "power_intensity",
          "weight": 0.35,
          "source": {
            "tag_id": "tag_pm",
            "kind": "measurable_scalar",
            "path": "measurables.last_optimization_score",
            "normalize": { "min": 0.0, "max": 1.0 }
          },
          "metric": "one_minus_normalized"
        }
      ]
    },

    "solver": {
      "type": "block_cobyla",
      "max_total_evals": 80,
      "keep_best": true,
      "settle_ms": 120,
      "normalization": { "space": "unit_hypercube" },
      "blocks": [
        {
          "id": "block_mirrors",
          "variable_ids": ["v_m1_m1", "v_m1_m3", "v_m2_m1", "v_m2_m3"],
          "max_evals": 30,
          "trust_region_u": 0.2,
          "passes": 2
        },
        {
          "id": "block_lens_relaxed",
          "variable_ids": ["v_lens_y"],
          "max_evals": 15,
          "trust_region_u": 0.15,
          "passes": 1
        }
      ]
    },

    "capture": {
      "before_each_eval": [
        { "tag_id": "tag_cam", "kind": "frame" },
        { "tag_id": "tag_pm", "kind": "integrate_ms", "duration_ms": 50 }
      ]
    },

    "telemetry": {
      "stream": "optimization-ensemble",
      "include": ["eval", "loss", "terms", "u", "block_id"]
    }
  }
}
```

**Legacy mode** (removed — OPTIMIZE is ensemble-only):

```json
{
  "action": "OPTIMIZE",
  "target_id": "tag_m1",
  "parameters": { "mode": "ensemble", "…": "see examples/" }
}
```

---

## 11. Version control integration

Ensemble optimization **does not** change reconcile semantics:

| Data | Versioned? | Notes |
|------|------------|-------|
| Final motor setpoints / poses | Yes (on Save configuration) | Via `extract_configuration` |
| Optimization metadata | Yes (`metadata.optimization`) | Score, `placement_mode`, optional `session_id` |
| Optimizer trace / per-eval history | No | Telemetry only |
| `placement` tunable in diff | No | Excluded from `configuration_diff` (implemented) |

At session end, `commit_optimization_ensemble_complete` writes tunables for all touched tags and per-tag metadata. Operator **Save configuration** persists the DAG node.

---

## 12. Mock landscape (Phase 2 preview)

The mock implements a **coupled synthetic loss** so ensemble COBYLA can be tested without optics:

- **Centroid:** linearized beam walk \( \text{px} = target + J \cdot (\theta - \theta^*) \) with 4×2 sensitivity matrix \(J\) (both mirrors steer X and Y).
- **Intensity:** Gaussian in motor space with cross-terms between mirrors (coupled fringe).
- **Clamping bias (optional):** while `held=True`, centroid offset simulates gripper stress; touch-and-go measures only `held=False`. Mock `ActuatorRouter` must set `held=False` and arm-at-home during **continuous-only** blocks (§7.3).

See **Phase 2** for module paths and acceptance tests. Mock truth `TRUE` angles are hidden from the solver; tests assert recovery within ε.

---

## 13. Implementation roadmap

```text
Phase 1 ── Core logic (lab_model + lab_automation interfaces)
    │
Phase 2 ── Mock communicator + synthetic landscape + tests
    │
Phase 3 ── Operator docs + mock walkthrough + UI session shell
    │
Phase 4 ── Real bench: latency, hardware adapters, production hardening
```

### Phase 1 — Core logic in code *(implemented 2026-07-03)*

**Goal:** Types, validation, orchestration entry, and automation skeleton **without** requiring real hardware or finished UI.

| # | Deliverable | Location (proposed) |
|---|-------------|---------------------|
| 1.1 | Pydantic models: `VariableRef`, `TouchAndGoSpec`, `ObjectiveSpec`, `SolverSpec`, `OptimizeEnsembleParameters` | `lab_model/language/primitives/schemas.py` or `lab_model/execution/optimization/spec.py` |
| 1.2 | `VariablePathResolver` — allowlist parse, dry-run get/set bind against live state | `lab_model/execution/optimization/paths.py` |
| 1.3 | `NormalizedSearchSpace` (hypercube map) | `lab_model/execution/optimization/normalize.py` |
| 1.4 | `run_optimize_ensemble` orchestrator — **pre-flight path resolve before `OPTIMIZING`** | `lab_model/execution/orchestration/optimize_ensemble.py` |
| 1.5 | `commit_optimization_ensemble_complete` | `lab_model/coordinator/state/commits.py` |
| 1.6 | Dispatch branch: `parameters.mode == "ensemble"` | `lab_model/language/primitives/dispatch.py` |
| 1.7 | `OptimizeHost` protocol extensions | `lab_model/execution/orchestration/protocol.py` |
| 1.8 | `ActuatorRouter` interface + stub | `lab_model/execution/optimization/router.py` |
| 1.9 | `run_ensemble_optimization` | `lab_model/execution/optimization/session.py` |
| 1.10 | Legacy preset compiler: NEWTON/COBYLA → ensemble spec (optional parallel) | `lab_model/execution/optimization/presets.py` |
| 1.11 | Unit tests: normalization round-trip, schema validation, invasive requires touch_and_go, **path resolver pre-flight failures** | `backend/tests/test_ensemble_optimization_spec.py` |

**Pre-flight gate (locked):** `run_optimize_ensemble` order is validate JSON → **resolve all paths** → layout check at \(x_0\) → null measurables → `OPTIMIZING`. Invalid path → HTTP 400, `system_status` unchanged.

**Exit criteria:** Valid ensemble payload parses; all paths dry-run against fixture state; normalization maps known bounds correctly; orchestrator enters/exits `OPTIMIZING` only after pre-flight; tests green.

---

### Phase 2 — Mock end-to-end *(implemented 2026-07-03)*

**Goal:** Full ensemble session on **mock** communicator with synthetic loss.

| # | Deliverable |
|---|-------------|
| 2.1 | `MockEnsembleObjective` (coupled centroid + intensity + clamping bias) |
| 2.2 | Mock hardware bridge: `set_motor`, `gripper_release/acquire`, staged measurables |
| 2.3 | Block COBYLA in normalized space (SciPy or minimal internal implementation) |
| 2.4 | `ActuatorRouter` mock: arm clearance during mirror blocks, lazy re-engage, touch-and-go timing |
| 2.8 | Integration test: continuous block asserts gripper released + arm at safe_home throughout |
| 2.5 | Fast telemetry hook (extend existing optimization stream) |
| 2.6 | Integration test: 4-motor scenario converges to hidden `TRUE` |
| 2.7 | Integration test: invasive variable requires release before loss decreases |

**Exit criteria:** `POST OPTIMIZE` (ensemble) on mock completes session; loss curve monotonic-ish; final tunables within tolerance; VC metadata on commit.

---

### Phase 3 — Documentation & mock application guide *(implemented 2026-07-03)*

**Goal:** Operators and developers can run ensemble alignment on mock without reading source.

| # | Deliverable |
|---|-------------|
| 3.1 | **Mock walkthrough** — §18 below |
| 3.2 | Example payloads — [`schemas/ensemble_optimization_examples/`](../schemas/ensemble_optimization_examples/) |
| 3.3 | Staged optimization mode UI — right sidebar **Optimization** tab → **Enter optimization mode** |
| 3.4 | Preset migration table — §19 below |
| 3.5 | [`README.md`](../README.md) ensemble section |

**Exit criteria:** New team member aligns two mock mirrors using doc-only instructions.

---

### Phase 4 — Real bench

**Goal:** Same API on `real/` communicator; handle latency and safety.

| # | Topic |
|---|--------|
| 4.1 | **Latency:** eval budget vs wall clock; async capture; longer settle for invasive |
| 4.2 | **Hardware adapters:** WiFi stepper batching, robot move + gripper + **safe_home retract** primitives |
| 4.7 | **Clearance interlocks:** hardware or layout check that arm is out of beam path before mirror evals |
| 4.3 | **COBYLA:** wrap existing `CobylaAlignmentStrategy_cloudlab` as one block or replace with shared engine |
| 4.4 | **Safety:** collision checks each eval; max delta from applied VC node |
| 4.5 | **Partial failure:** comm drop mid-session; abort vs keep_best |
| 4.6 | **Real mock parity:** same JSON spec on mock and real (contract tests) |

See also [`EDGE_UC_MIGRATION_ROADMAP.md`](./EDGE_UC_MIGRATION_ROADMAP.md).

**Exit criteria:** One real two-mirror + power alignment session documented with timings and failure modes.

---

## 14. Module map (target end state)

```text
lab_model/
  optimization/
    spec.py              # Pydantic models
    paths.py             # VariablePathResolver (pre-flight bind)
    normalize.py         # NormalizedSearchSpace
    presets.py           # legacy strategy → spec
  orchestration/
    optimize.py          # legacy single-tag (keep)
    optimize_ensemble.py # new entry
  state/
    commits.py           # commit_optimization_ensemble_complete
  primitives/
    schemas.py           # OptimizeParameters mode enum
    dispatch.py          # route ensemble vs legacy

  optimization/
    spec.py              # Pydantic models
    paths.py             # VariablePathResolver
    normalize.py         # unit hypercube
    preflight.py         # dry-run before OPTIMIZING
    metrics/             # @register_metric + evaluate_weighted_sum
    session.py           # run_ensemble_optimization
    router.py            # ActuatorRouter protocol
    backend.py           # EnsembleEvaluationBackend protocol
    solvers/
      block_cobyla.py

lab_communicator/
  mock/
    ensemble.py          # synthetic landscape + MockEnsembleBackend
  real/
    ensemble.py          # motor-variable bridge (real; invasive touch-and-go deferred)
```

---

## 15. Glossary

| Term | Meaning |
|------|---------|
| **Ensemble optimization** | One session optimizing a declared list of variables against a declared objective |
| **VariableRef** | One tunable DOF in the search vector |
| **Unit hypercube** | Normalized solver space \([0,1]^N\) |
| **Touch-and-go** | Lazy re-engage on new pose → move → release → settle → measure; arm stays clear after |
| **Safe home / clearance** | Retracted robot pose with grippers open, beam path unobstructed |
| **Pre-flight path resolve** | Dry-run all variable paths on live state before `OPTIMIZING` |
| **Block COBYLA** | Sequential COBYLA on variable subsets |
| **Eval** | One loss measurement (may include multiple hardware substeps) |
| **Legacy strategy** | `NEWTON` / `COBYLA` preset mode |

---

## 18. Mock walkthrough (operator)

Prerequisites: backend running in **mock** mode (`LAB_MODE=MOCK`), default mock bench with **`tag_20`** and **`tag_18`** on the breadboard (both mirrors, motors **1** and **3** in tunables).

### Option A — UI (recommended)

1. Start the server from `backend/`: `uvicorn main:app --reload`
2. Open the app in a browser; confirm **MOCK** in the status area.
3. Right sidebar → **Optimization** tab (default).
4. Click **Enter optimization mode** (purple accent while active).
5. **Stage 1 — Objective:** add terms from any components (e.g. camera on tag_21 + score on tag_20), **Record** per term if needed, then **Next**.
6. **Stage 2 — Variables:** add motor tunables, then **Next**.
7. **Stage 3 — Tune:** set weights, centroid targets, normalize ranges, then **Next**.
8. **Stage 4 — Solver:** max evals, block COBYLA settings, optional second block, then **Next**.
9. **Stage 5 — Run:** review summary, click **Start optimization**.
9. Watch the loss chart and status (`eval`, `best loss`). Overlay shows `ENSEMBLE · eval … · loss …`.
10. When status returns to **IDLE**, mirrors show **ENSEMBLE** placement badge and updated motor setpoints. **Save configuration** to persist a VC node.

### Option B — curl

From the repo root (adjust host/port if needed):

```bash
curl -s -X POST http://127.0.0.1:8000/api/command \
  -H "Content-Type: application/json" \
  -d @schemas/ensemble_optimization_examples/two_mirror_mock.json
```

Expected: HTTP 200, `"status": "accepted"`. Poll `GET /api/lab-state` until `system_status` is `IDLE` and `components.tag_20.statecontrol.measurables.last_optimization_score` is set.

**Pre-flight failure (400):** typo in a variable path, mirror not on bench, or motor index missing — fix payload before retry; bench never enters `OPTIMIZING`.

### Option C — Command console

Paste the `parameters` object from [`two_mirror_mock.json`](../schemas/ensemble_optimization_examples/two_mirror_mock.json) is not directly supported; use UI or curl for ensemble until console shorthand is added.

---

## 19. Legacy preset migration

Legacy `strategy: NEWTON|COBYLA` / `mode: legacy_strategy` paths are **removed**. Prefer `mode=ensemble`, SDK `run_optimize` / `run_cobyla`, Twin Alignment session, and closed-loop jobs. See `GET /api/optimization/capabilities` and [`two_mirror_mock.json`](../schemas/ensemble_optimization_examples/two_mirror_mock.json).

| Former legacy `parameters` | Ensemble equivalent |
|----------------------------|---------------------|
| `{ "strategy": "NEWTON", … }` / `{ "strategy": "COBYLA", "motor_ids": […] }` | Single-tag motors in one `block_cobyla` block with an objective term (e.g. `rms_distance_px`) |
| Multi-mirror bench alignment | [`two_mirror_mock.json`](../schemas/ensemble_optimization_examples/two_mirror_mock.json) |
| Robot-held lens after mirrors | Add `invasive_discrete` variable in stage 2 (pose Y on **tag_11**) and a second solver block in advanced JSON |

---

## 16. Open questions (defer to implementation)

| ID | Question | Default lean |
|----|----------|--------------|
| Q1 | `target_id` for ensemble — anchor tag vs `"<ensemble>"` sentinel? | First scope tag |
| Q2 | Store eval history on disk under `optimization_run_dir`? | Yes, JSONL for replay |
| Q3 | Shared `session_id` in all tags' VC metadata? | Yes |
| Q4 | SciPy COBYLA vs embedded copy? | SciPy if available in lab_automation env |
| Q5 | Invasive + continuous in same block? | Discouraged; if allowed, mirrors apply with arm **cleared** first, touch-and-go only at end for changed invasive vars. Separate blocks strongly preferred. |
| Q6 | Robot pose during continuous-only blocks? | **Locked:** fully disengaged, retracted to safe_home, clear of optical path |
| Q7 | Re-engage after each invasive measure? | **Locked:** no — `re_engage_on_demand` default; `re_engage_after_measure` opt-in false |
| Q8 | When to fail on bad variable path? | **Locked:** pre-flight at API boundary, before `OPTIMIZING` |

---

## 17. Changelog

| Date | Change |
|------|--------|
| 2026-07-03 | Initial design doc: ensemble model, normalization, touch-and-go, Scenario B payload, roadmap Phases 1–4 |
| 2026-07-03 | Phase 2 mock: synthetic landscape, block COBYLA, MockActuatorRouter, integration tests |
| 2026-07-03 | Move ensemble loop from `backend/lab_automation/` → `lab_model/execution/optimization/`; metrics registry; edge contract §8.3; UI 5-stage builder |
