# Frontend refactor plan

This document describes how to reorganize the lab UI so it stays **maintainable** as features grow. Today almost all behavior lives in **`frontend/app.js`** (~2.5k+ lines) plus **`index.html`** (large inline CSS). The **Command Console** is already split under **`frontend/js/`** (ES modules); the rest of the app should follow the same direction **without** changing product behavior during migration.

**Status:** plan only — implement after agreement.

---

## 1. Goals

| Goal | Meaning |
|------|--------|
| **Modular** | Clear files by concern (networking, canvas, recipes, …), not one mega-script. |
| **Navigable** | A new contributor finds “where recipes live” or “where lab state is updated” quickly. |
| **Incremental** | Refactor in **small PR-sized steps**; main UI keeps working between steps. |
| **No new mandatory build step (initially)** | Prefer **native ES modules** in the browser, matching `js/command-console.js`, unless we explicitly choose a bundler later. |
| **Stable public surface** | FastAPI still serves **`/static/app.js`** or a single **`/static/js/main.js`** entry; **`window.__commandConsoleDeps`** (or a successor) stays a thin, documented bridge. |

Non-goals for v1 of the refactor:

- Rewriting the UI in React/Vue/Svelte (unless you later decide to).
- Perfect purity; some shared mutable state for the canvas is acceptable if **owned by one module** and imported read-only elsewhere.

---

## 2. Current shape (baseline)

- **`frontend/app.js`** — constants, coordinate math, polling, commands, collision, canvas events, rendering, sidebar/recipes/library, modals, `init()`, video/table-cam/Cobyla, command-console hooks.
- **`frontend/index.html`** — layout + large **`<style>`** block + script tag(s).
- **`frontend/js/`** — `command-parse.js`, `command-complete.js`, `command-api.js`, `command-console.js` (already modular).
- **`frontend/debug.html`** — separate page; should be called out if scripts move.

---

## 3. Target layout (proposed)

Keep static hosting; add a clear tree under **`frontend/js/`**:

```
frontend/
  index.html                 # slim: structure + link to main entry + optional critical CSS
  debug.html
  app.js                       # DEPRECATED → thin shim OR removed after migration
  css/
    main.css                   # extracted from index.html (optional phase; can stay inline longer)
  js/
    main.js                    # entry: wires imports, calls bootstrap()
    config.js                  # LAB_* constants, POLLING_INTERVAL, grid offsets (pure)
    state/
      lab-state.js             # labState, ghostState, previousSystemStatus, forceGhostSync, pendingCommands
      ui-state.js              # selectedComponent, dragging, recipe flags, optimization flags (or split further)
    api/
      client.js                # fetchCatalogMap, fetchLabState, fetchRecipes, fetchLaserLine, …
      commands.js              # sendCommand, executeSendCommand (uses state + api client)
    canvas/
      coordinates.js           # mmToPx, pxToMm, scales
      collision.js             # getComponentSize, getComponentRadius, checkCollision
      render.js                # clearCanvas, drawLaserPath, drawComponent, render()
      interaction.js           # mousedown/move/up, wheel, drag from inventory (attaches listeners)
    ui/
      modals.js                # showErrorModal, showConfirmationModal
      context-panel.js         # updateContextPanel, ctx move, strategies wiring
      parameter-modal.js       # showParameterModal
      sidebar.js               # component list, library popup, catalog render
      recipes.js               # editor, play, requirements modals, golden checks
      log.js                   # log()
    features/
      video-feed.js
      table-cam.js
      cobyla-ref.js
    bootstrap.js               # init(), DOM refs, calls feature inits, exports __commandConsoleDeps
```

**Naming is flexible** — the important part is **boundaries**, not exact filenames.

---

## 4. Module boundaries (map from today’s `app.js`)

