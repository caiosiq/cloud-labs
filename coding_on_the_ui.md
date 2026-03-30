# Command Console — design & roadmap (UI)

This document plans the **Command Console**: a text-driven area on the lab UI where operators type **single-line commands** instead of (or in addition to) dragging and using the context panel. Implementation stays **on the frontend** and reuses **existing HTTP APIs**—**no changes to `lab_communicator`** for this track unless we discover a hard gap (none anticipated for v1).

**Document order (project roadmap):**  
1. **`coding_on_the_ui.md`** (this file) — browser Command Console.  
2. **`Run_CloudLab_Scripts.md`** — Python library & long-running orchestration.  
3. **`import_json.md`** — declarative JSON sequences (e.g. LLM-generated plans).

---

## 1. Goals

- Let power users run the **same logical operations** as the GUI (move, optimize, motors, refresh, etc.) by **typing readable shorthand**, not raw JSON.
- Keep **one source of truth** for what reaches the backend: the console produces the **same structured command** the rest of the app already sends, routed through **`executeSendCommand`**.
- Avoid bloating `index.html` / `app.js`: **new UI in a small, optional surface** with **new code in dedicated ES modules**.

## 2. Non-goals (initially)

- A new backend scripting language, sandboxed VM, or server-side REPL.
- Replacing the canvas or recipes; the console is an **alternate input path**.
- Mandatory refactor of all of `app.js` in the first iteration (optional follow-up).

---

## 3. Why UI-only is enough

Today the backend already exposes lab actions:

| Intent | Mechanism |
|--------|-----------|
| Move / optimize / motor | `POST /api/command` with `action`, `target_id`, `parameters` (see `backend/main.py` → `receive_command`). |
| Rescan / refresh lab state | `POST /api/lab-state/refresh` (Refresh button in `app.js`). |

The frontend wraps moves with **`sendCommand` → `executeSendCommand`** (confirmation for `MOVE_COMPONENT`, recipe recording, `409 BUSY`, etc.). The Command Console must **call that same path** so behavior matches the GUI.

**Note:** `action == "SCAN"` in `/api/command` may not mirror full refresh behavior; for “scan/rescan” from the console, use **`POST /api/lab-state/refresh`** until naming is unified in the API.

---

## 4. Command vocabulary (canonical payload)

Everything the parser eventually emits must match what the backend expects:

- **`MOVE_COMPONENT`** — `target_id`, `parameters`: `target_x`, `target_y`, `rotation` (lab mm / deg as today).
- **`MOVE_MOTOR`** — `parameters`: `motor_id`, `distance`.
- **`OPTIMIZE`** — `parameters`: `strategy` (`NEWTON` / `COBYLA`), plus strategy fields as the context panel builds.
- **Refresh** — `POST /api/lab-state/refresh` (dedicated verb in shorthand, e.g. `refresh`).

Optional later: parity with every other button (state load/save, Cobyla reference, …).

---

## 5. UX concept (avoid overwhelming the main UI)

- **Collapsible Command Console** (bottom or right), or a **tab / toggle**—default **collapsed**.
- **Single-line input + history** (up/down); multiline scripts are out of scope here (see **`import_json.md`** for batch sequences).
- **Output strip**: echo parsed intent, success/error, HTTP `detail` on failure (especially `409` busy).
- **`help` / `?`**: allowed verbs and one example each, generated from a single **command registry** in code.

**Naming in the UI:** use **“Command Console”**—not “Terminal” (suggests shell/system access) or “Script” (suggests multi-line logic). “Console” matches **single-line, immediate execution**.

---

## 6. Modularity plan (frontend)

**Principle:** New behavior in **new modules**; `app.js` only **imports / initializes** the console (minimal glue).

**Chosen integration:** **ES modules** (`<script type="module">`) for new code so the global scope stays clean. `app.js` may remain a classic script that sets up shared state; the console module **imports** what it needs via a thin bootstrap pattern (exact pattern decided at implementation time—e.g. init function receiving `executeSendCommand`, `log`, `checkCollision`).

Suggested layout:

```
frontend/
  index.html              # container + script type="module" entry for console
  app.js                  # existing; export or attach hooks consumed by console bootstrap
  js/
    command-console.js    # UI: open/close, input, history, output log
    command-parse.js      # shorthand string → canonical command object
    command-api.js        # dispatch to executeSendCommand + refresh fetch
```

- **`command-parse.js`** — pure functions (split / regex / small grammar); easy to test.
- **`command-console.js`** — DOM only.
- **`command-api.js`** — no duplicated `fetch('/api/command')` except where unavoidable; prefer **`executeSendCommand`**.

---

## 7. Input language — roadmap

### Primary path (v1): **shorthand from day one**

Raw JSON lines are **not** the default end-user experience—typing `{"action":"MOVE_COMPONENT",...}` is error-prone and hostile to humans.

**Recommendation:** Implement **`command-parse.js` immediately** with a tiny shorthand grammar, e.g.:

- `move tag_22 280 -50 0` → `MOVE_COMPONENT` with those `parameters`.
- `motor tag_22 1 0.5` → `MOVE_MOTOR`.
- `optimize tag_22 NEWTON …` → strategy-specific tokens as needed.
- `refresh` → lab state refresh endpoint.

Implementation is intentionally small (~order of tens of lines: trim, split on whitespace, switch on first token, validate arity). Map shorthand → **one canonical object** → **`executeSendCommand`**.

### Optional developer escape hatch

