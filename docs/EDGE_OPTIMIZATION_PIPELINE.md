# Edge optimization pipeline — design & implementation roadmap

**Status:** Phases 0–6 implemented (logic). Real-bench checks remain in §10.
**Last updated:** 2026-08-07
**Audience:** cloud-labs maintainers, edge implementors, anyone extending `OPTIMIZE`

**Related docs:**

- [`ENSEMBLE_OPTIMIZATION.md`](./ENSEMBLE_OPTIMIZATION.md) — variables / objectives / solvers vocabulary (Phases 1–3 implemented on mock; Phase 4 real bench superseded by this doc)
- [`OBJECTIVE_GRAPH.md`](./OBJECTIVE_GRAPH.md) — authoring IR → runtime `ObjectiveSpec`
- [`EDGE_UC_MIGRATION_ROADMAP.md`](./EDGE_UC_MIGRATION_ROADMAP.md) — edge contract migration context

---

## 1. Why this document exists

Ensemble optimization is implemented **on the coordinator**. `lab_model/execution/optimization/`
holds the spec, the normalizer, block COBYLA, the metric registry, the TorchScript runtime,
and the per-eval measurable sync. The only `EnsembleEvaluationBackend` implementation is
`mock_backend/src/mock_backend/host/ensemble.py`. On a real bench,
`_primitive_run_ensemble_optimization` is unimplemented, so `run_optimize_ensemble` logs
`Ensemble optimization stub` and returns to `IDLE` without committing.

Meanwhile the real edge runs `lab_automation/objects/strategies.py` — five classes where the
objective, the actuator, and the solver are welded together, each evaluation costing a
recorder `CAP` → full-resolution PNG on disk → size-stability poll → `cv2.imread`.

This roadmap replaces both with one shape:

```text
author writes pipeline JSON
        │  (HTTPS, once, at job submit)
        ▼
   edge reads pipeline
        │
        ├─► plan: which measurables at which step
        ├─► capture: measurable → tensor (in-process, latched)
        ├─► kernel: tensor → scalar | features        ◄── general
        ├─► loss:   terms → weighted scalar            ◄── general
        ├─► stepper: loss history → next u             ◄── general (predefined)
        └─► router:  next u → actuation                ◄── lab-specific
```

Only **capture** and **router** are lab-specific. Everything else is shared engine code.

---

## 2. Locked architectural decisions

| # | Decision | Consequence |
|---|----------|-------------|
| D1 | The loop runs **entirely on the edge**. The coordinator ships a pipeline once and subscribes to job events. | No per-eval HTTP round trip. |
| D2 | `cloudlabs_edge` **never imports `cloudlabs` or `lab_model`.** Server contact is HTTPS only. | The engine ships in `cloudlabs_edge_dev`, which the edge already imports (`edge_data`). |
| D3 | The stepper is **predefined engine code**, not a shipped kernel. | Block COBYLA stays a fixed engine; no ask/tell refactor needed. |
| D4 | Kernels execute on the edge. Catalog (premade) kernels are **edge-owned files**; session kernels are still provisioned over HTTPS. | Coordinator/Wiki **reflect** the active edge's catalog; they do not own it. |
| D5 | In-loop measurement is **latched but not materialized**. | No JPEG encode, no `LazyRef` per eval; envelopes only on new-best / every Nth. |
| D6 | In-loop actuation **bypasses state-control primitives**. | Bookkeeping is block-scoped; one authoritative commit at session end. |
| D7 | Premade catalog = files under `cloudlabs_edge/kernels/` (`manifest.json` + `.pt`). Skeleton seeds the experiment defaults. Session compile stays for new science. | No package CLI as source of truth for premades; labs may add/remove rows in their own edge tree. |

### D4 / D7 in detail — two shelves, edge is the catalog

The Wiki already draws this correctly ([`frontend/wiki/guides/07-kernels.md`](../frontend/wiki/guides/07-kernels.md)):

