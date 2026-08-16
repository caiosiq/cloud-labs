# Command Matrix — multi-thread lab scheduler

**Status:** Phase 3 hardening (cancel API, lease safe-stop, matrix reconcile)  
**Repos:** `cloud-labs` (scheduler + Twin), `robot-deathray` / mock / sim (advertise threads only)  
**Branch:** `feat/command-matrix`

## Goal

Replace single-flight `BUSY → HTTP 409` with a **lease-scoped, backend-local command matrix** so operators can enqueue many primitives (e.g. Store × 5) without waiting for each to finish.

- **Edge** advertises **threads** (topology only).
- **Coordinator** owns **routing, mode locks, barriers, queues, drain**.
- **Twin / SDK** enqueue and observe; they do not schedule.
- **Backend = indivisible queue unit** (one lease per backend ⇒ one matrix).

This generalizes what VC reconcile already does for one plan (ordered sequence) to everyday Twin clicks.

## Motivation

Today:

```text
Click Store A → BUSY → Click Store B → 409 "Please wait"
```

Desired:

```text
Click Store A, B, C, D, E quickly → all accepted → arm drains FIFO
Motors / other threads may run in parallel when independent
```

## Layering

| Layer | Owns | Does not own |
|--------|------|----------------|
| **Edge** (`capabilities.json`) | Shared threads: `arm.0`, `sense.0`, … | Per-tag motors, queue, HOLDING rules, OPTIMIZE barriers |
| **Coordinator** (cloud-labs server) | Scheduler, routing table, mode locks, accept/refuse, drain; **creates** `motor.<tag_id>.<motor_id>` columns on demand | Hardware motion |
| **Twin / SDK** | Enqueue UX, show per-thread queues / barriers | Scheduling policy |
| **Lease** | Who may enqueue on this backend | Thread topology |

No duplicate scheduler on the edge — `/execute` stays single-shot.

## Mental model

```text
BackendRuntime (leased)
  └─ CommandMatrix
       ├─ threads: { "arm.0": Queue, "sense.0": Queue, "motor.tag_20.1": Queue, … }
       ├─ barriers: OPTIMIZE (full-row fence)               ← occupies all columns
       └─ sessions: TELEOP claims on a subset of threads
```

Think of threads as **columns**, queue depth as **rows**. A normal primitive is one (or a few) cell(s). An **OPTIMIZE** job is always a **full row**: it waits until every column is clear ahead of it, then blocks everything behind it until done.

```text
arm.0:          [STORE A] [STORE B] |======== OPTIMIZE ========| [MOVE C]
motor.tag_20.1: [SET m1 ]           |======== OPTIMIZE ========| [SET m1]
motor.tag_11.1: [SET m1 ]           |======== OPTIMIZE ========|
```

Motor ids are **local to a component** (many tags reuse `1` / `2`). Matrix columns are therefore tag-scoped.

## Ownership / lease

- Each backend allows **one** active session lease.
- The queue is therefore both **per lease** and **per backend** — same unit.
- The lease holder owns the **full ensemble** on that table; another lease would invalidate intent (e.g. cavity vs crystal).
- Multi-backend experiments for one user are **out of scope**.

## Thread advertisement (edge)

Edges declare **shared** topology only, e.g. in `capabilities.json`:

```json
{
  "execution_threads": [
    { "id": "arm.0", "kind": "arm" },
    { "id": "sense.0", "kind": "sense" }
  ]
}
```

**Motors are not listed on the edge.** Inventory reuses `motor_id` per tag (`tag_20` motor `1` ≠ `tag_11` motor `1`). The coordinator creates columns lazily:

```text
SET_MOTOR_SETPOINT(target=tag_20, motor_id=1) → motor.tag_20.1
```

**Fallback** (if `execution_threads` missing): synthesize `arm.0` + `sense.0`.

If a pending OPTIMIZE barrier already exists when a motor column is first created, that barrier is extended onto the new column so work cannot sneak past the fence.

Deathray / mock / sim only advertise shared threads; they do not implement queues.

## Server policy (Cloud Labs)

### Routing (Phase 1 defaults)

| Primitive family | Threads |
|------------------|---------|
| MOVE / STORE / PLACE / PICK / HOVER / PLACE_FROM_HOVER / CONFIRM_HOLDING | `arm.0` |
| MOVE_MOTOR / SET_MOTOR_SETPOINT / MOTOR_SEND_HOME / MOTOR_SET_ZERO | `motor.<target_id>.<motor_id>` (created on demand) |
| SET_EXPOSURE / RECORD_MEASURABLES / EVAL_KERNEL | `sense.0` |
| START/END_LIVE_FEED, SET_LIVE_EXPOSURE | `sense.0` |
| OPTIMIZE (ensemble) | **All threads** (barrier) |
| START/END_TELEOP, TELEOP_* | Session lock on `arm.0` (jog/goto bypass queue) |

Multi-resource items: declare a **resource set**; admit only when **all** required threads can take the item together.

### Mode locks (thread-local)

After `PICK` → HOLDING on an arm thread:

- **Allowed** on that arm: `HOVER`, `PLACE_FROM_HOVER`, `CONFIRM_HOLDING_TAG` (same held tag), TeleOp on that tag.
- **Other threads** (motors, future second arm) stay open unless policy says otherwise.
- If a lab had two arms, only the holding arm’s thread would be mode-locked.

### OPTIMIZE barrier

1. Wait until every thread is idle ahead of the barrier (and no HOLDING/TELEOP unless explicitly allowed — default: refuse).
2. Occupy **all** threads until the ensemble run finishes.
3. Block enqueue behind the barrier on every column.

