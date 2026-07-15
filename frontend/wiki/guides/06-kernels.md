# Kernels — measurements, not verbs

A **kernel** is a **measurement function** the edge can run on a capture—
typically a TorchScript model over a camera frame. It returns a scalar score or
a small feature vector (centroid, beam widths, and similar). **Primitives**
still perform actuation; kernels help **OPTIMIZE** (and authoring probes)
*observe*.

A kernel is not another kind of lab command.

## Three shelves

Naming overlaps; keep these distinct:

| Kind | Definition | Where it appears |
|------|------------|------------------|
| **Catalog TorchScript** | Shipped `.pt` + manifest (`demo.*`, `builtin.roi_centroid`, …) | Wiki **Catalog → Kernels** |
| **Session kernels** | Author an `nn.Module`, `register_kernel` for this lease | Scripts such as `04_session_kernels.py` |
| **Runtime “builtin” hooks** | Python ensemble plumbing (`ensemble.eval.*`) | Listed in Catalog; not image models |

Ids such as `builtin.roi_centroid` are **TorchScript catalog** entries
(“builtin” = product-shipped). A Wiki badge `builtin` on `ensemble.*` denotes
internal hooks—a different shelf.

## Physical examples

- **`builtin.roi_centroid`** — `(cx, cy)` of the spot in a central ROI;
  alignment error in pixels.
- **`demo.image_mean_score`** — approximate frame brightness; a teaching
  scalar, not a calibrated power meter.
- **Session kernel** — optics math encoded by the author (within TorchScript
  limits).

## Attachment to actions

```text
probe_kernel  →  EVAL_KERNEL primitive   (one-shot authoring measurement)
run_cobyla / run_optimize  →  OPTIMIZE   (kernel ids are inputs to the objective)
```

**Registering** a kernel uploads a package. It does not move hardware. Mirrors
move because OPTIMIZE or MOVE (or another actuation primitive) said so.

## Practice

1. Wiki → **Catalog** → **Kernels** → `builtin.roi_centroid` → **Physical
   interpretation**.
2. Run `scripts/language/02_kernels_and_match.py`.
3. Compare with `04_session_kernels.py`—catalog shelf versus session-authored
   measurement.

Next: [Imperative vs closed-loop](#)—where the control loop runs.