| Shelf | Definition | Ownership |
|-------|------------|-----------|
| **Catalog / premade** | Shipped `.pt` + manifest for what *this lab* can measure | **Edge tree** — `cloudlabs_edge/kernels/manifest.json` + artifacts |
| **Session** | Author `nn.Module`, `register_kernel` under a lease | Uploaded once over HTTPS; cached on that edge for the job |
| **Runtime hooks** (`ensemble.eval.*`) | Internal Python plumbing, not image models | Coordinator/engine — not shown as TorchScript |

Today the Wiki's "Backends → Kernels" tab is wrong about *where* the catalog lives: `GET /api/kernels` reads the coordinator's `schemas/kernels/manifest.json`, not the active edge. Intended shape:

```text
cloudlabs_edge/
  kernels/
    manifest.json          ← authoritative premade list for THIS backend
    builtin_roi_centroid.pt
    builtin_beam_power.pt
    …
  kernel_host.py           ← loads from ./kernels (or CLOUDLABS_EDGE_KERNELS_DIR)
```

- `cloudlabs-edge init` writes a starter set (centroid, power, gaussian moments, demos) into that folder — same pattern as `data/library.json`.
- Each lab edits *its* manifest for what the optics actually support; mock/sim/real can diverge.
- Wiki / Twin list kernels by asking the **active backend's edge** (`GET /kernels` or equivalent), optionally merged with lease-scoped session packages.
- Authors still compile *new* kernels via the SDK (`register_kernel` / session path). That is not how premades are produced.

What we deliberately **do not** do: a `cloudlabs-edge build-kernels` (or similar package CLI) as the way premades exist. Building TorchScript once to *seed the skeleton templates* is fine as a maintainer one-off; the living source of truth after init is the edge's `kernels/` directory.

### D2 in detail — where the engine lives

The edge already imports `cloudlabs_edge_dev.edge_data` at runtime
(`runtime/inventory_state.py`, `runtime/storage_grid.py`, `adapters/vision.py`,
`server/app.py`). That package is the edge-side library, distinct from the server-side
`cloudlabs` SDK. So:

| Code | Home | Why |
|------|------|-----|
| Tensor carrier, kernel compiler, metrics, normalizer, stepper, session loop | `cloudlabs_edge_dev.optimization.*` — **importable library** | Versioned with the contract, identical on every bench, pip-installed |
| Capture adapter, actuator router | `cloudlabs_edge/adapters/` + `cloudlabs_edge/optimization/` — **scaffolded stubs** | Lab-specific by definition; each lab fills them in |

This mirrors the existing `edge_data` (library) vs `adapters/` (per-lab) split. Drift between
the coordinator's copy of the metric math and the edge's copy is caught by
`cloudlabs-edge certify`, exactly as `measurables_schema.py` already handles envelope drift.

---

## 3. How kernels reach a bench today (baseline) vs intended

| Path | Today | Intended (this roadmap) |
|------|-------|-------------------------|
| **Premade catalog** | `schemas/kernels/` in the **coordinator** repo; Wiki lists via `list_kernels()` locally | `cloudlabs_edge/kernels/` on **that edge**; Wiki lists via edge `GET /kernels` for the active backend |
| **Session package** | Upload to coordinator scratch; often evaluated coordinator-side | Upload still via Twin/SDK; artifact forwarded in job payload → edge cache; always evaluated on edge |
| **Inline provision** | `EVAL_KERNEL` `artifact_b64` / `kernel_uri` → edge `kernel_host` | Unchanged — escape hatch for one-shot / missing premade |

There is **no UI upload button**. `frontend/js/cloudlabs/client.js` exposes `probeKernel()`
(one-shot `EVAL_KERNEL`); Wiki **Backends → Kernels** is browse + usage snippets only.

Consequence today: **two TorchScript runtimes** and a **mis-owned catalog**. D4/D7 make the edge catalog + `kernel_host` authoritative; coordinator-side `torchscript_runtime` becomes mock/authoring preview only.

---

## 4. The pipeline document

One JSON object, self-contained, validated by schema on both sides. Sketch:

