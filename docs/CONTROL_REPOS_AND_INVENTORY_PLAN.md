# Control repos, uncommitted runtime, and inventory-driven catalog

**Status:** Phase A2 complete; Phase B next  
**Last updated:** 2026-06-26  
**Related:** [`CONTROL_RUNTIME_AND_VERSIONING.md`](./CONTROL_RUNTIME_AND_VERSIONING.md), [`CONTROL_PANEL_UX_BACKLOG.md`](./CONTROL_PANEL_UX_BACKLOG.md)

---

## 1. Problem statement

Three layers are partially conflated today:

| Layer | Today | Target |
|-------|--------|--------|
| **Runtime** | Live `lab_state` (working table) | Always exists; editable without a repo |
| **Catalog** | Static `/api/catalog` from manifest | Can grow when parts are added from inventory (Phase B) |
| **Control repo** | `control/{repo_id}/` commits + branches | Optional VC overlay; first commit starts history |

Branch/repo switching assumed the **same component set**. Different repos may imply different catalogs and placements. Adding a part from inventory must update runtime + catalog and localize pose without a full-table rescan (Phase C).

---

## 2. Core concepts

### 2.1 Three-way separation

```
Runtime (working tree)     ← always live, not versioned directly
    ↑ soft/hard checkout
Control repo (commits)     ← optional; empty until first Commit
    ↑ references
Catalog (active library)   ← tag ids + capabilities; may differ per repo (Phase D)
```

**Uncommitted runtime:** The bench can be valid with **no commits**. VC UI: “No commits — Commit to start history.” View mode applies when `viewing ≠ applied`, not when “no repo.”

### 2.2 Repo binds a configuration history (not the physical bench)

Each repo stores under `control/{repo_id}/`:

- `refs.json` — branch HEADs + **applied** bench pointer (per repo)
- `configurations/` — commit DAG
- `repo.json` — display name, created_at (Phase A1)

On commit (future Phase D): validate `catalog_hash` + tag set.

### 2.3 Applied vs viewing (implemented)

- **Applied** — last hard-applied or committed configuration for **this repo**
- **Viewing** — soft-checked-out preview on the table
- **View mode** — `viewing ≠ applied` → orange frame, read-only canvas, badge

---

## 3. Multi-repo lifecycle

### Phase A1 (this sprint) ✓ target

- `GET /api/control/repos` — list repos + summary
- `POST /api/control/repos` — create empty repo
- UI: repo dropdown + “New repo…”
- Switch repo with confirmation (like branch switch)
- Empty repo → graph empty; runtime unchanged until first Commit
- Repo with **applied** → soft-checkout applied on switch (canvas matches that repo’s bench)
- First **Commit** snapshots current runtime and sets HEAD + applied

### Phase A2 — uncommitted runtime UX ✓

- HEAD pill shows **Working table** when branch has no commits (not green HEAD)
- Graph empty: “Working table — uncommitted…”
- `normalizeControlViewState()` clears stale preview when uncommitted
- View mode blocks: canvas, commands, pose refresh, pencil/guides, commit/fork, sidebars/panels
- Orange chrome only when `viewing ≠ applied`

### Phase B — add from inventory

- `POST /api/components/add` through RuntimeManager
- UI: “Add to table” on OFF_TABLE inventory rows
- Active catalog merge in `GET /api/catalog`

### Phase C — scoped pose refresh

- `GET/POST refresh-pose` with `tag_ids` / `apply_tag_ids`
- Mock → real → MuJoCo communicator hooks

### Phase D — compatibility + wizard

- Checkout report: missing tags in catalog/runtime
- “Bring bench to config” wizard

---

## 4. Repo switch rules (A1)

| Target repo | Behavior |
|-------------|----------|
| No commits | Runtime unchanged; empty graph; applied null |
| Has applied | Soft-checkout applied; viewing cleared if matches applied |
| In view mode before switch | Confirm; then rules above |

Runtime **never** requires a repo. Commits are optional checkpoints.

---

## 5. Open decisions (locked 2026-06-26)

All recommendations accepted by operator:

1. **Catalog:** Global active catalog; commits store `catalog_hash` + tag set for validation (Phase D).
2. **Mutations in view mode:** Block except Return to bench / Apply.
3. **First commit on new repo:** Snapshot current runtime; applied pointer set automatically.
4. **Real lab add:** Scoped pose refresh first; full rescan as power-user fallback (Phase C).

---

## 6. Module map

```
backend/lab_model/state/control_manager.py   list_repos, create_repo
backend/main.py                              GET/POST /api/control/repos

frontend/js/api/control.js                   fetchControlRepos, createControlRepo
frontend/js/ui/control-repo.js               switch + create UX
frontend/js/ui/control-panel.js              wire repo select
frontend/js/control/control-state.js         applied/viewing (done)
```

---

## 7. Phase checklist

| Phase | Status |
|-------|--------|
| A0 Applied pointer, branch switch, view mode | Done |
| **A1 Repo list/create/switch** | **Done** |
| A2 Uncommitted runtime UX | Done |
| B1–B2 Inventory add + catalog | Pending |
| C1–C2 Scoped pose refresh | Pending |
| D1–D2 Compatibility wizard | Pending |