| Today (approx. section) | Future module(s) | Notes |
|---------------------------|------------------|--------|
| Constants + mm/px | `config.js`, `canvas/coordinates.js` | Pure functions, easy to test. |
| Networking / polling | `api/client.js` | All `fetch` to `/api/*` in one place; return data, minimal DOM. |
| `sendCommand` / `executeSendCommand` | `api/commands.js` | Depends on lab state + log + recipe editor callbacks. |
| Collision + sizes | `canvas/collision.js` | Needs `catalogMap` + ghost state (inject or import state module). |
| Canvas draw + `render` | `canvas/render.js` | Takes ctx, state snapshots, laser coeffs. |
| Mouse / drag / wheel | `canvas/interaction.js` | Registers listeners; calls into commands + render. |
| Context panel + move | `ui/context-panel.js` | |
| Strategy parameter modal | `ui/parameter-modal.js` | |
| Library / sidebar / catalog | `ui/sidebar.js` | |
| Recipes | `ui/recipes.js` | Large; can split `recipe-editor.js` vs `recipe-play.js` if needed. |
| Video / table cam / Cobyla | `features/*.js` | Each gets `initX(domRefs, deps)`. |
| `init`, DOM queries | `bootstrap.js` + `main.js` | Single place for `getElementById` where possible. |
| `__commandConsoleDeps` | `bootstrap.js` (or `api/commands-bridge.js`) | Document the contract next to code. |

---

## 5. Shared state strategy

The hardest part of splitting `app.js` is **mutable cross-cutting state** (`labState`, `ghostState`, `catalogMap`, `selectedComponent`, …).

**Recommended approach (pragmatic):**

1. **`js/state/lab-state.js`** (and siblings) export:
   - `getLabState()`, `setLabState(...)`, or explicit getters/setters for fields that must stay consistent.
   - Avoid exporting raw objects for **wide** mutation unless unavoidable; prefer `updateGhost(tag, pose)` style where it helps.

2. **Circular imports:** reduce by having **`api/client.js`** only return JSON; **`bootstrap.js`** or **`commands.js`** applies updates to state and triggers `render()`.

3. **`render()`** should live in **`canvas/render.js`** and accept **read-only snapshots** where possible, or import state getters.

If a cycle appears (`render` ↔ `interaction`), break it with a tiny **`events.js`** (`onLabStateUpdated(cb)`) or by passing **`{ render, getState }`** into `initInteraction(deps)` — same pattern as the Command Console’s `__commandConsoleDeps`.

---

## 6. Styles

- **Phase A (optional):** leave CSS in **`index.html`** until JS split stabilizes.
- **Phase B:** move to **`frontend/css/main.css`**; **`index.html`** links `<link rel="stylesheet" href="/static/css/main.css">` (FastAPI already mounts `frontend/` at `/static` — add `css/` folder).

Use **BEM-like** or existing prefixes (e.g. `.command-console__*`) for new rules to avoid collisions.

---

## 7. `index.html` / script loading

**Option A — Native ES modules (recommended first step)**  
- `<script type="module" src="/static/js/main.js?v=…"></script>`  
- **`main.js`** imports `bootstrap` and calls `bootstrap.start()`.  
- Remove inline dependency on legacy **`app.js`** once migration completes.

**Option B — Bundler (Vite / esbuild) later**  
- If you need tree-shaking, TS, or single-file deploy, introduce a **`package.json`** in `frontend/` and build to **`dist/`**; adjust FastAPI static mount. **Defer** until module graph is clear.

---

## 8. `debug.html`

- Audit which APIs it uses; either:
  - keep a **small dedicated `debug.js`**, or  
  - import shared **`api/client.js`** read-only helpers.  
- Document in README where debug assets live after the move.

---

## 9. Migration order (low risk → higher)

1. **Extract pure code** — `config.js`, `coordinates.js`, `collision.js` (minimal state coupling). Wire from `app.js` re-exports or imports (if using modules from a thin entry).  
2. **Extract `api/client.js`** — all fetches; `app.js` shrinks but still owns state.  
3. **Extract `commands.js`** — `sendCommand` / `executeSendCommand` + modals they need.  
4. **Extract `canvas/render.js`** — `render` + draw helpers.  
5. **Extract `canvas/interaction.js`** — event listeners last (most tangled).  
6. **Extract UI slices** — sidebar, recipes, context panel, parameter modal.  
7. **Extract features** — video, table-cam, Cobyla.  
8. **Collapse `app.js`** into **`bootstrap.js` + main entry**; delete or 20-line shim **`app.js`** for one release if anything external still references `/static/app.js`.  
9. **CSS extraction** (optional).  
10. **Update README** — how to run, where modules live, how Command Console hooks in.

