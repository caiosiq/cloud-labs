# Backend isolation and coordinator vs edge ownership

**Status:** Phases 0–3 + ownership split + edge catalog resolve  
**Last updated:** 2026-07-31  
**Audience:** cloud-labs maintainers wiring multi-backend Twin / edge  

**Related:** [`RECORD_TUNABLES_AND_SYNC_RUNTIME.md`](./RECORD_TUNABLES_AND_SYNC_RUNTIME.md), [`EDGE_CONTRACT_AND_UC_LIVE_PLANE.md`](./EDGE_CONTRACT_AND_UC_LIVE_PLANE.md)

---

## 1. Goals

1. **Per-`backend_id` isolation** for version control and working lab-state.
2. **Coordinator-owned language commits** after successful edge primitives — edges execute; Twin FSM is applied on the coordinator.
3. **Clear ownership:** edge authors library / inventory / layout; coordinator owns VC + working FSM + Twin-only overlays.
4. Edges stay **executors**; they do not invent Twin FSM writers.

---

## 2. Ownership (normative)

| Owner | Path / API | Contents |
|-------|------------|----------|
| **Coordinator** | `coordinator_data/<backend_id>/` (auto-created on probe/connect) | Working `lab_state.json`, `control/` (VC), `laser_lines.json`, `recipes/`, optional `catalog_store/` pins |
| **Edge** | `data/library.json`, `data/inventory.json`, bench layout, motor tracking | What the lab has, geometry, physical RECORD/SYNC truth, streams / teleop / `runtime_sync` |
| **Teaching only** | `mock_backend/lab_view`, `simulation_edge/lab_view` | Local edge host bootstrap (layout/library/motors). Twin/VC SoT is still `coordinator_data/<id>/` |

[`schemas/backends.json`](../schemas/backends.json) registers **identity + reachability** (`backend_id`, `edge.base_url`, optional `lab_view_path` for in-process teaching). It does **not** hand-author a fat clone of edge catalogs for real backends.

**Do not** put library, inventory, layout, or motor_rotations under `coordinator_data/` as source of truth.

**Derived:** Twin “active catalog” = inventory keys on the edge — not a second authored `active_catalog.json` SoT on the coordinator.

---

## 3. Twin merge + commits

- Edge returns structured primitive results (e.g. `{holding: true}`).
- After successful **remote** southbound, coordinator runs `commit_*` on that backend’s working lab-state.
- Twin `GET /api/lab-state` **merges**:
  - **Coordinator wins:** `system_status`, `holding`, presence, commanded tunables
  - **Edge wins:** `runtime_sync`, stream URLs, live teleop samples

---

## 4. Explicit non-goals

- Teaching the real edge to hand-write `HOLDING` into edge `lab_state.json`
- Sharing one `coordinator_data` tree across backends
- Storing camera frames in coordinator lab-state
- Hand-authoring `backends/<id>/lab_view` clones (removed; use `coordinator_data/`)

---

## 5. Debug prefixes

| Prefix | Use |
|--------|-----|
| `[backend]` | Registry bind, ensure `coordinator_data/<id>`, shared-path warnings |
| `[control]` | VC ops with `backend=` / `repo=` |
| `[lab_state]` | Seed / coordinator commit (not every Twin poll) |
| `[lab_init]` | SYNC_RUNTIME / READY only |
| `[edge]` | Sparse southbound result notes (optional) |

Ownership mistakes log under `[backend]` or `[lab_state]` with an explicit reason.

---

## 6. Phased delivery (history)

| Phase | Exit |
|-------|------|
| 0–3 | Isolation, working store, remote commits (checkpoint) |
| **Ownership** | Thin `coordinator_data/`, skills, retire fat `backends/real.default` |
| **Catalog** | Twin `/api/catalog*` / `/api/library` / `/api/inventory` via `resolve_edge_catalog` (HTTP edge or teaching `cloudlabs_edge/data/`) — never `coordinator_data/` |
| **PR D** | `mock_edge` → `mock_backend` (folder, package, ops scripts) |
