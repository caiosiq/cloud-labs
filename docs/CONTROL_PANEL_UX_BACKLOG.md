# Control panel UX — reported issues, design backlog, and next steps

**Status:** active backlog (2026-06-25)  
**Related:** [`CONTROL_RUNTIME_AND_VERSIONING.md`](./CONTROL_RUNTIME_AND_VERSIONING.md) (canonical VC spec), [`universal_component_architecture.md`](./universal_component_architecture.md)

This document captures operator-reported friction after Phase 4–6 (configuration commits, soft/hard checkout, sidebar timeline). Each item is reviewed for whether it is a **bug**, a **UX gap**, or a **new feature**, then mapped to a concrete fix.

---

## 1. Summary table

| # | Report | Verdict | Root cause (today) | Proposed fix |
|---|--------|---------|----------------------|--------------|
| A | Banner “not live HEAD” on fresh start (no commits) | **Fixed** | CSS `display:flex` on `.control-view-banner` overrides the HTML `[hidden]` attribute | `[hidden] { display: none !important; }` + reset `viewingCommitId` on boot |
| B | Banner persists after first commit (same id as HEAD) | **Fixed** | Same CSS issue + clicking HEAD still runs soft checkout and sets `viewingCommitId` | CSS fix; HEAD click info-only; clear `viewingCommitId` on HEAD select |
| C | “Return to HEAD” appears to do nothing | **Fixed** | State may update but banner stays visible; or `liveHeadId` null so handler returns early | CSS fix + ensure `liveHeadId` synced; clear `viewingCommitId` on return |
| D | “Apply on bench” flashes “already on HEAD” | **Fixed** | Operator sees banner → clicks Apply; logic correctly rejects because `selectedCommitId === liveHeadId` | Banner fix; hide Apply when selected === HEAD |
| E | “Workspace snapshots” confuses “Configuration” | **Done** | Legacy `states/` save/load predates ControlManager | Removed UI + `/api/states/*` (2026-06-25) |
| F | “Refresh pose” should warn when scan delta is tiny | **Done (mock)** | Refresh ran immediately without tolerance preview | `GET /api/lab-state/refresh-pose/offers` + modal (2026-06-25) |
| G | Sidebar timeline is wrong place for branch graph | **Done** | Phase 4 MVP: vertical list in left panel | SVG graph above canvas (2026-06-25) |

---

## 2. Item A–D — “Viewing configuration … not live HEAD” (bugs)

### 2.1. What you reported

1. On first lab start (no configuration commits yet), the amber banner already reads **“Viewing configuration … — not live HEAD on this branch.”**
2. After **Save configuration** (first commit), the banner still shows the **same commit id** as HEAD.
3. **Return to HEAD** has no visible effect.
4. **Apply on bench** briefly shows that the selected commit **is already HEAD** (hard to read before it disappears).

### 2.2. Does this make sense as real bugs?

**Yes.** Under the intended model ([`CONTROL_RUNTIME_AND_VERSIONING.md` §3.5](./CONTROL_RUNTIME_AND_VERSIONING.md)):

- **No commits** → no HEAD, no “viewing” state → **no banner**.
- **Single commit that is HEAD** → runtime matches HEAD → **no banner**.
- **Return to HEAD** → soft-checkout HEAD tunables, clear view mode → **banner must hide**.

The current behavior violates all three expectations.

### 2.3. Root cause analysis

#### Primary: CSS overrides `[hidden]`

In `frontend/index.html`:

```css
.control-view-banner {
    display: flex;
    ...
}
```

The banner node uses the boolean `hidden` property from JS (`banner.hidden = !viewing`). In browsers, author `display: flex` **wins over** the default `[hidden] { display: none }` unless we add an explicit rule such as:

```css
.control-view-banner[hidden] {
    display: none !important;
}
```

**Effect:** the banner is **always visible** after CSS loads, regardless of control-panel logic. That explains the warning on a fresh project before any commit exists.

#### Secondary: soft checkout on HEAD click

