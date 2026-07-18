# Lab model: UC language, execution, coordinator

This folder is the **`lab_model`** Python package: shared domain logic for the
digital twin (**not** hardware I/O). Hardware lives in **`mock_edge/`** (teaching)
or a lab **`cloudlabs_edge/`** process, reached via **`execution.edge`**.

**Platform map:** [`ARCHITECTURE.md`](ARCHITECTURE.md) ·
**Commands:** [`language/primitives/README.md`](language/primitives/README.md) ·
**Surfaces / VC:** [`../../docs/LAB_SURFACES_VC_AND_INITIALIZATION.md`](../../docs/LAB_SURFACES_VC_AND_INITIALIZATION.md) ·
**Edge:** [`../../docs/EDGE_CONTRACT_AND_UC_LIVE_PLANE.md`](../../docs/EDGE_CONTRACT_AND_UC_LIVE_PLANE.md)

## Layout

```text
lab_model/
  language/        # what Twin / SDK speak
    domain/
    primitives/
    tunables/  measurables/  telemetry/
  execution/       # how verbs run (still no drivers)
    orchestration/
    edge/
    optimization/
  coordinator/     # this server’s control plane
    backends/
    catalog/
    jobs/
    state/
  platform.py
```

### Naming (aggregates)

| Term | Meaning |
|------|---------|
| **Runtime** | Live working-tree JSON (`current_state`) |
| **Configuration** | All tunables (+ holding intent) — versioned by **ControlManager** |
| **Observations** | All measurables — pinned, not branched |
| **Setup** | Configuration + observations at a tagged checkpoint |

**ControlManager** is configuration version history — not a lab-side experiment manager.

## Component state shape

Each entry in `components[tag_id]` uses `statecontrol` (tunables + measurables)
and `telemetry` (teleop + live_feed). Mutations go through **primitives** only.

See Wiki Learn and [`ARCHITECTURE.md`](ARCHITECTURE.md) for the full UC model.
