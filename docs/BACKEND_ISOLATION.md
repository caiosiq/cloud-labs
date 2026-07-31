# Backend isolation and coordinator-owned commits

**Status:** Phase 0–3 landed on this branch (Phase 4 polish next)  
**Last updated:** 2026-07-31  
**Audience:** cloud-labs maintainers wiring multi-backend Twin / edge  

**Related:** [`RECORD_TUNABLES_AND_SYNC_RUNTIME.md`](./RECORD_TUNABLES_AND_SYNC_RUNTIME.md), [`EDGE_CONTRACT_AND_UC_LIVE_PLANE.md`](./EDGE_CONTRACT_AND_UC_LIVE_PLANE.md)

---

## 1. Goals

1. **Per-`backend_id` isolation** for version control (`control/`) and working lab-state — backends must not share a `lab_view` tree.
2. **Coordinator-owned language commits** after successful edge primitives — edges execute motion; the Twin FSM (`HOLDING`, presence, commanded tunables) is applied on the coordinator.
3. Edges stay **executors**; they do not invent Twin FSM writers.

---

## 2. Contract (normative)

### 2.1 Namespacing

| Surface | Scope |
|---------|--------|
| Working lab-state + `control/` | Namespaced by `backend_id` via that backend’s `lab_view_path` |
| Official catalog **content** (library pins) | May be copied/shared as *content* |
| Applied / working configuration tree | Always per backend |

Two enabled backends **must not** resolve to the same absolute `lab_view` root. Startup logs a warning (strict mode may fail).

### 2.2 Edge vs coordinator roles

- Edge returns **structured primitive results** (e.g. `{holding: true}`, poses).
- Coordinator runs existing language `commit_*` helpers on **that backend’s** working lab-state after southbound success.
- Twin reads a **merge**:
  - **Coordinator wins:** `system_status`, `holding`, presence, commanded tunables
  - **Edge wins (overlays):** `runtime_sync`, stream URLs, live teleop samples

### 2.3 Durable VC (near term)

Coordinator path: `backends/<backend_id>/lab_view/control/` (or any unique `lab_view_path` in `schemas/backends.json`).  
Edge-owned `data/control/` is a later migration path, not required for isolation.

### 2.4 Explicit non-goals

- Teaching the real edge to hand-write `HOLDING` into `lab_state.json`
- Sharing `lab_view_path` across backends “for convenience”
- Storing camera frames in coordinator lab-state

---

## 3. Phased delivery

| Phase | Exit |
|-------|------|
| **0** — this doc | Team agrees on contract |
| **1** — isolation | Separate real `lab_view`; ControlManager keyed by `(backend_id, repo_id)`; shared-path warn; Twin mock vs real show different VC trees |
| **2** — working lab-state | `LabStateStore` per backend; `GET /api/lab-state` merges coordinator FSM + edge overlays; mock aliases host RuntimeManager |
| **3** — commits | After remote PICK/HOVER/PLACE/CONFIRM success, `_southbound_execute` → `apply_edge_primitive_commit` → Twin HOLDING |
| **4** — harden | Skills, doctor, leftover globals, fail-closed missing backend header |

Suggested PR order: **A** Phase 1 → **B** Phase 2 → **C** Phase 3 → **D** polish.

---

## 4. Debug prefixes

| Prefix | Use |
|--------|-----|
| `[backend]` | lab_view bind, shared-path warnings |
| `[control]` | VC ops with `backend=` / `repo=` |
| `[lab_state]` | coordinator commit / merge source (Phase 2+) |
| `[lab_init]` | SYNC_RUNTIME / READY only |