In `control-panel.js`, every timeline click calls `viewConfiguration(id)`, which:

1. POSTs soft checkout (even when `id === liveHeadId`)
2. Sets `store.control.viewingCommitId = commitId`

When HEAD and viewing ids match, `isViewingHistoricalConfiguration()` returns `false` — **correct** — but the banner stays up because of the CSS bug. The operator reasonably believes they are “viewing” a non-HEAD commit.

#### Tertiary: “Return to HEAD” / “Apply on bench”

- **Return to HEAD:** likely **does** clear `viewingCommitId` and re-fetch lab state, but the banner never hides (CSS).
- **Apply on bench:** correctly blocks when `commitId === liveHeadId`; message may appear in the **log strip** (easy to miss) or as an error modal under another overlay.

### 2.4. Recommended fixes (small, Phase 4.1)

| Step | Change | Files |
|------|--------|-------|
| 1 | Add `.control-view-banner[hidden] { display: none !important; }` | `index.html` |
| 2 | On boot / `refreshControlPanel`: if no `liveHeadId`, force `viewingCommitId = null` | `control-panel.js` |
| 3 | Timeline click: if `id === liveHeadId`, **clear** view mode (no soft checkout); else soft checkout | `control-panel.js` |
| 4 | `returnToLiveHead`: always clear `viewingCommitId`; skip redundant soft checkout when already at HEAD | `control-panel.js` |
| 5 | Hide **Apply on bench** when `selectedCommitId === liveHeadId` (already partially done; verify after CSS fix) | `control-panel.js` |
| 6 | Add smoke test checklist to Phase 4 exit criteria | `CONTROL_RUNTIME_AND_VERSIONING.md` |

**Estimated effort:** ~1–2 hours. **Risk:** low.

---

## 3. Item E — Remove “Workspace snapshots” from UI

### 3.1. What you reported

“Workspace snapshots” is confusing next to **Configuration** (ControlManager commits).

### 3.2. Does this make sense?

**Yes.** We now have two overlapping persistence concepts:

| Mechanism | Storage | Purpose | VC-aware |
|-----------|---------|---------|----------|
| **Save configuration** | `{LAB_VIEW}/control/{repo}/configurations/` | Tunable intent, branch graph, diff, checkout | Yes |
| **Workspace snapshot** | `{LAB_VIEW}/states/*.json` | Full runtime JSON + optional UI (guides) | No |

For operators, two “save” buttons in the same sidebar invites mistakes: saving a snapshot when they meant a configuration commit (or vice versa).

### 3.3. Recommendation

**Remove from default UI** (sidebar section + buttons in `index.html`, wiring in `app-main.js`).

**Keep backend routes** (`/api/lab-state/save`, `/load`, list) temporarily:

- Mark **deprecated** in OpenAPI/comments.
- Document migration: use **Save configuration** + optional future **Save setup** (configuration + observations pin).

**Optional later:** one-shot import of a legacy snapshot into a configuration commit (Phase 8 migration).

**Estimated effort:** ~1 hour UI removal + doc note. **Risk:** low (power users lose quick full-state dump unless we add “Export runtime JSON” under admin).

---

## 4. Item F — Refresh pose with tolerance offers (like session checkpoint)

### 4.1. What you reported

**Refresh pose** should list components whose scanned pose is **too close** to the current measurables and let the operator choose whether to apply updates — same interaction pattern as **session reconciliation** on boot.

### 4.2. Does this make sense?

**Yes, with one nuance.**

| Flow | Compares | Applies |
|------|----------|---------|
| **Session reconciliation** | Current runtime vs **last shutdown checkpoint** | Software-only restore (no hardware) |
| **Refresh pose (proposed)** | **New scan result** vs **current measurables.pose** | Updates measurables (and possibly tunables per existing refresh rules) via camera scan |

Both are “**offer / opt-in per tag**” flows: avoid silent micro-jitter and avoid overwriting poses the operator trusts.