```jsonc
{
  "schema_version": 1,
  "session_label": "beam centering + power",
  "variables": [
    {
      "id": "v_m1", "tag_id": "tag_20",
      "actuator": { "kind": "motor", "controller": "wifi_stepper1", "motor_id": 1 },
      "physical_type": "continuous", "unit": "deg",
      "bounds": { "min": -3.0, "max": 3.0 }, "delta": true
    },
    {
      "id": "v_lens_y", "tag_id": "tag_9",
      "actuator": { "kind": "pose", "axis": "y" },
      "physical_type": "invasive_discrete", "unit": "mm",
      "bounds": { "min": -8.0, "max": 8.0 }, "delta": true,
      "touch_and_go": { "measure_only_while_released": true, "settle_ms_after_release": 450 }
    }
  ],
  "capture": [
    { "id": "cam", "tag_id": "tag_22", "field": "camera_image", "exposure_s": 0.05 }
  ],
  "objective": {
    "type": "weighted_sum", "minimize": true,
    "terms": [
      { "id": "center", "weight": 1.0, "capture_id": "cam",
        "kernel_id": "builtin.roi_centroid", "metric": "rms_distance",
        "params": { "feature_index": [0, 1], "target": [2744, 1836] } },
      { "id": "power", "weight": 0.4, "capture_id": "cam",
        "kernel_id": "builtin.beam_power", "metric": "one_minus_normalized",
        "params": { "feature_index": 0, "normalize": { "min": 0, "max": 1 } } }
    ]
  },
  "solver": {
    "type": "block_cobyla", "max_total_evals": 80, "settle_ms": 120, "keep_best": true,
    "blocks": [
      { "id": "mirrors", "variable_ids": ["v_m1"], "max_evals": 40, "passes": 2 },
      { "id": "lens",    "variable_ids": ["v_lens_y"], "max_evals": 12 }
    ]
  },
  "kernel_packages": [
    // session.* only — premades are already on the edge; do not re-ship builtins here
    { "kernel_id": "session.real_default.my_score.a1b2c3d4", "digest": "sha256:…", "artifact_b64": "…" }
  ]
}
```

Three deliberate differences from the coordinator's `OptimizeEnsembleParameters`:

1. **`actuator` replaces `path`.** The coordinator's `VariableRef.path` is a coordinator
   state path (`tunables.nominal_motor_positions.1`). The edge has no coordinator state,
   so the variable binds to a physical actuator directly. The coordinator translates
   `path → actuator` when it compiles the job.
2. **`capture` is explicit and shared.** Terms reference a `capture_id`, so N terms over one
   frame capture once. Today `collect_objective_measurements` achieves this with an ad-hoc
   `capture_cache` dict.
3. **Every term goes through a kernel.** No `derived_centroid` special case — centroid is
   just `builtin.roi_centroid` from **this edge's** premade manifest. `kernel_packages` carries
   session artifacts only; referencing an unknown premade id fails preflight against the
   edge catalog, not against `schemas/kernels/`.

---

## 5. Phases

```text
Phase 0 ── Contract: pipeline schema + compiler on the coordinator
    │
Phase 1 ── Engine in cloudlabs_edge_dev (pure, no hardware)
    │        + skeleton seeds cloudlabs_edge/kernels/ (premade starter set)
    │
Phase 2 ── Lab seams on the real edge (capture + router) ✅
    │
Phase 3 ── Edge-owned catalog + session provisioning over HTTPS ✅
    │        Wiki/Twin list kernels from the active edge; coordinator demoted
    │
Phase 4 ── Per-eval telemetry: edge job stream → Twin trace → UI ✅
    │
Phase 5 ── Curate this experiment's premades on the real edge ✅
    │        (centroid / power / shift) — edit the edge tree, not a package CLI
    │
Phase 6 ── Real bench bring-up and hardening ✅
```

**Phase impact of D7:** Phase 1 gains skeleton seeding of `kernels/`. Phase 3 is no longer
"ship builtins in every job"; it is "catalog lives on edge + Wiki reads edge; session
packages still ride HTTPS." Phase 5 stops treating `schemas/kernels/build.py` as the
product path — it may remain a *maintainer* tool to refresh skeleton fixtures, but labs
own their files.
---

### Phase 0 — Contract ✅

**Goal:** the pipeline JSON exists, is schema-validated, and the coordinator can compile an
ensemble spec into it. Nothing executes yet.