If needed for debugging, a **prefix** such as `json ` followed by a single-line JSON object can be supported later for copy-paste from DevTools—**not** the primary UX.

### Phase 2 — Quality of life

- **History** (session-local `sessionStorage` optional).
- **Tab completion** for `tag_*` from the latest `labState` snapshot (read-only).

### Phase 3 — Optional larger refactor

- Split `app.js` into smaller files **after** the console ships, if maintainability demands it.

---

## 8. Safety & parity with the GUI

- **MOVE_COMPONENT** must go through **`sendCommand`** so confirmation modal and ghost revert on cancel match the GUI.
- **Collision policy:** the console **always mirrors the GUI by default**—call the same **`checkCollision`** (or equivalent) before dispatch; **reject** with a clear **error string** if a move would collide. A **`--force`**-style escape (or separate verb) is **explicitly deferred** until power users ask for it.
- Respect **409** (BUSY / OPTIMIZING): show message; default **no queue**, fail fast.
- **Recipe recording:** when the UI is in **recording** mode, console-issued commands **must** append to the recipe. Routing through **`executeSendCommand`** should achieve this **automatically** (same path as button-driven commands)—verify in QA.

---

## 9. Implementation checklist (refined)

1. Expose **`executeSendCommand`**, **`sendCommand`** (if needed for confirmations), **`checkCollision`**, and **`log`** for use from a **module**—smallest change to `app.js` (export from a tiny `app-core.js` or pass into `initCommandConsole(...)`).
2. Add **`coding_on_the_ui.md`-aligned** ES module entry in `index.html` + **collapsed Command Console** shell + styles scoped under `.command-console`.
3. Implement **`command-parse.js`** with **shorthand first** (move / motor / optimize / refresh / help).
4. Wire **`command-api.js`** to **`executeSendCommand`** and **`POST /api/lab-state/refresh`** for `refresh`.
5. **`help`** driven by a single **command registry** (verb → args → example).
6. Output log + **409** / collision / parse errors surfaced clearly.
7. Optional: `json …` one-liner for developers only.

---

## 10. Decisions (resolved)

| Topic | Decision |
|--------|----------|
| Collision | **Mirror GUI**; reject with error; **`--force` later** if needed. |
| Recipe recording | **Yes** when recording—via **`executeSendCommand`**. |
| UI name | **Command Console**. |
| Module system | **ES modules** for new frontend code. |
| Default input | **Shorthand**, not raw JSON. |

---

## 11. Summary

| Topic | Decision |
|--------|----------|
| Backend / `lab_communicator` | **No change** for v1; use `/api/command` and `/api/lab-state/refresh`. |
| Source of truth | Same payloads as **`executeSendCommand`**. |
| Structure | **`frontend/js/`** ES modules; slim hooks from `app.js`. |
| Rollout | **Shorthand parser (v1)** → QoL → optional `json` escape → optional `app.js` split. |

**Next documents:** after this UI track, see **`Run_CloudLab_Scripts.md`** (Python orchestration) and **`import_json.md`** (batch / LLM-friendly sequences).

---

## Roadmap (phased checklist)

### Phase 1 — Hooks & module wiring

- [ ] Expose `executeSendCommand`, `sendCommand` (if needed), `checkCollision`, and `log` for ES module consumption (export or `initCommandConsole(deps)` pattern).
- [ ] Add `<script type="module">` entry in `index.html` that loads the console bootstrap after core app setup.

### Phase 2 — Command Console shell

- [ ] Collapsible **Command Console** region (default collapsed) with label **Command Console**.
- [ ] Scoped styles under `.command-console` to avoid bleeding into the rest of the UI.
- [ ] Single-line input and a scrollable output / log strip.

### Phase 3 — Shorthand parser (`command-parse.js`)

- [ ] `move <tag_id> <x> <y> <rotation>` → `MOVE_COMPONENT` payload (lab mm / deg).
- [ ] `motor <tag_id> <motor_id> <distance>` → `MOVE_MOTOR`.
- [ ] `optimize <tag_id> <strategy> …` → `OPTIMIZE` with required strategy parameters (document minimal v1 subset).
- [ ] `refresh` → `POST /api/lab-state/refresh` (no raw `SCAN` unless unified with backend).
- [ ] `help` / `?` → print usage from a single command registry object.
- [ ] Parse errors: clear, single-line messages (unknown verb, wrong arity, bad numbers).

### Phase 4 — Dispatch (`command-api.js`)

- [ ] Route parsed commands through `executeSendCommand` (not a duplicate `fetch` to `/api/command` except where unavoidable).
- [ ] `refresh` wired to the same refresh flow as the Refresh button.
- [ ] **Collision:** run the same `checkCollision` as the context panel before `MOVE_COMPONENT`; reject with error string on clash.
- [ ] Surface **409** (BUSY / OPTIMIZING) and HTTP error bodies in the output log.

### Phase 5 — Parity & polish

- [ ] Confirm recipe recording: with recording on, console commands append steps via `executeSendCommand` (QA pass).
- [ ] Keyboard history (e.g. up/down) for the input line.
- [ ] Optional: `json …` single-line escape for developer copy-paste from DevTools.

### Phase 6 — Quality of life (later)

- [ ] Session history in `sessionStorage` (optional).
- [ ] Tab completion for `tag_*` from latest lab state snapshot (read-only).

### Phase 7 — Optional refactor (only if needed)

- [ ] Split large sections of `app.js` into smaller modules after the console is stable.
