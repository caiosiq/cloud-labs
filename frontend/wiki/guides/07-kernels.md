# Kernels — measurements, not verbs

A **kernel** is a **measurement function** the edge can run on a capture—
typically a TorchScript model over a camera frame. It returns a scalar score or
a small feature vector (centroid, beam widths, and similar). **Primitives**
still perform actuation; kernels help **OPTIMIZE** (and authoring probes)
*observe*.

A kernel is not another kind of lab command.

## Where the catalog lives

For remote / teaching / real backends, the **authoritative premade list** is the
active edge’s `cloudlabs_edge/kernels/` tree (`manifest.json` + `.pt` files).

Twin **Wiki → Backends → Kernels** and Optimization mode presets call
`GET /api/kernels`, which **proxies that edge**. Coordinator
`schemas/kernels/` is a CI / fixture shelf only — not what the real bench runs.

## Three shelves

| Kind | Definition | Where it appears |
|------|------------|------------------|
| **Catalog TorchScript** | Edge-owned `.pt` + manifest (`builtin.*`, `demo.*`) | Wiki **Backends → Kernels**; Twin Optimization presets |
| **Session kernels** | Author an `nn.Module`, `register_kernel` for this lease | Scripts such as `04_session_kernels.py` |
| **Runtime “builtin” hooks** | Python ensemble plumbing (`ensemble.eval.*`) | Listed under Backends → Kernels; not image models |

## Premades this lab ships

| Id | Output | Typical objective |
|----|--------|-------------------|
| `builtin.roi_centroid` | `[cx, cy]` | Align CoM to a pixel target (`rms_distance`) |
| `builtin.beam_power` | `[flux, peak, sat]` | Maximize flux (`one_minus_normalized` on flux); watch saturation |
| `builtin.gaussian_beam_fit` | `[amp, cx, cy, σx, σy]` | Minimize σₓ (`minimize_value`) |
| `builtin.beam_shift` | `[dx, dy, magnitude]` | **Maximize** ‖Δ‖ from **this frame’s** `(W/2, H/2)` — weight −1 on magnitude. No reference capture; resolution-agnostic |
| `builtin.beam_com` | `[cx, cy, peak]` | Full-frame CoM (same gate as beam_shift). Pair with `signed_axis_offset` + operator origin / axis / ± direction |

If a preset button is disabled in Optimization mode, the edge listed the id but
`artifact_present` is false — seed/rebuild that edge’s `kernels/` tree.

## Attachment to actions

```text
probe_kernel  →  EVAL_KERNEL primitive   (one-shot authoring measurement)
run_cobyla / run_optimize  →  OPTIMIZE   (kernel ids are inputs to the objective)
```

**Registering** a kernel uploads a package. It does not move hardware. Mirrors
move because OPTIMIZE or MOVE (or another actuation primitive) said so.

## Practice

1. Select the backend (e.g. `real.default`) → Wiki **Backends → Kernels** — confirm
   builtins match that machine’s edge files.
2. Twin **Optimization mode** → Edge kernel presets (same catalog).
3. Scripts: `02_kernels_and_match.py`, `04_session_kernels.py` for session shelf.

Next: [Imperative vs closed-loop](#)—where the control loop runs.