| # | Deliverable | File | Status |
|---|-------------|------|--------|
| 0.1 | `optimization_pipeline.schema.json` | `schemas/edge_contract/v1/optimization_pipeline.schema.json` | done |
| 0.2 | Register schema for conformance validation | `conformance.py`, `doctor.py` (`_SCHEMA_FILES`) | done |
| 0.3 | `compile_pipeline(spec, catalog) -> dict` | `backend/lab_model/execution/optimization/pipeline.py` | done |
| 0.4 | Emit pipeline in the OPTIMIZE payload | `dispatch.py`, `optimize_ensemble.py` (stripped before IR validate) | done |
| 0.5 | Bump contract version → **1.1.0** | kit + mock/sim/real edge pins + capabilities schema | done |

**Logic tests** — `backend/tests/test_optimization_pipeline_compile.py` (10 cases, green).

**Exit criteria:** met — mock two-mirror example compiles, validates against the schema, and round-trips `json.dumps`.
---

### Phase 1 — Engine in `cloudlabs_edge_dev` ✅

**Goal:** the general half runs on any machine with no hardware and no coordinator.

| # | Deliverable | Status |
|---|-------------|--------|
| 1.1–1.10 | `cloudlabs_edge_dev.optimization` (`tensors`, `spec`, `normalize`, `metrics`, `kernels`, `stepper`, `router`, `capture`, `session`) | done |
| 1.11–1.13 | Scaffold seeds `kernels/manifest.json` + `optimization/*` stubs | done |
| 1.14 | Scaffold `kernel_host` manifest-aware path resolution | done |
| 1.15 | `kernels-optimize` skill updated (premade = edge files) | done |

**Logic tests** (green): `test_edge_engine_normalize`, `test_edge_engine_metrics`,
`test_edge_engine_kernels`, `test_edge_engine_session`, `test_engine_parity`.

**Exit criteria:** met — engine suites green without hardware; parity holds for shared metrics.

> Starter `.pt` artifacts are listed in the seeded manifest but may be absent until a
> maintainer builds them (or Phase 5). Session tests use measurable-path landscapes so
> torch is not required for CI.

---

### Phase 2 — Lab seams on the real edge ✅

**Goal:** the real bench can execute a pipeline. This is where the `strategies.py` loops die.

| # | Deliverable | File |
|---|-------------|------|
| 2.1 | `capture_tensor(tag, field, exposure_s) -> EdgeTensor` — latch, in-process `capture_bgr`, **no JPEG, no LazyRef** | `lab_automation/cloudlabs_edge/adapters/observe.py` |
| 2.2 | `EdgeCaptureSource` implementing `CaptureSource` over 2.1, one capture per `capture_id` per eval | `lab_automation/cloudlabs_edge/optimization/capture_impl.py` (new) |
| 2.3 | `RealActuatorRouter`: continuous → `adapters.motors.set_motor_setpoint`; invasive → motion helper with release-and-retract before measure | `lab_automation/cloudlabs_edge/optimization/router_impl.py` (new) |
| 2.4 | Block-scoped bookkeeping: `set_system_status("BUSY")` and `set_observed_pose` on block enter/exit, **not per eval** | same |
| 2.5 | Rewrite `optimize_component` to parse the pipeline and run `session.run()`; OPTIMIZE is ensemble-only | `lab_automation/cloudlabs_edge/adapters/optimize.py` |
| 2.6 | (removed) Legacy `mode="legacy_strategy"` — no longer supported | — |

**Logic tests** — `lab_automation/cloudlabs_edge/tests/` (new):

- `test_optimize_pipeline_parse.py` — a pipeline with an unknown kernel / unbound actuator is
  refused **before** any hardware call (mock the experiment; assert zero calls).
- `test_router_continuous.py` — patch `adapters.motors`; assert `set_motor_setpoint` called
  with absolute angles, no inventory write, no `scan_components_cloudlab`.
- `test_router_invasive.py` — assert the gripper is released and the arm retracted **before**
  every capture, and that an unchanged invasive variable does not re-move.
- `test_capture_no_jpeg.py` — patch `vision.encode_jpeg` to raise; a full session must still
  complete (proves the in-loop path never encodes).
