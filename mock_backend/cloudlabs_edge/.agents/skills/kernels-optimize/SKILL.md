---
name: kernels-optimize
description: >-
  Provisions TorchScript kernels and runs EVAL_KERNEL / OPTIMIZE on a Cloud Labs
  edge. Use when wiring kernel_host, session kernels, scoring camera frames, or
  closed-loop optimization jobs.
---

# Kernels and optimize

## Do

- Treat kernels as **inputs** to primitives (`EVAL_KERNEL`, `OPTIMIZE`), not new verbs.
- Use `kernel_host.py` for provision (b64 or URI + digest), cache, and `eval_on_bgr`.
- Derive `capabilities.features.torchscript_execution` from real torch availability.
- Run capture → score → actuate **on the edge** inside OPTIMIZE jobs.
- Bake targets into the job before submit (no arbitrary Python callback each eval).

## Do not

- Do not add `/kernel/probe` outside the contract.
- Do not fail with a stack trace when torch is missing — refuse as unavailable.
- Do not re-download unverified artifacts every eval; verify digest, then cache.

## Authoring vs closed loop

- One-shot score: `EVAL_KERNEL` / SDK `probe_kernel`.
- Many evals: `OPTIMIZE` job / SDK `run_cobyla` / `run_optimize`.

## Typical files

- `kernel_host.py`, `adapters/optimize.py`, `adapters/observe.py`.
