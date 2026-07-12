# Session edge kernels + feature objectives

**Status:** Implemented (MVP)  
**Related:** [`EXECUTION_MODES.md`](./EXECUTION_MODES.md) (optional kernel blobs), [`OBJECTIVE_GRAPH.md`](./OBJECTIVE_GRAPH.md)

## Purpose

Authors register TorchScript packages under an imperative lease, measure with them, then submit closed-loop jobs that run the **same** artifacts on the edge. Feature kernels return vectors; objective weights/targets stay in ensemble IR (retunable without recompiling).

## Policy

| Backend | Session register |
|---------|------------------|
| `mock.*` | Always allowed |
| Other | Only when `CLOUDLABS_ALLOW_SESSION_KERNELS=1` |

Scratch root override: `CLOUDLABS_KERNEL_SESSION_ROOT`.

## Lifecycle

1. `POST /api/kernels/session` (lease required) → `session.<backend>.<name>.<hex>`
2. Authoring-time eval: `POST /api/kernels/eval` or SDK `lab.eval_kernel(...)`
3. SDK exports session artifacts into ``kernel_packages`` on submit, **releases the
   imperative lease**, then submits so the job runner can acquire the backend
4. Job stages packages into job scratch + digest archive (`result.kernel_audit`)
5. Lease scratch is deleted on imperative lease release; job scratch after the job ends

## I/O contract

| Kind | Kernel output | Objective source `kind` |
|------|---------------|-------------------------|
| Scalar | float | `torchscript_scalar` |
| Features | 1D float vector | `torchscript_features` |

Metrics that consume features: `squared_error` (optional `feature_index`), `rms_distance` (`feature_index: [i,j]` + `target`/`target_px`), `minimize_value` (`feature_index`).

## SDK sketch

```python
kid = lab.register_kernel(
    "beam_features",
    module=MyModule(),
    output_kind="features",
    feature_names=["cx", "cy", "radius"],
)
feats = lab.eval_kernel("tag_22", "camera_image", kernel_id=kid)

graph = (
    ObjectiveGraphBuilder()
    .term(
        term_id="center",
        source={
            "tag_id": "tag_22",
            "kind": "torchscript_features",
            "kernel_id": kid,
            "from": "measurables.camera_image",
            "feature_index": [0, 1],
            "target_px": {"x": 512, "y": 384},
        },
        metric="rms_distance",
        weight=1.0,
    )
    .term(
        term_id="tight",
        source={
            "tag_id": "tag_22",
            "kind": "torchscript_features",
            "kernel_id": kid,
            "from": "measurables.camera_image",
            "feature_index": 2,
        },
        metric="minimize_value",
        weight=0.5,
    )
    .build()
)
```

Catalog fixtures: `demo.image_mean_score`, `demo.roi_mean_score`, `demo.peak_intensity` under `schemas/kernels/`.
