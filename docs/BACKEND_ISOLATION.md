# Backend isolation and coordinator vs edge ownership

**Status:** Phases 0–4 (isolation, ownership, catalog, fail-closed, bench, remote commits)  
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
- After successful **remote** southbound, coordinator runs `commit_*` on that backend’s working lab-state for: pick / hover / place / confirm, move / store / place-from-storage, affirm-placed, **START_TELEOP / END_TELEOP**, **START_LIVE_FEED / END_LIVE_FEED**.
- **Coordinator owns Twin `system_status` for remote edges:** around each southbound execute the working store flips `IDLE → BUSY →` quiescent (`IDLE` or `HOLDING` from commits). Edges must **not** invent Twin FSM writers or “busy” side APIs so the Stash/dirty UI works — Twin merge already prefers coordinator status over edge `lab_state`.
- **Coordinator owns Twin live-feed session flags** (`telemetry.live_feed.*.live|connected`) the same way as teleop. Twin UI sends catalog channel `stream`; southbound remap arms edge `{tag}.camera_image`. Edge `/lab-state` that hardcodes `live: false` must not clobber an armed Twin session.- Soft checkout / control status / commit / stash / checkout-report read the working store (`LabStateStore`) and resolve catalog tags via **edge inventory** (`resolve_edge_catalog`), not an in-process communicator or `active_catalog.json` on HTTP backends.
- Twin `GET /api/lab-state` **merges**:
  - **Coordinator wins:** `system_status`, `holding`, presence, commanded tunables,
    `telemetry.teleop` session flags (`active` / `ready` / mode / command) after
    remote `START_TELEOP` / `END_TELEOP`
  - **Edge wins:** `runtime_sync`, stream URLs / `live_feed`, live teleop *samples*
    (not session lease flags — real edges often hardcode `teleop.active=false`)
- After successful remote southbound, coordinator also commits teleop start/end
  (edge already finished hardware arming; Twin marks `active`+`ready` together).
- **Once per edge process session** (sim + real HTTP): when the edge advertises a
  new `edge_session_id` and `runtime_sync.status == ready`, Twin replaces the
  working lab-state from that edge snapshot (`LabStateStore.reset_from_new_edge_session`)
  so commanded poses match the physical RECORD/SYNC. Twin-only overlays
  (`alignment_guides`, `laser_lines`) are preserved. Until READY, only
  seed-if-empty + inventory membership reconcile run.
- Twin `GET /api/lab-layout` resolves via edge `GET /bench` (HTTP) or teaching `cloudlabs_edge/bench/layout.json` / `lab_view/layout.json` — never `coordinator_data/` as layout SoT.
- **Storage geometry lockstep:** each backend’s edge `bench/layout.json` storage rectangle must match the geometry used to pack that backend’s lab-state poses. Mock must declare `extent_from_origin_mm` (or `bounds_mm`) on the edge bench — a bare `negative_xy` full-Q3 grid will draw large cells while poses sit on a denser teaching grid, so Twin looks like everything is piled in a few slots (not a cross-backend inventory leak).
- `BackendSession` rebinds process-global `storage_region` from the **active** edge bench on every request (see `bind_storage_geometry`) so mock Q3 cannot stick when serving real/sim.
- API middleware **fail-closed**: every `/api/*` call (except backends listing / job submit lease paths) requires `?backend_id=` or `X-CloudLabs-Backend`. Shared `lab_view` / `coordinator_data` paths fail boot unless `CLOUDLABS_STRICT_LAB_VIEW=0`.

---

## 4. Explicit non-goals

- Teaching the real edge to hand-write `HOLDING` / `BUSY` into edge `lab_state.json` for Twin
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
| **Phase 4** | Fail-closed missing `backend_id`; doctor requires capabilities `backend_id`; layout via `resolve_edge_bench`; remote commits beyond in-air |
