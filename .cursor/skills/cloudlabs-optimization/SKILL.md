---
name: cloudlabs-optimization
description: >-
  Coordinator/Twin closed-loop OPTIMIZE: edge-owned kernel catalog, pipeline
  compile, ensemble job stream, UI presets, and capture/kernel/actuate debug.
  Use when editing optimization UI, /api/kernels, optimize_ensemble, ensemble_host,
  pipeline compile, Wiki Backends→Kernels, or debugging a real/mock OPTIMIZE run.
---

# Cloud Labs optimization (coordinator / Twin)

Roadmap: `docs/EDGE_OPTIMIZATION_PIPELINE.md` (Phases 0–6). Lab hardware checks: §10.

## Mental model

```text
Twin Optimization mode / SDK
    → compile ensemble IR → pipeline JSON
    → OPTIMIZE job to active edge
    ← SSE progress (loss + stages) → lab_state.optimization_session.trace
    → commit_optimization_ensemble_complete (tunables) once
```

- Loop runs **on the edge**. Coordinator does not execute TorchScript for remote backends.
- Live premade catalog = **active edge** via `GET /api/kernels` (Wiki Backends → Kernels + UI presets).
- `schemas/kernels/` = CI/fixtures only. Rebuild: `python scripts/ops/build_torchscript_kernels.py --seed-edges`.

## Twin UI (operator)

Staged mode: Objective → Variables → Tune → Solver → Run → Results.

| Control | Meaning |
|---------|---------|
| Objective goals | Plain-language presets → `builtin.roi_centroid` (center on pixel), `beam_power`, `gaussian_beam_fit`, `beam_shift`, `beam_com` (axis push from origin) when `artifact_present` |
| Target pixel | Defaults to camera center from library `parameters.resolution` (else assume 1920×1080) |
| Post-move wait | `solver.settle_ms` — pause after actuate before capture (**honored on the edge session**; without it CoM can be wrong while motors still move) |
| Camera preview every N | `telemetry.camera_every_n` — sparse JPEG (first + new-best + every Nth) |
| Stop when loss ≤ | `solver.stop_loss` — early success when total normalized loss ≤ threshold (default 0.1 ≈ 10% FOV) |
| **Accept · good enough** | Live panel button while running → `POST /api/jobs/{id}/accept` → edge early_stop (`operator_accept`), job **succeeded**, keep best (not cancel) |
| Live feed pop-out | Detachable floater + bench **Live:** strip; Hide ≠ End. RECORD/OPTIMIZE blocked on that camera until End |
| Max Δ deg/mm | `solver.constraints[{type:max_delta_from_start}]` |
| Keep best / rollback x0 | `keep_best` / `rollback_on_fail` |
| Stage debug | Live + Results show capture / kernel / actuate |

Authoring graphs for edge presets must pass **`source`** (torchscript_features), not sibling `kernel_id` fields (`ObjectiveGraphSpec` is `extra=forbid`).

Remote compile/preflight must pass **`edge_kernel_ids`** from active edge `GET /kernels` — never require coordinator `schemas/kernels/` for `real.default`.

**Sparse camera preview:** Edge stashes a downscaled JPEG on first eval, new-best, and every Nth (`telemetry.camera_every_n`, default 5) — never on every eval. Coordinator commits that envelope into component measurables; Twin Optimization live panel + Results show `/api/components/{tag}/camera-image`. Loop critical path stays tensor-only.

**Actuator kinds (not “everything is a motor”):**
| Path | Edge actuator | Block / physical_type | Hardware |
|------|---------------|------------------------|----------|
| `tunables.nominal_motor_positions.*` | `motor` | continuous | `SET_MOTOR_SETPOINT` |
| `tunables.nominal_pose.{x,y,rotation}` | `pose` | invasive_discrete (+ touch_and_go) | arm `MOVE_COMPONENT` then release for measure |
| other tunables (exposure, …) | not OPTIMIZE variables today | — | SET_* primitives outside the loop |

**Search bounds (operator):** Each variable carries `bounds` + `delta`. Twin Variables stage edits these; motors default to **Δ ±45°** (not ±3°). Physical box maps to u∈[0,1]; `rhobeg_u` / `rhoend_u` are fractions of that span (initial step ≈ rhobeg × (max−min)). Safety Max Δ defaults to 45° and auto-raises with the search box so it does not silently clip travel.

Pipeline compile coerces pose paths to invasive even if the UI mis-tags them continuous. Motors and pose must not share one solver block.

**Beam presence (centroid):** `builtin.roi_centroid` returns `[cx, cy, peak]`. On eval 1 the edge session latches `peak_ref`; later evals with `peak < min_peak_ratio * peak_ref` (default 0.5) mark the measurement absent so align loss hits `loss_cap` (~2) instead of chasing noise CoM. Opt out with `latch_peak_ref: false`.

**Restore-on-absent (centralize / beam-loss):** When presence fails, the session (1) adds a distance barrier `absent_step_barrier × ||Δu||` from the last present point (default weight 1.0 → full-span jump adds +1 on top of ~2), and (2) actuates back to that last-present pose before the next COBYLA trial. Trace shows `stages.actuate_restore` / `policy.presence_restore`. This is stronger than trusting COBYLA trust-region shrink alone — hardware does not stay ejected between evals.

**Maximize brightness (`builtin.beam_power`):** Uses metric `ratio_to_ref` on flux (feature 0). First-eval flux is latched as `value_ref`; loss = `ref / flux` (~1 at start, lower when brighter, >1 when darker). **Not** fixed Normalize min/max (the old `1e6` scale was meaningless). Loss stuck at **2** is the invalid/absent penalty — not a maximize soft-cap.

