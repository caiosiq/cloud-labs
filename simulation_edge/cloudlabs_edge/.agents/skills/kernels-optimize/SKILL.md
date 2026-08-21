---
name: kernels-optimize
description: >-
  Edge-owned TorchScript catalog, capture/router seams, and OPTIMIZE pipeline
  jobs on a Cloud Labs edge. Use when editing kernels/, kernel_host, adapters/optimize,
  optimization/capture_impl or router_impl, premade builtins, or closed-loop sessions.
---

# Kernels and optimize (edge)

## Architecture (locked)

```text
Twin/SDK  --HTTPS once-->  edge OPTIMIZE job
                             capture → kernel → loss → stepper → router
```

- General engine: `cloudlabs_edge_dev.optimization` (no `cloudlabs` / `lab_model` imports).
- Lab seams only: `optimization/capture_impl.py`, `optimization/router_impl.py`.
- Premade catalog = **this tree** `kernels/manifest.json` + `.pt` (not coordinator `schemas/kernels/`).
- Session kernels (`session.*`) may arrive in the job payload; never re-ship premade ids.
- `kernel_host._resolve_path` must map id → filename via **local** `manifest.json` `artifact` (ids use dots, files use underscores). Do not require `cloudlabs_edge_dev` on the bench just for that map; falling back to `{kernel_id}.pt` yields `builtin.roi_centroid.pt` (missing) when the file is `builtin_roi_centroid.pt`.

## Premades this experiment expects

| Id | Features | Objective habit |
|----|----------|-----------------|
| `builtin.roi_centroid` | `[cx, cy]` | `rms_distance` → pixel target |
| `builtin.beam_power` | `[flux, peak, sat]` | maximize flux; watch sat |
| `builtin.gaussian_beam_fit` | `[amp, cx, cy, σx, σy]` | often minimize σₓ |
| `builtin.beam_shift` | `[dx, dy, magnitude]` | maximize ‖Δ‖ from **this frame** `(W/2,H/2)`; weight −1; **no** reference capture |
| `builtin.beam_com` | `[cx, cy, peak]` | full-frame CoM; maximize signed offset from operator origin along X/Y ± (`signed_axis_offset`) |

Rebuild/seed: from cloud-labs repo `python scripts/ops/build_torchscript_kernels.py --seed-edges`.

## Do

- Kernels are **inputs** to `EVAL_KERNEL` / `OPTIMIZE`, not new verbs.
- In-loop capture: latched tensor (BGR), **no JPEG / LazyRef** per eval.
- Continuous blocks: `assert_optical_path_clear` (arm must not hold); bookkeeping block-scoped.
- Honor `solver.constraints` `max_delta_from_start` and `keep_best` / `rollback_on_fail`.
- Emit per-eval `stages: {capture, kernel, actuate}` on job progress (Twin debug panel).
- `GET /kernels` must reflect local manifest + `artifact_present`.
- Non-variable tunables stay at current lab values when capturing — see
  [`tunables-hold-for-measure`](../tunables-hold-for-measure/SKILL.md).

## Do not

- Do not import `cloudlabs` or `lab_model` on the edge.
- Do not silently downgrade `mode=ensemble` to legacy single-tag COBYLA without `pipeline`.
- Do not bake a fixed camera center (e.g. 2790) into beam_shift — use frame size.
- Do not add private `/kernel/*` HTTP outside the contract.
- Do not invent a separate OPTIMIZE exposure (or other tunable) — use the lab's current value.
- Do not resolve premade paths as `{kernel_id}.pt` when a manifest `artifact` exists.

## Typical files

- `kernels/manifest.json`, `*.pt`, `kernel_host.py`
- `adapters/optimize.py`, `adapters/observe.py`, `adapters/motion.py` (`clear_for_measure`)
- `optimization/capture_impl.py`, `optimization/router_impl.py`
