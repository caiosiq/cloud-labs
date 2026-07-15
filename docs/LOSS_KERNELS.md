# Loss kernels (design plan)

**Status:** Planned (not implemented)  
**Related:** [`SESSION_KERNELS.md`](./SESSION_KERNELS.md), [`OBJECTIVE_GRAPH.md`](./OBJECTIVE_GRAPH.md), [`ENSEMBLE_OPTIMIZATION.md`](./ENSEMBLE_OPTIMIZATION.md)  
**Motivation:** Authors can ship feature kernels, but term losses are limited to a fixed metric catalog (`squared_error`, `rms_distance`, `minimize_value`, …). Custom shapes like \(\log(b/b_0)\) require either a new metric id or baking the formula into a scalar feature kernel. We want **loss kernels** as a first-class peer to feature kernels.

---

## 1. North star

| Role | Artifact | OPTIMIZE term uses it as |
|------|----------|---------------------------|
| **Feature / score kernel** (today) | TorchScript → scalar or features | Input to a **metric** (or standalone match) |
| **Loss / term kernel** (new) | TorchScript → **scalar term contribution** | Direct term value (then × `weight`) |

User language stays **primitives** (`OPTIMIZE`). Kernels remain **inputs / packages**. Loss kernels are another artifact kind those inputs may reference — not a new lab verb.

**Keep metric ids** for common cases and UI wizards. Loss kernels are the escape hatch for author-defined formulas without growing `METRIC_REGISTRY` forever.

---

## 2. Two authoring patterns (both supported)

### A — Single loss kernel on the image (MVP)

Kernel computes the full term from the camera tensor (constants baked at register/probe time):

```text
L_term = log(brightness / brightness0)     # brightness0 frozen in .pt or as buffer
OPTIMIZE term: source.kind = torchscript_loss, metric = identity (or omitted)
total = Σ weight_i * L_term_i
```

### B — Feature kernel + loss kernel (follow-on)

```text
features = feature_kernel(image)           # reusable
L_term   = loss_kernel(features, consts)   # formula
```

Requires a defined feature→loss wiring in the IR (which feature kernel, which loss kernel, how consts are passed). Defer to Phase 2 unless MVP proves too limiting.

**MVP ships pattern A.** Pattern B is explicitly phased.

---

## 3. IR changes

### 3.1 Source kind

Extend `ObjectiveSourceSpec.kind` with:

```text
torchscript_loss
```

Required fields:

| Field | Meaning |
|-------|---------|
| `kernel_id` | Catalog or `session.*` loss kernel |
| `from` / tag | Same capture path as scalar/features (`measurables.camera_image`) |
| Optional consts | Prefer **baked into the module** for MVP; later `source.constants: { brightness0: … }` if we add buffered inputs |

### 3.2 Metric handling for loss terms

Options (pick one in implementation):

1. **`metric: "identity"`** — registered metric that returns the measurement scalar as-is (clearest, least special-casing).
2. **Omit metric** when `kind == torchscript_loss` — schema makes `metric` optional for that kind only.

Prefer **(1)** so `ObjectiveTermSpec` stays uniform and `evaluate_weighted_sum` unchanged.

### 3.3 Registration

`output_kind: "loss"` (alongside `scalar` / `features`) on:

- `POST /api/kernels/session`
- catalog `schemas/kernels/manifest.json` entries (optional demos later)
- SDK `register_kernel(..., output_kind="loss")`

Preflight: `torchscript_loss` terms must reference a kernel whose declared `output_kind` is `loss` or `scalar` (accept scalar for MVP compat — loss is a scalar).

---

## 4. Runtime path

Today (`objective_measurements` → `evaluate_weighted_sum`):

1. Capture BGR for term  
2. If `torchscript_*` → `run_torchscript_output` → put value in measurement dict  
3. Metric fn(measurement, term) → raw  
4. × weight → sum  

**Loss kernel MVP:** same as `torchscript_scalar`, but:

- plan kind `torchscript_loss`
- measurement carries `value` / `scalar`
- metric `identity` returns that scalar

No per-eval HTTP `EVAL_KERNEL` — still in-process inside OPTIMIZE on the edge (same as feature/scalar kernels).

---

## 5. SDK / language surface

```python
loss_id = lab.register_kernel(
    "log_brightness_ratio",
    module=LogBrightnessRatio(brightness0),  # nn.Module, scalar out
    output_kind="loss",
)

lab.run_optimize(
    variables=[...],
    objective=(
        ObjectiveGraphBuilder()
        .term(
            term_id="log_ratio",
            weight=1.0,
            metric="identity",
            source={
                "tag_id": "tag_22",
                "kind": "torchscript_loss",
                "kernel_id": loss_id,
                "from": "measurables.camera_image",
            },
        )
    ),
)
```

Helpers (optional niceties):

- `lab.loss_term(kernel_id, tag=..., weight=1.0)` → term dict  
- Document that `run_cobyla` remains the **match-to-target** helper; custom formulas use `run_optimize` + loss kernels.

Language demo: extend [`scripts/language/04_session_kernels.py`](../scripts/language/04_session_kernels.py) or add `06_loss_kernels.py` with \(\log(b/b_0)\) vs the current squared-error IR terms.

---

## 6. Implementation phases

### Phase 0 — Spec lock (short)

- [ ] Confirm MVP = pattern A only (image → loss scalar)  
- [ ] Confirm `metric: "identity"` approach  
- [ ] Confirm consts baked into module for MVP (no IR constants yet)

### Phase 1 — Lab model MVP

- [ ] Add `identity` metric in `lab_model/optimization/metrics/`  
- [ ] Allow `kind: torchscript_loss` in planning (`objective_measurements.plan_objective_term`) and collect path (reuse scalar TorchScript branch)  
- [ ] Preflight: kernel exists; output is scalar/loss; `identity` registered  
- [ ] Session register accepts `output_kind="loss"`  
- [ ] Unit tests: parse objective, mock ensemble eval with a tiny scripted loss module  

### Phase 2 — SDK + demo

- [ ] `register_kernel(..., output_kind="loss")` documented  
- [ ] `ObjectiveGraphBuilder` / wiki snippet for loss terms  
- [ ] Language script demo: log-ratio loss kernel vs feature+metric terms  
- [ ] Update [`SESSION_KERNELS.md`](./SESSION_KERNELS.md) + Wiki copy  

### Phase 3 — Optional hardening

- [ ] IR `source.constants` fed as TorchScript buffers / extra inputs (retune without recompile)  
- [ ] Pattern B: `torchscript_features` → `torchscript_loss` chain in one term (or two-step measure)  
- [ ] Catalog demo loss kernels under `schemas/kernels/`  
- [ ] Twin UI: “custom loss kernel” term type in optimization builder  

### Non-goals (for this track)

- Author-laptop Python loss callbacks during the loop  
- Replacing the metric catalog (keep ids for wizards / simple matches)  
- Making loss-kernel eval a separate primitive (still OPTIMIZE in-process)  
- DAG step “EVAL_LOSS” unless a clear need appears  

---

## 7. Success criteria

1. Author can register a session loss kernel implementing \(\log(b/b_0)\) and drive COBYLA with it via OPTIMIZE.  
2. Existing feature + metric terms (`04`) keep working unchanged.  
3. Docs state clearly: **feature kernel vs loss kernel vs metric id**.  
4. Edge path uses the same TorchScript packaging as today’s session kernels.

---

## 8. Suggested first PR slice

Smallest vertical: `identity` metric + `torchscript_loss` treated like scalar in collect/preflight + one unit test + one script snippet. No UI. No constants IR. No feature→loss chain.