**Minimize brightness (`minimize.beam_power`):** Same `builtin.beam_power` kernel, metric `ratio_from_ref` = `flux / ref` (~1 at start, lower when darker).

**Motor setpoints / OPTIMIZE variables:** Twin lists motors from catalog `motor_ids` even before the first MOVE. Opening a motor panel RECORD_TUNABLES `nominal_motor_positions` when Twin has no finite angle yet (edge reads K10CR2 `get_position`).

**Normalize min/max:** Only for `one_minus_normalized` with a known physical range (e.g. power-meter mW). Maps value into [0,1] then loss = `1 - normalized`. Do not invent a camera-flux max.

**Demo kernels:** Teaching scalars (`demo.image_mean_score`, `demo.roi_mean_score`, sometimes `demo.peak_intensity`) — mean / ROI mean intensity for SDK scripts. Prefer `builtin.*` for closed-loop science.

**Early-stop:** Solver `stop_loss` (UI default 0.1) ends the session successfully when total normalized loss ≤ threshold — e.g. align-only ≈ within 10% of FOV diagonal from target; for maximize ≈ 10× brighter than first-eval flux. Not an abort; keep_best still applies.

**Operator accept:** Twin live panel **Accept · good enough** posts coordinator `/api/jobs/{id}/accept` (distinct from cancel). Edge session treats it like `stop_loss` (`early_stopped`, `early_stop_reason=operator_accept`). HTTP edges: coordinator POSTs edge `/jobs/{id}/accept` (no task cancel) then waits for succeeded final + commit.

**Session UI:** Twin Optimization live panel stays minimal (status / eval / loss / Accept + link). Full trace, tunables, stage/kernel debug, camera, and logs live on `/optimize-session` (`frontend/optimize-session.html`, `js/optimize-session/`).

**Kernel overlays (session page):** Sparse camera preview draws UI-only overlays from stage debug — e.g. `builtin.roi_centroid` / `rms_distance` shows detected CoM (amber) vs authored `target_px` (cyan); `builtin.gaussian_beam_fit` shows μ + σ/2σ ellipses (no fake target). Uses `stages.kernel` / `stages.policy` feature previews + FOV; does not re-run kernels or change the loop.

**Measurable kernel probes (component panel):** Under `camera_image` / `ImageViewer`, Twin lists premade edge kernels (`GET /api/kernels`). Click runs `EVAL_KERNEL` via `labClient.probeKernel` (lease + `/api/command`, outside OPTIMIZE) on the **latched RECORD BGR** still resident on the edge (not a fresh capture unless `recapture=true`), shows scalar/features, and overlays centroid / gaussian moments on the recorded image (`frontend/js/ui/measurable-kernels.js`).

## Key code

| Area | Path |
|------|------|
| UI builder / presets | `frontend/js/state/optimization-builder.js`, `ui/optimization-mode.js` |
| Full session page | `frontend/optimize-session.html`, `js/optimize-session/` |
| Compile API | `frontend/js/api/optimization.js`, `backend/.../pipeline.py` |
| Orchestration | `backend/.../orchestration/optimize_ensemble.py` |
| HTTP edge host | `backend/.../edge/ensemble_host.py` |
| Kernels proxy | `backend/main.py` `GET /api/kernels`, `resolve_edge_kernels.py` |
| Wiki catalog | `frontend/js/wiki/dashboard.js` `loadKernels` + Backends hub |
| Guides | `frontend/wiki/guides/06-teleop-telemetry-optimize.md`, `07-kernels.md` |

## Debug (Twin)

1. Confirm backend selected; Wiki **Backends → that lab → Kernels** matches edge files + `artifact_present`.
2. Open **Open full session page** (`/optimize-session`) while the run is live; each eval shows:
   - **capture** FAIL → camera / latch / tag (fix on edge observe/capture_impl)
   - **kernel** FAIL → missing `.pt` / torch / wrong id (fix edge `kernels/`). If the path is `{id}.pt` with **dots** (e.g. `builtin.roi_centroid.pt`) while underscores exist on disk, edge `kernel_host` must resolve via local `manifest.json` `artifact` — not `{kernel_id}.pt` and not requiring `cloudlabs_edge_dev` on the bench.
   - **actuate** FAIL → clearance (holding), max-delta refuse, motor/router
   - **presence** ABSENT → peak dropped below `min_peak_ratio × peak_ref` (beam left FOV / exposure change)
   - **scales** missing / policy FAIL → no `frame_hw` from capture; FOV normalize may be wrong
   - **policy** → loss, `stop_loss`, per-term scaled loss, EARLY_STOP when threshold hit
   - **telemetry** → sparse camera stash reason (`first_eval` / `new_best` / `every_N`); NOT stashed / commit FAIL explains missing preview
3. Edge logs: `[optimize.policy]`, `[optimize.telemetry]`; coordinator: `[edge-ensemble …] policy` / `preview camera`.
4. Trace rows also carry `stages` and optional `refused` / `early_stop`.
5. Tests: `backend/tests/test_builtin_kernels.py`, `test_normalized_loss.py`, `test_stop_loss_early_stop.py`, `test_phase6_hardening.py`, `test_pipeline_backend_parity.py`, `test_edge_job_stream_to_trace.py`.

## Do not

- Do not treat coordinator `schemas/kernels/` as the live real-bench catalog.
- Do not reintroduce per-eval coordinator TorchScript for remote backends.
- Do not invent a package CLI to “install” premades onto labs — edit the edge tree / seed script.
- Do not add Twin Optimizer fields for non-variable tunables (e.g. exposure) “just for OPTIMIZE.”

## Edge companion

Edge skills under each `cloudlabs_edge/.agents/skills/`:
`kernels-optimize`, `optimize-pipeline-debug`, `tunables-hold-for-measure`.
