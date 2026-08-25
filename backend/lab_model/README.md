# Lab model: UC language, execution, coordinator

This folder is the **`lab_model`** Python package: shared domain logic for the
digital twin (**not** hardware I/O). Hardware lives in **`mock_backend/`** (teaching)
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
    parameters.py  # static UC identity (GET_PARAMETERS)
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

## Component shape (UC triple + telemetry)

Each catalogued part is a **Universal Component** with three noun bags (Wiki Learn →
Components):

| Bag | Role | Read / write |
|-----|------|----------------|
| **Parameters** | Static identity (type, size, hardware binding, …) | `GET_PARAMETERS`; authored in the library, not by jogs/captures |
| **Tunables** | Commanded DOFs (pose, exposure, motors, …) | Write primitives; observe may recalculate the **same** tunable |
| **Measurables** | Observations without a setpoint (`camera_image`, …) | `RECORD_MEASURABLES` / resolve |

Runtime entries in `components[tag_id]` use `statecontrol` (tunables + measurables)
and `telemetry` (teleop + live_feed); parameters come from the catalog (often
mirrored for Twin display). Mutations of live state go through **primitives** only.

See Wiki Learn and [`ARCHITECTURE.md`](ARCHITECTURE.md) for the full UC model.