**Nuance:** refresh pose **does** command or simulate hardware scan (mock/real differ). The modal should say that explicitly, unlike session reconciliation’s “hardware is not commanded.”

### 4.3. Proposed design

1. **`GET /api/lab-state/refresh-pose/offers`**
   - Run scan in **dry-run** mode (or compute poses without committing).
   - For each on-table tag (respecting `preserve_tag_ids` pre-filter if sent):
     - `delta_mm`, `delta_yaw_deg` vs current `measurables.pose`
     - `within_tolerance` using manifest thresholds (reuse `session_reconciliation.position_mm` / `yaw_deg` or dedicated `pose_refresh` block).
   - Return `{ offers: [{ tag_id, current, proposed, delta_mm, delta_yaw_deg, within_tolerance }] }`.

2. **Frontend modal** (clone `session-reconciliation.js` patterns):
   - Pre-check tags **outside** tolerance (default selected).
   - Pre-uncheck tags **inside** tolerance with label “change smaller than ±X mm — skip?”
   - **Apply** → existing `POST /api/lab-state/refresh-pose` with `{ preserve_tag_ids: [...] }` **or** new `{ apply_tag_ids: [...] }` for clearer semantics.

3. **Manifest** (`lab_manifest.json`):

   ```json
   "pose_refresh": {
     "position_mm": 2.0,
     "yaw_deg": 1.0
   }
   ```

### 4.4. Phasing

| Sub-phase | Deliverable |
|-----------|-------------|
| F1 | Mock dry-run offers + modal (no behavior change if user accepts all) |
| F2 | Real bench: wire dry-run to scan pipeline |
| F3 | Unify threshold config + shared “delta table” component in frontend |

**Estimated effort:** F1 ~1–2 days; F2 depends on real scan API. **Risk:** medium (real scan side effects — must not move hardware in dry-run).

---

## 5. Item G — Git-style branch graph (not sidebar list)

### 5.1. What you reported

Branches as a **left sidebar list** feels wrong. Prefer a **visual graph**: nodes and edges like the Git logo — clickable, hoverable, with a **commit** action near the graph.

### 5.2. Does this make sense?

**Yes as the Phase 4+ target UX** described qualitatively in [`CONTROL_RUNTIME_AND_VERSIONING.md` §4.5](./CONTROL_RUNTIME_AND_VERSIONING.md) (“Timeline / graph”). The sidebar list was an MVP, not the end state.

**Conceptual model (matches Git logo):**

```text
        o  obs pin (future)
        |
    o---o---●  HEAD (main)
        |
        o---o    branch: experiment
```

- **Node** = configuration commit (`id`, message, author, time).
- **Edge** = `parent_id` link (directed, acyclic).
- **Branch label** = ref head (`refs.json` → colored tip).
- **HEAD** = highlighted node on active branch.

### 5.3. Interaction spec (draft)

| Gesture | Action |
|---------|--------|
| Hover node | Tooltip: message, short id, time, branch; optional mini diff vs HEAD |
| Click node | Soft checkout (view); ghost updates; banner only if not HEAD (after bugfix) |
| Double-click / “Apply on bench” on node | Hard checkout preview → confirm |
| Click **+ Commit** (near HEAD) | Same as **Save configuration** |
| Click branch name | Switch active branch filter |
| Drag / pan | Navigate large graphs |

### 5.4. Implementation options

| Option | Pros | Cons |
|--------|------|------|
| **SVG** in main panel (recommended v1) | Crisp, accessible, easy hit targets | Layout algorithm needed |
| **Canvas** | Performance for huge graphs | More work for hover/click |
| **Third-party** (e.g. dagre + d3) | Fast layout | Dependency weight |

**Layout:** simple layered DAG — mainline vertical, forks branch right (Git logo style). Reuse `parent_id` from API; no new backend for v1.

**Placement:** replace or supplement sidebar timeline with a **Configuration graph** panel above or beside the optical table (collapsible drawer), not buried under “Lab components.”