- `test_optimize_refuses_legacy_downgrade.py` — `mode="ensemble"` without a pipeline refuses
  rather than running single-tag COBYLA.

**Lab integration / exit criteria:** deferred to [§ Real-bench checklist](#real-bench-checklist)
(dry-run, single-motor, timing). Logic exit: the five unit tests above are green.

---

### Phase 3 — Edge-owned catalog + session provisioning ✅

**Goal:** the Wiki and Twin show what *this* edge has as premades; session artifacts still
arrive over HTTPS; the coordinator stops executing TorchScript for remote backends.

| # | Deliverable | File |
|---|-------------|------|
| 3.1 | Edge route `GET /kernels` — list rows from local `kernels/manifest.json` + digests + `artifact_present` | `lab_automation/cloudlabs_edge/server/app.py` (+ mock/sim), contract schema |
| 3.2 | Twin `GET /api/kernels` proxies the **active backend's** edge catalog (merge lease session packages on top); stop reading coordinator `schemas/kernels/` as the live catalog for remote backends | `backend/main.py`, `lab_model/.../kernels/registry.py` |
| 3.3 | Wiki unchanged UX, correct data: Backends → Kernels already calls `/api/kernels`; it now reflects that edge | `frontend/js/wiki/dashboard.js` (verify only) |
| 3.4 | Accept `kernel_packages[]` for **`session.*` only** inside the OPTIMIZE / job payload; provision before first eval; refuse packaging of premade ids that the edge already lists | `adapters/optimize.py`, `kernel_host.py`, `jobs/runner.py` |
| 3.5 | Persist provisioned session kernels under the edge kernels dir (or a session subdir) keyed by digest; skip rewrite when digest matches | `kernel_host.py` |
| 3.6 | For remote backends, `torchscript_*` terms must **not** execute coordinator-side; preflight against edge catalog ids | `objective_measurements.py`, `preflight.py` |

> Premade builtins are **not** re-shipped in every job. If an edge is missing a premade the
> experiment needs, fix that edge's `kernels/` tree (or add a session kernel) — do not
> invent a package CLI that "installs" builtins onto labs.

**Logic tests:**

- `backend/tests/test_kernels_proxy_from_edge.py` (new) — with a stub edge returning two
  premades, `/api/kernels` returns those ids (not the coordinator schema set).
- `lab_automation/cloudlabs_edge/tests/test_kernels_manifest.py` (new) — manifest parse,
  missing artifact → `artifact_present: false`, unknown id refused by `load_kernel`.
- `backend/tests/test_kernel_package_forwarding.py` (new) — session kernel under a lease
  appears in the edge payload; premade id in `kernel_packages` is rejected or ignored.
- `backend/tests/test_no_coordinator_kernel_exec_on_remote.py` (new) — remote backend never
  calls `run_torchscript_output` in `collect_objective_measurements`.

**Lab integration / exit criteria:** deferred to [§ Real-bench checklist](#real-bench-checklist)
(Wiki catalog matches edge files; session kernel under lease). Logic exit: the four tests above.

---

### Phase 4 — Per-eval telemetry ✅

**Goal:** the operator watches the loss curve live, through existing channels only.

| # | Deliverable | File |
|---|-------------|------|
| 4.1 | Emit `{eval, loss, best_loss, terms, block_id, u, values}` as job progress extras each eval | `lab_automation/cloudlabs_edge/adapters/optimize.py` (via `JobContext.progress`) |
| 4.2 | Materialize a `camera_image` envelope only on new-best or every Nth eval | same + `adapters/observe.py` |
| 4.3 | Beam-presence: latch first-eval `peak`; later `peak < ½ peak_ref` → capped align loss | `presence.py` + `roi_centroid` `[cx,cy,peak]` |
| 4.4 | Normalize spatial losses to ~0–2 (FOV diagonal / width scale) so multi-term weights work | `metrics.py` + session `inject_frame_scales` |
| 4.5 | Edge-backed `_primitive_run_ensemble_optimization`: subscribe `/jobs/{id}/stream`, forward records into the existing `progress_callback` | new edge-backed host (mirrors `mock_backend/src/mock_backend/host/communicator.py`) |
| 4.6 | Commit once on completion via `commit_optimization_ensemble_complete` | `backend/lab_model/execution/orchestration/optimize_ensemble.py` (already wired) |
| 4.7 | Stage debug cards: capture/kernel/actuate + **presence / scales / policy / telemetry**; logs `[optimize.policy]` / `[optimize.telemetry]` | `session.py`, real `adapters/optimize.py`, Twin `optimization-mode.js` |

`JobContext.progress(fraction, message, **extra)` already fans arbitrary extras to
subscribers, and `optimize_ensemble.py`'s `progress_callback` already records exactly this
record shape into `optimization_session.trace`. This phase is wiring, not new surface.

**Logic tests:** `backend/tests/test_edge_job_stream_to_trace.py` (new) — a fake edge job
stream yielding 10 progress events produces 10 trace records with monotonic `best_loss`;
a dropped connection mid-stream marks the session failed without corrupting the trace.

**Lab integration / exit criteria:** deferred to [§ Real-bench checklist](#real-bench-checklist)
(live loss curve + single end commit).

---

### Phase 5 — Curate this experiment's premades on the real edge ✅

**Goal:** the three objectives this lab cares about exist as **files on the real edge**,
validated against synthetic Gaussians in CI — not as a centrally compiled product catalog.
Real-optics checks stay in [§ Real-bench checklist](#real-bench-checklist).

| # | Kernel | Output | Notes |
|---|--------|--------|-------|
| 5.1 | `builtin.roi_centroid` (seeded in Phase 1) | `[cx, cy]` | Validate against `VisionManager.find_beam_centroid` on real frames |
| 5.2 | `builtin.beam_power` (add to real edge `kernels/` if missing) | `[flux_above_background, peak, saturated_fraction]` | Pin exposure/gain for the session; saturation channel mandatory |
| 5.3 | `builtin.gaussian_beam_fit` (seeded) | `[amplitude, cx, cy, sigma_x, sigma_y]` | Moment-based — fine for closed loop |
| 5.4 | `builtin.beam_shift` | `[dx, dy, magnitude]` from this frame's `(W/2, H/2)` | Maximize ‖Δ‖ (weight −1 on magnitude). Resolution-agnostic; **no** reference capture |
| 5.5 | `builtin.beam_com` | `[cx, cy, peak]` full-frame CoM (same gate as beam_shift) | Maximize signed offset from operator **origin** along axis X/Y ± via metric `signed_axis_offset`; peak latch for presence |

Objective for beam shift: push the single-beam intensity CoM as far as possible from the
**current camera frame's geometric center**. The kernel never bakes a lab-specific pixel
(e.g. 2790) — center is always half the captured width/height. Pair with
`minimize_value` on `feature_index=2` and `weight: -1` so the ensemble (which minimizes)
maximizes shift.

| # | Deliverable | File |
|---|-------------|------|
| 5.5 | Add/replace `.pt` + manifest rows **on the real edge tree** (and mock/sim if you want parity) | `lab_automation/cloudlabs_edge/kernels/`, optionally `simulation_edge/.../kernels/`, `mock_backend/.../kernels/` |
| 5.6 | If a new starter should ship to *future* edges, refresh the **scaffold fixture** once (maintainer); do not require labs to run a package CLI | `packages/cloudlabs_edge_dev/.../scaffold.py` fixtures |
| 5.7 | UI preset picker for the three objectives, populated from the **active edge** catalog | `frontend/js/state/optimization-builder.js`, `frontend/js/ui/optimization-mode.js` |
| 5.8 | Demote coordinator `schemas/kernels/` to fixtures / CI only (or delete once edges are seeded) | `schemas/kernels/`, docs |

**Logic tests:** `backend/tests/test_builtin_kernels.py` — synthetic Gaussians; centroid within
0.5 px; power monotonic; saturated frame flags; beam-shift equals injected displacement.
Prefer loading artifacts from a temp edge `kernels/` dir to prove the edge-owned path.

**Lab integration / exit criteria:** deferred to [§ Real-bench checklist](#real-bench-checklist)
(known physical displacement vs kernel output; Wiki presets for `real.default`).

**Rebuild / re-seed:** `python scripts/ops/build_torchscript_kernels.py --seed-edges`

---

### Phase 6 — Bring-up and hardening ✅

**Goal:** fail closed on unsafe steps, settle actuators honestly on abort, prove the same
pipeline JSON across edge catalogs, and make `certify --profile hardware` check OPTIMIZE.

| # | Topic | Implementation |
|---|-------|----------------|
| 6.1 | Clearance interlock | `ActuatorRouter.assert_optical_path_clear`; session calls on continuous enter + each eval; real router refuses while arm is holding |
| 6.2 | Max-delta guard | `solver.constraints[{type:max_delta_from_start, limits:{deg,mm}}]` enforced vs x0 before apply |
| 6.3 | Abort semantics | `keep_best` vs `rollback_on_fail` → settle apply of best / current / x0 after abort |
| 6.4 | Mock / sim / real parity | `backend/tests/test_pipeline_backend_parity.py` — shared builtins + golden pipeline run + compile |
| 6.5 | Certify hardware | `conformance._check_optimization_contract` — OPTIMIZE, pipeline schema dry-run, GET /kernels |
| 6.6 | Two-mirror + power session | Template in [§ Real-bench checklist](#real-bench-checklist) (lab-recorded timings) |

**Logic tests:** `backend/tests/test_phase6_hardening.py`, `backend/tests/test_pipeline_backend_parity.py`.

**Lab session:** deferred to [§ Real-bench checklist](#real-bench-checklist).

---

## 6. Behaviour changes to decide explicitly

These are **not** ports — the current behaviour is wrong and Phase 1 must choose:

| Issue | Today | Proposed |
|-------|-------|----------|
| Beam lost, features empty | `metric_rms_distance` returns `0.0` → scores as **perfect** | Raise `MeasurementInvalid`; session records the eval as refused and returns a large finite penalty derived from bounds |
| Beam lost, centroid `None` | `collect_objective_measurements` writes `NaN` → propagates through `evaluate_weighted_sum` into COBYLA | Same as above; never hand NaN to the stepper |
| Missing hardware scalar | Substituted with `0.0` | Refuse the eval |
| Legacy penalty | Fixed `1000.0` in `strategies.py` | Penalty scaled to the term's normalized range so it cannot silently dominate a weighted sum |

## 7. Known defects to fix in passing

| Defect | Location |
|--------|----------|
| Newton strategy reads `centroid[0]` regardless of configured axis, so y-centering optimizes x | `lab_automation/objects/strategies.py:167` |
| Frame size hardcoded `5488×3672` for target and tolerance | `lab_automation/objects/strategies.py:122` |
| `mode="ensemble"` silently downgraded to single-tag COBYLA | `lab_automation/cloudlabs_edge/adapters/optimize.py:29` |

---

## 8. File change index

| Phase | New | Modified |
|-------|-----|----------|
| 0 | `schemas/edge_contract/v1/optimization_pipeline.schema.json`, `backend/lab_model/execution/optimization/pipeline.py`, `backend/tests/test_optimization_pipeline_compile.py` | `conformance.py`, `dispatch.py`, `contract_version.txt` |
| 1 | `cloudlabs_edge_dev/optimization/*`, `backend/tests/test_edge_engine_*.py`, scaffold `kernels/manifest.json` + starter `.pt` fixtures | `scaffold.py`, `agent_skills.py`, `kernel_host` templates |
| 2 | `cloudlabs_edge/optimization/{capture_impl,router_impl}.py`, 5 edge tests | `adapters/observe.py`, `adapters/optimize.py` |
| 3 | edge `GET /kernels`, `backend/tests/test_kernels_proxy_from_edge.py` (+ 3 related) | `server/app.py`, `backend/main.py`, `registry.py`, `kernel_host.py`, `jobs/runner.py`, `preflight.py` |
| 4 | edge-backed ensemble host, `backend/tests/test_edge_job_stream_to_trace.py` | `adapters/optimize.py`, `adapters/observe.py` |
| 5 | real-edge `kernels/` rows for power/shift as needed, `backend/tests/test_builtin_kernels.py` | edge manifests, optimization UI presets; demote `schemas/kernels/` to fixtures |
| 6 | `optimization/guards.py`, `test_phase6_hardening.py`, `test_pipeline_backend_parity.py` | `session.py` (edge+coord), `router.py`, real `router_impl.py`, `conformance.py`, pipeline schema, this doc |

---

## 9. Test ladder (applies to every phase)

1. **Unit** — pure functions, no I/O. Runs in CI on any machine.
2. **Engine parity** — coordinator math vs edge math on identical inputs, bit-identical.
3. **Contract** — `cloudlabs-edge doctor` (file set) and `certify --profile skeleton`
   (schema conformance against a running edge).
4. **Mock end-to-end** — the same pipeline JSON on `mock.default`, asserting convergence to
   the hidden synthetic truth.
5. **Real dry-run** — capture + kernel + loss on real optics, zero motion, one eval.
6. **Real bounded** — few evals, tight bounds, verify actuators return home.
7. **Real session** — full run with timings recorded.

Never skip step 5 when a kernel or capture path changes; it is the cheapest way to catch a
frame-format or exposure regression before the arm moves.

---

<a id="real-bench-checklist"></a>

## 10. Real-bench checklist (run when the lab is up)

All physical / Wiki-on-hardware checks live here so phases can ship on logic tests alone.
Check items off in one lab session; record notes/timings under each item.

### Phase 2 — capture + router on hardware

- [ ] **Dry-run (hardware idle).** Submit a pipeline with `max_total_evals: 1` and a
  camera-only objective (no motion variables, or variables held at x₀ with no apply). Confirm
  capture, kernel, and loss on real optics with zero motion.
- [ ] **Single-motor, bounded.** One continuous variable, `bounds: ±0.5 deg`, `max_evals: 5`.
  Watch the motor move and the loss change. Verify `SET_MOTOR_SETPOINT` logical bookkeeping
  still agrees afterwards (`MOTOR_SEND_HOME` returns to zero).
- [ ] **Timing baseline.** Log per-eval wall clock (settle / capture / kernel / apply). Target:
  continuous-block eval under 1 s vs today's 3–5 s.
- [ ] **Exit:** 5-eval single-motor session completes; loss monotonic-ish; motors home;
  per-eval timing in the job result.

### Phase 3 — edge catalog + session packages

- [ ] Wiki → Backends → `real.default` → Kernels matches
  `lab_automation/cloudlabs_edge/kernels/manifest.json` on that machine.
- [ ] Register a session kernel from a script, probe it, confirm it appears under the lease
  and executes on the edge.
- [ ] **Exit:** Wiki catalog = edge files; session compile works; coordinator never loads torch
  for the real backend.

### Phase 4 — live telemetry

- [ ] Run the Phase 2 five-eval session; UI optimization panel ticks per eval.
- [ ] Sparse camera preview appears on first / new-best / every Nth eval (not every eval).
- [ ] Final tunables commit once; `system_status` returns to `IDLE` if the browser closes mid-run.
- [ ] **Exit:** live loss curve + sparse camera during a real session; one commit at the end.

### Phase 5 — curated premades on optics

- [ ] Place a known target, translate a measured distance with teleop; centroid kernel on the
  real edge reports the same displacement.
- [ ] Wiki for `real.default` lists the three objectives; UI presets work.
- [ ] **Exit:** physically known displacement matches kernel output.

### Phase 6 — full session

Record one two-mirror + power run (use UI presets or `schemas/ensemble_optimization_examples/two_mirror_mock.json` shape with edge kernels). Fill in:

| Field | Value |
|-------|-------|
| Date / backend | |
| Variables | e.g. tag_20 m1/m3, tag_18 m1/m3 |
| Objective | centroid + power (and/or beam_shift) |
| `max_delta` limits | deg=___ mm=___ |
| Evals / wall clock | |
| Best loss | |
| Failure modes seen | clearance refuse / max-delta / abort / other |
| Notes | |

- [ ] One real two-mirror alignment session, documented above, repeatable.
- [ ] `cloudlabs-edge certify --profile hardware` against the live edge is green
      (OPTIMIZE + pipeline schema + kernels).
- [ ] **Exit:** documented timings + failure modes; certify hardware green.
