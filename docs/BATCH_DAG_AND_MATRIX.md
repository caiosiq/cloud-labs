# Batch DAG + Command Matrix

**Status:** Phases 0–5 complete (operator mock/deathray soak remains manual)  
**Repos:** `cloud-labs` (planner + matrix); **every edge** declares `reconcile_staging_seats` on `GET /bench`  
**Related:** [`COMMAND_MATRIX.md`](./COMMAND_MATRIX.md), reconcile seat model below

## Goal

Support **groups of primitives** with real dependencies (order, swaps, staging), while keeping:

- **Command Matrix** for hardware-thread parallelism (arm ‖ sense ‖ motors)
- **One shared planner** for seat DAGs (not reimplemented on every edge)
- **OPTIMIZE** latency: inner eval loop stays on the edge; outer job is a matrix barrier

```text
Batch intent  →  plan_batch (DAG + staging)  →  enqueue with predecessors[]
                                              →  matrix drain (thread free ∧ preds done)

OPTIMIZE job  →  matrix full-row barrier
              →  edge session (trivial/simplified actuate; no full seat DAG on hot path)
```

## Two layers

| Layer | Owns | Does not own |
|--------|------|----------------|
| **Planner (`plan_batch`)** | What must precede what; Park/Unpark; fail-closed capacity | Which thread runs an op; Twin queue chrome |
| **Command Matrix** | Threads, FIFO, TeleOp/OPTIMIZE barriers, **predecessor gates** | Inventing swap order or free space |

Casual single clicks stay independent enqueues. DAG planning is for **declared batches**: VC Apply, future group-move, (optional) spatial optimize steps.

## Three batch situations

| Kind | Source | Planner | Matrix |
|------|--------|---------|--------|
| **Group move** (future) | Multi-select MOVE | Full seat DAG | Preds + arm/motor threads |
| **VC reconcile** | `S_current` → `S_target` | Full seat DAG + staging | Preds; motors/tunes after spatial |
| **OPTIMIZE actuate wave** | Solver setpoints per eval | **Simplified** (usually empty DAG) | Outer OPTIMIZE barrier only; actuate on edge |

## Seat model (reconcile / group-move)

- **BenchSeat** — quantized pose for bookkeeping; **footprint collision** drives deps  
- **StorageSlot(i,j)** — durable Q3 inventory  
- **StagingSeat(k)** — reconcile/group scratch only; layout-declared; **never** in VC commits  

Occupation: `tag_id → pose + footprint`. **Part–part collision** uses the Twin rule (circumscribed circle √(w²+h²)/2 + layout pad, default 5 mm) — same as `checkCollision` / `storage_region._collides`. Not arm-path planning.

### Diff classes

| Class | Meaning | Base op |
|-------|---------|---------|
| `remove` | on bench now, not in target table | `STORE` |
| `add` | not on table now, on bench in target | `PLACE_FROM_STORAGE` |
| `move` | on bench both sides, seat changes | `MOVE` |
| `stay` | same bench seat | no spatial op (tunes OK) |

### Dependency rule

Edge `A → B` (“A before B”) when **B’s target footprint collides with A’s current footprint** and A is leaving (move/remove/park), or Park/Unpark pairing, or sequenced storage capacity. Two target footprints that collide → fail closed (`target_overlap`).

**Cycles:** break with staging — Park one tag → others move → Unpark.

### Phases (after cycle-break)

1. Park → 2. Remove → 3. Move → 4. Add → 5. Unpark → 6. Tune  

### Staging vs storage

Prefer **layout `reconcile_staging_seats`**. Durable storage stays STORE/PLACE inventory semantics. HOLDING is not the general buffer.

### Fail closed

Before execute: peak storage demand, staging count, illegal target, missing inventory → structured report (`needs_staging_n`, `cycle_tags`, …).

## Blocked heads (matrix)

A queue head may be first on its thread but **not runnable** until predecessors are `done`:

```text
claim when: thread.running is free ∧ predecessors all done
```

