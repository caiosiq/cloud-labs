# Run CloudLab scripts — Python library & long experiments

This document discusses a **Python-facing layer** for **long-running lab workflows**: experiments that need **data collection**, **measurements between steps**, waits, retries, and branching—not just single-line UI commands. This track **does** involve **`lab_communicator`** and the automation stack, exposed as an **importable Python module** (or a small set of modules) that scripts can call.

**Document order (project roadmap):**  
1. **`coding_on_the_ui.md`** — browser Command Console (HTTP-only, no communicator changes).  
2. **`Run_CloudLab_Scripts.md`** (this file) — Python orchestration & communicator API.  
3. **`import_json.md`** — declarative JSON sequences (LLM lab plans, batch runner).

---

## 1. Goals

- Allow **Python scripts** (on the lab machine or an approved runner) to:
  - Invoke the **same actions** as the digital twin: move components, optimize, motors, refresh state, etc.
  - **Insert arbitrary Python** between steps: cameras, file IO, analysis, logging, conditionals.
- Provide a **stable, documented import surface** (e.g. “run this move and block until idle”) instead of every script re-implementing HTTP or poking internals.
- Support **long experiments** with clear **lifecycle**: start, step, capture data, handle failures, optional resume (future).

## 2. Non-goals (initial sketch)

- Running untrusted arbitrary code on the server without an operator—**governance** (who can run what) is a deployment concern, not fully specified here.
- Replacing `lab_automation`—this layer **composes** it via `lab_communicator` / `OpticalExperiment`, not rewrites it.

---

## 3. Relationship to existing code

Today, **`RealLabCommunicator`** wraps **`OpticalExperiment`** and implements async methods (`move_component`, `optimize_component`, …). The FastAPI app calls into the communicator from **background tasks**.

**Design fork to decide in implementation:**

| Approach | Pros | Cons |
|----------|------|------|
| **In-process:** scripts `import` a package that holds or receives a **reference to the same `LabCommunicator` / experiment** instance the server uses | Lowest latency; true blocking semantics | Same process as the API server—or a dedicated “runner” process that owns the robot |
| **HTTP client library:** Python wraps **`POST /api/command`**, `GET /api/lab-state`, etc. | Clear separation; scripts can run remotely | Polling for completion; must define “done” via lab state |

For **data collection** tied to hardware timing, **in-process** on the lab PC is often preferable; for **remote notebooks**, **HTTP** may win. The library design can expose **both** behind one façade (`LabSession` with pluggable transport).

---

## 4. Proposed module shape (illustrative, not API contract)

Names are placeholders:

- **`cloudlab_control`** (or under `backend/lab_communicator/client.py`):
  - `move_component(tag_id, x, y, rotation, ...)`
  - `optimize(tag_id, strategy, params)`
  - `refresh_state()`
  - `get_state()` → dict mirroring `get_lab_state`
  - Helpers: **`wait_until_idle()`**, **`wait_for_optimization_step(...)`** if needed

Implementation either calls **communicator methods directly** (in-process) or **HTTP** with the same JSON as the UI.

---

## 5. Long-running experiment pattern

Typical script structure:

1. Initialize session (connect / bind to communicator or API base URL).
2. Loop or graph of steps:
   - **Act** (move / optimize / motor).
   - **Wait** until `system_status` is idle (and optionally optimization step advances).
   - **Measure** (user Python: save image, read sensor, write HDF5/CSV).
3. **Log** metadata (timestamp, recipe id, git hash) for reproducibility.

**Concurrency:** the FastAPI server may already run moves in **background tasks**. Scripts must respect **BUSY / OPTIMIZING** and not issue conflicting commands—or the library serializes and queues (explicit policy to define).

---

## 6. Data collection & “between steps”

Unlike the Command Console, Python can:

- Pull from **table cameras**, **optimization image folders**, or external instruments.
- Block on **file watchers** or **timeouts**.
- Branch: “if metric &lt; threshold, run COBYLA again.”

The library should **not** try to implement every instrument—only **hooks** and **state queries** so user code stays readable.

---

## 7. Roadmap (outline)

Detailed tasks with checkboxes live in **Roadmap (phased checklist)** at the end of this file.

---

## 8. Connection to `import_json.md`

Batch **declarative** runs (JSON list of steps) can be executed by a **thin runner** that maps each step to the same Python API defined here—or by translating JSON to HTTP if we stay HTTP-only. See **`import_json.md`** for LLM-generated plans and schema; this document owns **how those steps execute inside Python**.

---

## 9. Open questions

- **Single owner of the robot:** Should long scripts **stop the FastAPI server** from accepting conflicting UI commands, or is shared access with last-writer-wins acceptable?
- **Process model:** dedicated **script runner** subprocess vs in-server **admin endpoint** that runs a job—security and ops tradeoffs.
- **Async:** align with `asyncio` if the communicator stays async-friendly for callers.

---

## 10. Summary

| Topic | Direction |
|--------|-----------|
| Primary use | **Python scripts**, long runs, **measurements between steps**. |
| `lab_communicator` | **Yes** — expose actions through a **proper importable module** (and/or HTTP wrapper). |
| UI doc | **`coding_on_the_ui.md`** remains HTTP-only; this doc extends **off-browser** automation. |
| Next | **`import_json.md`** — JSON sequences and LLM plans feeding the same actions. |

---

## Roadmap (phased checklist)

### Phase 1 — Spike & transport choice

- [ ] Document decision: in-process communicator access vs HTTP client (or both behind one façade).
- [ ] Spike script: one move (or noop-safe call) via chosen transport; print lab state before/after.
- [ ] Spike: same action via alternate transport for comparison (optional second spike).

### Phase 2 — Minimal Python API

- [ ] Package or module layout (import path, `__init__.py`, naming per repo convention).
- [ ] `move_component` / `optimize` / `refresh_state` / `get_state` aligned with `LabCommunicator` + `/api/command` semantics.
- [ ] `wait_until_idle()` (or equivalent) polling `system_status` until `IDLE` (define timeouts and errors).

### Phase 3 — Long-run ergonomics

- [ ] Example: loop with **measure** step between commands (log path, timestamp).
- [ ] Example: wait for optimization step / image folder if needed by your workflow.
- [ ] Clear errors: robot failures, **409** busy, disconnect.

### Phase 4 — Hardening & docs

- [ ] Typing and docstrings for public functions.
- [ ] README section or standalone doc: install, env vars, running next to FastAPI vs standalone HTTP.
- [ ] Decide and document concurrency policy (shared robot with UI vs exclusive mode).

### Phase 5 — Optional advanced

- [ ] `asyncio` integration if callers need async.
- [ ] Dedicated “runner” process or job API if security/ops requires it.