### 5.5. Phasing

| Sub-phase | Deliverable |
|-----------|-------------|
| G1 | Read-only SVG graph from `/history` + branch refs |
| G2 | Click/hover + HEAD highlight + remove sidebar timeline |
| G3 | Inline commit button, fork from node context menu |
| G4 | Observation pins on nodes (Phase 5 setups) |

**Estimated effort:** G1–G2 ~3–5 days. **Risk:** medium (layout edge cases with many forks).

---

## 6. Conceptual clarity — three “save” verbs (after cleanup)

After removing workspace snapshots from UI, operators should only see:

| UI label | Meaning |
|----------|---------|
| **Save configuration** | Commit tunable intent to ControlManager (branch HEAD advances) |
| **Save setup** *(future)* | Configuration commit + observations pin |
| **Refresh pose** | Update measurables from camera (with tolerance offers) |

**Runtime** remains the unpinned working tree; **Configuration** is versioned intent; **Observations** are pinned receipts.

---

## 7. Recommended implementation order

Prioritized for **maximum pain relief first**, then structural UX:

### Sprint 1 — Fix false “not live HEAD” ✅ (2026-06-25)

1. CSS `[hidden]` fix for `.control-view-banner`
2. HEAD-click / return-to-HEAD logic in `control-panel.js`
3. Clear `viewingCommitId` when no HEAD or when viewing === HEAD

**Exit:** Items A–D resolved.

### Sprint 2 — Remove workspace snapshots ✅ (2026-06-25)

4. Removed sidebar UI, `app-main.js` wiring, and `/api/states` routes

**Exit:** Item E resolved.

### Sprint 3 — Refresh pose offers (mock) ✅ (2026-06-25)

6. `GET /api/lab-state/refresh-pose/offers` + tolerance modal (reuses session reconciliation thresholds)
7. Mock scan is deterministic — preview matches apply

**Exit:** Item F on mock.

### Sprint 4 — Git graph v1 ✅ (2026-06-25)

8. SVG commit graph above canvas (`control-graph.js`) — nodes, edges, branch lanes, hover tooltips
9. Sidebar timeline retired; toolbar + graph in `#control-graph-panel`

**Exit:** Item G v1.

### Later (unchanged roadmap)

- Phase 5 observations pins + **Save setup**
- Phase 7 real + MuJoCo hard checkout parity
- Legacy snapshot → configuration import (optional)

---

## 8. Decisions (locked 2026-06-25)

| Question | Decision |
|----------|----------|
| Workspace snapshots | **Remove UI and backend API** (`/api/states/*`). Persistence moves to ControlManager (configuration commits / future setups). Legacy `states/` files may remain on disk for migration scripts only. |
| Graph placement | **Above canvas** — `#control-graph-panel` in `#main-content`, above bench chrome / table. |
| Refresh pose thresholds | **Reuse session reconciliation** manifest thresholds (`position_mm`, `yaw_deg`). |
| Single commit / HEAD click | **Info only** — log line, no soft checkout; clear `viewingCommitId`. |

---

## 9. References (code touched today)

| Area | Path |
|------|------|
| Banner visibility | `frontend/index.html` (`.control-view-banner`) |
| Control state | `frontend/js/ui/control-panel.js`, `frontend/js/state/store.js` |
| Control API | `frontend/js/api/control.js`, `backend/main.py` (`/api/control/...`) |
| Session reconciliation pattern | `frontend/js/ui/session-reconciliation.js`, `backend/main.py` (`/api/session-reconciliation/...`) |
| Refresh pose | `frontend/js/ui/pose-refresh.js`, `POST /api/lab-state/refresh-pose` |
| Workspace snapshots | `frontend/js/app-main.js`, `GET/POST /api/lab-state/save|load` |

---

*When Sprint 1 lands, update checkboxes in [`CONTROL_RUNTIME_AND_VERSIONING.md`](./CONTROL_RUNTIME_AND_VERSIONING.md) Phase 4 exit criteria.*