Failed/cancelled predecessor → fail or cancel dependents (policy: fail with reason). Unrelated threads keep draining.

## OPTIMIZE (simplified / latency)

- Outer: `OPTIMIZE` = matrix **full-row barrier** (already).  
- Inner: `cloudlabs_edge_dev.optimization.session` + lab `ActuatorRouter`.  
- v1 variables = **continuous tunables** (motors, exposure); breadboard MOVE/STORE stay outside the inner loop.  
- Actuate wave = parallel setpoints on the edge (**trivial DAG**).  
- Edges do **not** implement seat DAG; skeleton ships router/capture only. If spatial-in-loop ever appears, **import** the same planner package — do not fork.

## Mapping onto Cloud Labs today

| Piece | Today | Target |
|-------|-------|--------|
| Diff | `configuration_diff` | Keep + seat occupation layer |
| Plan | `plan_reconcile` flat list | `plan_batch` DAG |
| Execute | Twin / matrix enqueue | Same primitives + `predecessors` |
| Compatibility | `checkout_compatibility` | Staging/cycle/capacity issues |
| Layout | bench JSON | `reconcile_staging_seats: [{x,y,rotation}, …]` |
| Config extract | `extract_configuration` | Refuse/strip staging from commits |

## Phased implementation

### Phase 0 — Spec (this doc)

- [x] This document  
- [x] Pointer from [`COMMAND_MATRIX.md`](./COMMAND_MATRIX.md)  
- [ ] Pointer from Onboarding hard-jump caveat (when ready)

### Phase 1 — Matrix predecessors

- [x] `predecessors` on queued items  
- [x] `claim_runnable` / peek honor preds; snapshot `blocked_on`  
- [x] Twin Command queue shows blocked / waiting state  
- [x] Unit tests: cross-thread A→B; unrelated sense still runs; failed pred fails dependent  

### Phase 2 — Shared `plan_batch` + reconcile

- [x] Seat model + Park/Unpark + capacity report  
- [x] Layout schema `reconcile_staging_seats`  
- [x] Wire `plan_reconcile` spatial path through planner  
- [x] Strip staging from commits / doctor  
- [x] Tests: swap, cycle-3, storage-peak fail, target overlap  

### Phase 3 — Batch enqueue API

- [x] `POST /api/command-batch` (or equivalent) for declared batches  
- [x] Returns `command_ids`, plan report, matrix snapshot  
- [x] Single `POST /api/command` unchanged  

### Phase 4 — OPTIMIZE clarification

- [x] Docs: tunables-only inner loop; trivial actuate DAG  
- [x] Optional `apply_setpoints` helper in edge_dev (`cloudlabs_edge_dev.optimization.apply_setpoints`)  
- [x] Refuse or shared-import if spatial vars appear in-loop (`SpatialSetpointError`; `tunables_only_inner_loop` / `CLOUDLABS_OPTIMIZE_TUNABLES_ONLY`)  

### Phase 5 — Polish

- [x] Checkout UI surfaces staging needs (plan roles + `batch_plan` on 409)  
- [x] Multi-arm ready via same pred model (unit coverage)  
- [ ] Mock swap soak; deathray matrix soak *(operator — not mock-hardcoded logic)*  

### Edge contract (general)

Staging seats are **layout**, not mock code: every edge’s `bench/layout.json` / `GET /bench` may declare `reconcile_staging_seats`. Scaffold ships two example seats. Planner + matrix live in the coordinator and apply to any backend_id.

## Non-goals (v1)

- Continuous path / arm-sweep collision  
- Staging seats inside VC commits  
- DAG reimplemented per edge skeleton  
- Cobyla actuate via Twin command round-trips  
- HOLDING as general staging  

## Worked examples

- **A↔B swap, staging≥1:** Park A → Move B → Unpark A.  
- **Remove B from P, place A at P:** Store B → Place A (no staging).  
- **Optimize iter:** SET motors in parallel on edge; matrix shows one OPTIMIZE barrier.  
- **Two arms, A before B:** B may be head on arm.1 but blocked until A done on arm.0.