### TeleOp (session lock, not a barrier row)

- Claims relevant thread(s) until `END_TELEOP`.
- Compatible with HOLDING: TeleOp while hovering keeps the arm in the hold-mode lock; ending TeleOp does **not** place the part.

## Queued item shape

```text
command_id, validated envelope, lease_id
resources: set[thread_id]
kind: normal | barrier | session_lock
status: queued → running → done | failed | cancelled
```

## Admission order

1. Valid `lease_id` for this backend?
2. Route primitive → resource set (server table).
3. Mode-lock / TeleOp legality on those threads.
4. If barrier: enqueue as full-row fence (runs when all clear).
5. Else: append to required thread queue(s).
6. Return `{ status: "queued", command_id, resources, … }` — **not** 409-for-BUSY.

Still **409** for: wrong/missing lease, illegal mode (e.g. STORE while HOLDING another tag), validation errors.

## Drain

- Per thread: run head when free and legal.
- Southbound path unchanged (`_southbound_execute` / edge `/execute`).
- On complete: existing commits; wake dependents / barriers.
- Coordinator continues to own Twin `system_status` for remote edges.

### `system_status` (Phase 1)

- Keep a lab-level summary: `BUSY` if any matrix thread is `running`; else quiescent (`IDLE` / `HOLDING` / `TELEOP` / `OPTIMIZING` as today).
- Expose per-thread queue snapshot for Twin (queued + running).

## API / Twin

| Surface | Behavior |
|---------|----------|
| `POST /api/command` | Validate → **enqueue** → queued ack |
| `GET /api/command-queue` | Per-thread queues + running + session locks |
| `POST /api/command-queue/cancel` | Cancel one queued id or `all_queued` |
| `POST /api/command-queue/status` | Lookup statuses for reconcile / await |
| Twin | Confirm still per click; **Command queue** always shows arm/sense (idle muted); motor columns only while busy |
| Cancel | Queued cancel via API + Twin “Clear queued”; running orphaned on lease release |
| VC reconcile | Enqueues through matrix; Applying badge = plan progress; live threads stay on Command queue (no duplicate) |
| SDK | Same enqueue; optional await helper later |

## Phased rollout

### Phase 0 — Spec + topology (this doc + schema)

- [x] This document
- [x] `execution_threads` in edge capabilities (mock / sim / deathray)
- [x] Pointer from `EXECUTION_MODES.md`

### Phase 1 — MVP matrix (mock + Twin enqueue)

- [x] `CommandMatrix` on backend runtime (in-process)
- [x] Thread map from capabilities (+ arm/sense fallback; motors on demand)
- [x] Replace busy-409 with enqueue for leased mutating commands (when matrix enabled)
- [x] Async drain per thread → existing southbound / in-process execute
- [x] Arm HOLDING mode lock
- [x] Twin: treat `status: queued` as success; keep pending overlays
- [x] Feature flag: `CLOUDLABS_COMMAND_MATRIX` (default on for `mock.*` / `sim.*`, off for `real.*`)
- [x] `GET /api/command-queue` snapshot
- [x] Tag-scoped motor columns (`motor.<tag_id>.<motor_id>`)
- [x] Twin queue strip UI (arm/sense always; motors only while busy; VC apply uses Command queue, not a duplicate)

**Acceptance:** Store five parts quickly → all accepted → arm runs in order; motor setpoints can run in parallel with arm when enqueued.

### Phase 2 — Barriers & sessions

- [x] OPTIMIZE full-row barrier (refuses while HOLDING / TeleOp / running barrier)
- [x] TeleOp session lock (claims arm until END_TELEOP; refuses other arm work)
- [x] Live-feed / sense threading (`sense.0`)
- [x] Ops closed-loop jobs enqueue OPTIMIZE through the same matrix when enabled
- [x] Unit tests for session lock + sense parallel

### Phase 3 — Hardening

- [x] Cancel queued (`POST /api/command-queue/cancel`); status lookup (`/status`)
- [x] Lease release safe-stop: cancel queued, orphan running (`lease_released`), clear session locks
- [x] VC reconcile enqueues through the matrix when enabled (bulk enqueue + await drain)
- [x] Tests: cancel-all, statuses, lease release orphan/teleop clear
- [ ] Real-bench soak (deathray): set `CLOUDLABS_COMMAND_MATRIX=1` and exercise Store×N + reconcile

### Phase 4 — Out of scope for now

- Multi-arm threads as first-class topology
- Multi-backend experiments for one user

## Repo split

| Repo | Work |
|------|------|
| **cloud-labs** | Scheduler, API, Twin queue UI, tests, docs, mock/sim thread advertisement |
| **robot-deathray** | Advertise `execution_threads` only |

## Risks / open decisions

1. **Feature flag** — keep real benches opt-in until soak.
2. **Confirm + queue** — five confirms ⇒ five queued items (intentional).
3. **In-process vs HTTP edge** — matrix always on coordinator; edge never sees the queue.
4. **Motor thread lifetime** — columns created on first motor enqueue persist for the lease/matrix lifetime (not torn down when idle).
5. **Deathray caps** — advertise `arm.0` + `sense.0` only (done alongside mock/sim); motors stay coordinator-lazy.

## Related docs

- [`EXECUTION_MODES.md`](./EXECUTION_MODES.md) — lease, modes, historical single-flight note
- [`BACKEND_ISOLATION.md`](./BACKEND_ISOLATION.md) — coordinator owns Twin `system_status` around southbound
- [`LAB_SURFACES_VC_AND_INITIALIZATION.md`](./LAB_SURFACES_VC_AND_INITIALIZATION.md) — Twin / Ops / lease surfaces
