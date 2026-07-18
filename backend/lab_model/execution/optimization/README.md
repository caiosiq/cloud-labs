# Ensemble optimization (`lab_model/optimization`)

Cloud-labs ensemble closed-loop optimization lives here — **not** in the sibling
`lab_automation` repo (hardware drivers and `OpticalExperiment` motion only).

| Module | Role |
|--------|------|
| `spec.py` | Pydantic models for variables / objectives / solvers |
| `session.py` | Inner COBYLA loop and trace |
| `backend.py` | `EnsembleEvaluationBackend` protocol |
| `metrics/` | Pure loss math + shared edge image features |
| `sdk/` | Imperative `CloudLabsClient` + job helpers |
| `graph.py` | Authoring IR — field-based objective terms (Phase E) |
| `compiler.py` | Lower graphs → runtime `ObjectiveSpec` |
| `objective_measurements.py` | Real/mock measurement planner (capture tag resolve, scalar readers) |
| `preflight.py` | Static validation before `OPTIMIZE` (variables + objective sources) |

Communicator-specific bridges:

- `lab_communicator/mock/ensemble.py` — synthetic landscape
- `lab_communicator/real/ensemble.py` — motors + camera capture on the bench

See [`docs/ENSEMBLE_OPTIMIZATION.md`](../../../docs/ENSEMBLE_OPTIMIZATION.md),
[`docs/OBJECTIVE_GRAPH.md`](../../../docs/OBJECTIVE_GRAPH.md), and
[`docs/LAB_SURFACES_VC_AND_INITIALIZATION.md`](../../../docs/LAB_SURFACES_VC_AND_INITIALIZATION.md).
