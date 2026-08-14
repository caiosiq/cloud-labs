---
name: optimize-pipeline-debug
description: >-
  Debug failed or stuck OPTIMIZE sessions on a Cloud Labs edge by isolating
  capture vs kernel vs actuate. Use when stage debug shows FAIL, max-delta
  refuses, clearance errors, missing artifacts, empty features, or silent IDLE.
---

# Optimize pipeline debug (edge)

Every eval should report `stages` in job progress / trace. Triage in order:

## 1. Capture

**Symptoms:** `capture.ok=false`, empty frames, wrong shape, no camera.

**Check**

- `optimization/capture_impl.py` / `adapters/observe.capture_tensor` — latch + BGR in-process.
- Tag/field in pipeline `capture[]` matches a real camera measurable.
- Exposure: must follow current lab `exposure_time_ms` (see
  `tunables-hold-for-measure` / `resolve_exposure_s_for_tag`); arm not occluding.
- One-shot: `RECORD_MEASURABLES` / `EVAL_KERNEL` on the same tag.

## 2. Kernel

**Symptoms:** `kernel.ok=false`, missing `.pt`, torch refuse, empty features scored as perfect (should be invalid/penalty).

**Check**

- `GET /kernels` → id listed and `artifact_present: true`.
- File exists under `kernels/` and matches manifest `artifact`.
- Symptom `not found at …/builtin.roi_centroid.pt` (dots in filename) while `builtin_roi_centroid.pt` exists → `kernel_host` fell back to `{kernel_id}.pt` instead of reading local manifest `artifact`. Fix `_resolve_path` to use `manifest.json` without requiring `cloudlabs_edge_dev`.
- Operator **Accept · good enough** (Twin) → edge `/jobs/{id}/accept` → session `early_stopped` / `operator_accept` (success, keep best). Distinct from `/cancel` (abort).
- `capabilities.features.torchscript_execution` matches real torch import.
- Session packages: digest verify; refuse packaging premade ids.
- Synthetic Gaussian tests: `backend/tests/test_builtin_kernels.py` (coord repo).

## 3. Actuate

**Symptoms:** `actuate.ok=false`, `refused: clearance|max_delta_from_start`, motors don't move, settle wrong.

**Check**

- Continuous: `assert_optical_path_clear` — `is_physically_holding` must be false; `clear_for_measure`.
- `max_delta_from_start` limits vs x0 (deg/mm) — oversized candidates get penalty, **no** apply.
- Abort: `keep_best` vs `rollback_on_fail` → settle apply of best / x0.
- Router block lifecycle: enter → apply_eval → exit; status BUSY only inside block.

## Quick commands

```bash
cloudlabs-edge doctor --path .
cloudlabs-edge certify <edge-url> --path . --profile hardware
```

Hardware profile checks OPTIMIZE declared, pipeline schema dry-run, `GET /kernels`.

## Twin-side companion

Coordinator skill: `.cursor/skills/cloudlabs-optimization` (compile, Wiki catalog,
UI presets, job stream → stage debug). Edge never imports that code.
