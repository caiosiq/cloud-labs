# Declarative objective graphs (Phase E)

**Status:** Implemented (MVP)  
**Last updated:** 2026-07-10  
**Related:** [`ENSEMBLE_OPTIMIZATION.md`](./ENSEMBLE_OPTIMIZATION.md) §8, [`PROGRAMMABLE_LAB_VISION.md`](./PROGRAMMABLE_LAB_VISION.md) §7 Phase E, [`EXECUTION_MODES.md`](./EXECUTION_MODES.md) §3.3

---

## 1. Purpose

Scripts and the Twin UI optimization wizard can describe objectives in **authoring form** (measurable **fields** per tag) instead of hand-writing runtime `ObjectiveSourceSpec` JSON. A backend **compiler** lowers graphs to the existing ensemble IR; **preflight** validates metrics, tags, and measurable paths before `OPTIMIZING`.

The closed-loop runtime (COBYLA session, edge metrics, trace) is unchanged.

---

## 2. Authoring IR (`ObjectiveGraphSpec`)

```json
{
  "version": 1,
  "type": "weighted_sum",
  "minimize": true,
  "terms": [
    {
      "id": "centroid_rms_px",
      "weight": 1.0,
      "tag_id": "tag_20",
      "field": "camera_image",
      "metric": "rms_distance_px",
      "target_px": { "x": 512.0, "y": 384.0 }
    },
    {
      "id": "optimization_score",
      "weight": 0.35,
      "tag_id": "tag_20",
      "field": "last_optimization_score",
      "metric": "one_minus_normalized"
    }
  ]
}
```

| Field | Meaning |
|-------|---------|
| `version` | `1` — distinguishes authoring graphs from runtime objective JSON |
| `terms[].field` | Catalog measurable id (`camera_image`, `last_optimization_score`, …) |
| `terms[].metric` | Optional; defaults per field (see `graph.py`) |
| `terms[].target_px` | Centroid target for `camera_image` → `derived_centroid` |
| `terms[].normalize` | Optional `{min,max}` for scalar fields |
| `terms[].source` | Advanced: pass-through runtime source (skips lowering) |

Examples: `schemas/objective_graph_examples/`.

---

## 3. Compiler lowering

| Authoring | Runtime `source.kind` | Metric |
|-----------|----------------------|--------|
| `field: camera_image` | `derived_centroid` (`from: measurables.camera_image`) | `rms_distance_px` |
| `field: <scalar>` | `measurable_scalar` (`path: measurables.<field>`) | explicit or `one_minus_normalized` default for known fields |

Implementation: `backend/lab_model/optimization/compiler.py`.

The frontend `buildAuthoringObjectiveGraph()` mirrors the same field → source mapping as `buildObjectiveTermPayload()` in `optimization-builder.js`.

---

## 4. Preflight (objective sources)

`preflight_ensemble()` now validates objective terms after variable paths:

- Metric id ∈ `METRIC_REGISTRY`
- `tag_id` exists in runtime `components`
- `measurable_scalar` path prefix and field key on component
- `derived_centroid` requires `camera_image` measurable and `target_px`

Implementation: `preflight_objective_sources()` in `preflight.py`.

---

## 5. API

### `GET /api/optimization/metrics`

Returns registered metric ids.

### `POST /api/optimization/compile`

**Body:**

```json
{
  "graph": { "...ObjectiveGraphSpec..." },
  "preflight": true,
  "parameters": { "...optional full ensemble payload..." }
}
```

**Response:**

```json
{
  "ok": true,
  "objective": { "...runtime ObjectiveSpec..." },
  "preflight": { "ok": true },
  "parameters": { "...when parameters + preflight..." },
  "x0": { "v_m1": 0.0 }
}
```

Twin UI optimization **Run** calls this with `preflight: true` before submitting a closed-loop job.

---

## 6. Python SDK

```python
from lab_model.optimization.sdk import ObjectiveGraphBuilder, compile_objective

graph = (
    ObjectiveGraphBuilder()
    .term(term_id="cam", tag_id="tag_22", field="camera_image", weight=1.0)
    .term(
        term_id="score",
        tag_id="tag_20",
        field="last_optimization_score",
        metric="one_minus_normalized",
    )
    .compile()
)

# Or HTTP client session state:
# builder.compile_and_preflight(lab_state_dict)
```

Module: `backend/lab_model/optimization/sdk/objective.py`.

---

## 7. Real bench objective coverage

On **real** backends (`real.*`), preflight runs with `strict_real_objectives=True` and validates catalog + runtime:

| Term kind | Authoring field / metric | Real bench behavior |
|-----------|--------------------------|---------------------|
| `derived_centroid` | `camera_image` → `rms_distance_px` | Capture resolves to the first **OPTICAL_CAMERA** with hardware binding (e.g. mirror term on `tag_20` captures via `tag_22`) |
| `measurable_scalar` | `output_power_readback_mw` | Must reference a **LASER_SOURCE** tag (`tag_50`); read via `_hardware_read_laser_output_power_mw` |
| `measurable_scalar` | `last_optimization_score` | State read only; optional image fallback for mock-style proxies |

Implementation: `lab_model/optimization/objective_measurements.py` (planner) + `lab_communicator/real/ensemble.py`.

Example payload: `schemas/ensemble_optimization_examples/real_bench_centroid_laser.json`.

**Not supported on real v1:** invasive `touch_and_go` objective coupling (mirror-only motor variables).

---

## 8. Out of scope (defer)

- Visual graph editor / DAG nodes with chained ops
- New objective aggregators beyond `weighted_sum`
- Symbolic lazy `MeasurableTensor` graphs in SDK
- Edge TorchScript kernels (Phase F)

---

*Implementation PRs must link here when changing compiler IR or preflight rules.*
