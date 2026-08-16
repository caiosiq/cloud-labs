---
name: tunables-hold-for-measure
description: >-
  Keep non-optimized lab tunables fixed when capturing measurables (RECORD,
  EVAL_KERNEL, OPTIMIZE). Use when wiring capture exposure, SET_EXPOSURE,
  observe.capture_tensor, or any path that might invent OPTIMIZE-only defaults.
---

# Tunables hold for measure (edge)

## Invariant (locked)

When obtaining a measurable — including every OPTIMIZE eval capture:

1. **Actuators / tunables that are OPTIMIZE variables** may move (stepper + router).
2. **Every other tunable stays at its current lab value** (last SET / RECORD /
   set_at_init / library default already in effect).
3. There is **no separate “optimize exposure”** (or similar) knob. Capture uses
   the same settings the rest of the lab would use for that camera/source.

```text
lab tunables (exposure, power, …)  ──hold──►  measurable capture
OPTIMIZE variables (motors, pose, …) ──move─►  only those paths
```

## Why this is not one middleware

Different measurables need different settings (camera exposure vs laser power vs
future filters). There is no single “apply all tunables” call before every
capture. Enforce the invariant **per capture seam**:

- Resolve needed settings from **current lab state**, never invent OPTIMIZE-only
  hard-codes that bypass that state.
- Worked example: `adapters.observe.resolve_exposure_s_for_tag` — order is
  explicit override → camera last commanded → tunable overlay → library default
  → hardware last resort.
- `SET_EXPOSURE` must `commit_tunable` so overlays match hardware.
- Live preview brightness is separate: `SET_LIVE_EXPOSURE` / START_LIVE_FEED
  `exposure_time_ms` apply recorder `VEXP` only and must **not**
  `commit_tunable(exposure_time_ms)`.

## Do

- Before CAP / RECORD / OPTIMIZE capture, resolve exposure (and any future
  capture-affecting tunable) from lab state as above.
- Keep OPTIMIZE router scoped to `variable_ids` in the active block; do not nudge
  unrelated motors/poses “for the measurement.”
- When adding a new capture-affecting tunable, extend a resolver (or mirror the
  exposure pattern) — do not add a Twin Optimizer UI field unless the tunable is
  itself an OPTIMIZE variable.

## Do not

- Do not hard-code CAP exposure (e.g. 50 ms) when `exposure_time_ms` is already
  set on the lab / camera / library default.
- Do not snapshot a second “optimize exposure” into the pipeline unless the
  operator explicitly chose exposure as a variable.
- Do not reset tunables to library defaults mid-OPTIMIZE unless the job says so.

## Typical files

- `adapters/observe.py` (`resolve_exposure_s_for_tag`, `capture_tensor`, RECORD)
- `adapters/tunables.py` (`SET_EXPOSURE` + `commit_tunable`)
- `cameras/base.py` / `mindvision_tcp.py` (last commanded exposure)
- `optimization/capture_impl.py`

## Related

- [`kernels-optimize`](../kernels-optimize/SKILL.md) — closed-loop job shape
- [`measurables-tensors`](../measurables-tensors/SKILL.md) — envelope after capture
- [`optimize-pipeline-debug`](../optimize-pipeline-debug/SKILL.md) — stage triage