Each step should be **one mergeable change** with manual smoke test: load UI, poll state, move, recipe, console, debug page.

---

## 10. Testing & quality

- **Manual checklist** per PR: main canvas, refresh, move + confirm, recipe record/play, Command Console tab/hint, table cam (if real), debug page.  
- **Optional later:** a few **Node** tests for pure modules (`coordinates`, `parsePartialLine` already in `command-complete.js`) via `node --test` or vitest **without** full DOM.

---

## 11. Risks & mitigations

| Risk | Mitigation |
|------|------------|
| Circular ES module imports | State module + `bootstrap` orchestration; pass `deps` into `init*`. |
| Cache staleness (see below) | Dev: **`NoCacheMiddleware`** on `/static` (already). Entry script: optional **`?v=`** / server-start version in HTML. Production: CDN/cache policy or hashed assets if needed. |
| `window.*` globals | Prefer one **`window.labApp`** or only **`__commandConsoleDeps`** at the boundary. |
| Huge PR | Strictly follow the migration order in §9. |

### ES module caching (why `?v=` on `main.js` is not enough)

The HTML entry can use `<script type="module" src="/static/js/main.js?v=42">`, but **nested imports have no query string**: if `main.js` does `import { x } from './state.js'`, the browser requests **`/static/js/state.js`** as-is. Browsers may cache that URL aggressively. Bumping `?v=` on `main.js` alone does **not** invalidate cached `state.js`, `api/client.js`, etc.

**Already configured in this repo (dev-friendly):** `backend/main.py` registers **`NoCacheMiddleware`**, which adds **`Cache-Control: no-store, no-cache, must-revalidate, max-age=0`** (plus **`Pragma`** / **`Expires`**) to responses whose path is **`/`**, **`/debug`**, or anything under **`/static`**. So every **`/static/js/...`** module response is told not to be cached as a long-lived asset during normal FastAPI/Uvicorn runs. You should **not** need constant hard-refresh for submodule edits while that middleware is active.

**Related:** the index route’s **`_read_index_html`** + **`_STATIC_VERSION`** only rewrite **`app.js?v=...`** in the served HTML; that helps the **legacy** entry script. Submodule freshness in development still relies on the **same** no-cache headers for `/static/...`, not on per-file query params.

**If something changes later:** if you remove that middleware, narrow it (e.g. only HTML), or put **`/static`** behind a **CDN or reverse proxy that caches aggressively**, you must either (1) restore broad no-cache (or short `max-age` + revalidation) for **`/static/js/**`** in dev, or (2) use a **bundler** that emits **content-hashed filenames** and long-cache headers for those files. Document any such change in README and here.

---

## 12. Definition of done (refactor v1)

- No single file ~2500 lines for core UI logic; largest file **under ~500–800 lines** unless justified (e.g. render).  
- **Clear `js/` map** documented in **README** (short section) + this file.  
- **Command Console** remains ES modules; its **`deps`** contract documented next to **`bootstrap`**.  
- **`index.html`** readable at a glance (structure + one module entry + optional CSS link).

---

## 13. Checklist (for implementation phase)

Use `[ ]` → `[x]` as you complete steps.

- [ ] Agree on layout §3 and state approach §5 (adjust names if needed).  
- [ ] Add `frontend/js/main.js` + `bootstrap.js` skeleton; no behavior change.  
- [ ] Extract `config` + `coordinates` (pure).  
- [ ] Extract `api/client.js`.  
- [ ] Extract `api/commands.js` + modals dependency.  
- [ ] Extract `canvas/render.js`.  
- [ ] Extract `canvas/interaction.js`.  
- [ ] Extract `ui/*` (sidebar, recipes, context, parameter modal).  
- [ ] Extract `features/*` (video, table-cam, cobyla).  
- [ ] Remove legacy `app.js` body; single module entry in `index.html`.  
- [ ] (Optional) `css/main.css`.  
- [ ] README + smoke-test list.

---

If you want changes before implementation, good knobs to tweak are: **directory names**, **whether state is one file or split**, **CSS timing**, and **bundler now vs later**.
