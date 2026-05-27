# Import JSON — declarative lab sequences & LLM plans

This document discusses **importing sequences of actions** (e.g. as **JSON**) so experiments can be defined **declaratively** and executed through the same **CloudLab / digital-twin actions** (`MOVE_COMPONENT`, `OPTIMIZE`, `MOVE_MOTOR`, refresh, …). The motivating use case is **LLM-generated lab plans**: a model outputs a structured list of steps; a runner validates and executes them with **minimal manual transcription**.

**Document order (project roadmap):**  
1. **`coding_on_the_ui.md`** — interactive Command Console (shorthand, single lines).  
2. **`Run_CloudLab_Scripts.md`** — Python library, long runs, data collection, `lab_communicator` access.  
3. **`import_json.md`** (this file) — **batch JSON** / imported sequences / LLM integration.

---

## 1. Goals

- Represent a **recipe-like but machine-first** sequence: ordered steps with `action`, `target_id`, `parameters`—aligned with existing backend payloads (`POST /api/command` body shape).
- Support **import** from file, paste, or API upload—exact UX TBD.
- Enable **LLM-generated plans** to be **validated** (schema, component IDs, numeric ranges) before execution.
- Reuse the **same execution semantics** as the UI and Console: busy checks, optional collision policy for moves (match GUI unless `--force` exists elsewhere).

## 2. Non-goals (initial)

- Letting an LLM **directly** call the robot with no human review—**human approval** or **dry-run** mode should be a product decision.
- A full workflow language (loops, variables) in JSON—could be **phase 2** or delegated to **Python** (`Run_CloudLab_Scripts.md`).

---

## 3. Why JSON (and not only Python)

| Approach | Best for |
|----------|----------|
| **JSON sequence** | Interchange with **LLMs**, web tools, version control, static review |
| **Python scripts** | **Branching**, **loops**, **custom measurements** between steps |

JSON is a **lingua franca** for “plan as data”; Python remains the escape hatch for complexity. The two connect: a **JSON runner** can be a **small Python script** or a **FastAPI endpoint** that iterates and calls the same primitives as **`Run_CloudLab_Scripts.md`**.

---

## 4. Proposed document shape (illustrative schema)

Conceptually aligned with existing recipe / command objects in the backend (see `RecipeStep` in `main.py` and frontend recipe recording):

```json
{
  "version": 1,
  "name": "alignment-run-2025-03-25",
  "steps": [
    {
      "step": 1,
      "action": "MOVE_COMPONENT",
      "component": "tag_22",
      "parameters": { "target_x": 280, "target_y": -50, "rotation": 0 }
    },
    {
      "step": 2,
      "action": "OPTIMIZE",
      "component": "tag_22",
      "parameters": { "strategy": "NEWTON", "camera_number": 1, "axis": "x" }
    }
  ]
}
```

Field names (`component` vs `target_id`) should be **normalized** to whatever the API already accepts—implementation may **alias** for LLM convenience.

**Validation layer (required):**

- **Schema** (JSON Schema or Pydantic): required keys per `action`.
- **Semantic checks:** `tag_*` exists in catalog or current lab state; numeric bounds; strategy-specific params.

---

## 5. Execution surfaces (options)

| Surface | Description |
|---------|-------------|
| **Backend endpoint** | `POST /api/plan/run` with JSON body—server steps through commands (with auth later). |
| **Python CLI** | `python -m cloudlab_runner plan.json` using the **library from `Run_CloudLab_Scripts.md`**. |
| **UI** | “Import plan” button uploads file, shows preview, user clicks Run—internally same as recipe play with extra validation. |

All should map each step to **`lab.move_*`** or **`POST /api/command`**—**no duplicate robot logic**.

---

## 6. LLM integration workflow (recommended)

1. **System prompt / tool schema** exposes allowed `action` values and parameter shapes (from **`import_json.md`** schema + catalog).
2. Model outputs **JSON only** (or JSON in a fenced block) for easy extraction.
3. **Validator** rejects or repairs invalid steps; **human reviews** diff.
4. Runner executes **step-by-step** with **logging**; optional **pause after each step** for approval.

---

## 7. Relationship to recipes in the UI

The frontend already records **recipe steps** during “Recording.” Imported JSON is **conceptually similar** but:

- **Machine-oriented** (strict schema, LLM-friendly).
- May include **metadata** (version, author, experiment id).

Unifying **“recipe”** and **“imported plan”** in one internal model is a possible consolidation—implementation detail.

---

## 8. Roadmap (outline)

Detailed tasks with checkboxes live in **Roadmap (phased checklist)** at the end of this file.

---

## 9. Open questions

- **Idempotency:** should re-running the same plan detect “already at target” and skip?
- **Partial failure:** abort entire plan vs mark step failed and stop.
- **Refresh / SCAN** steps: encode as explicit `REFRESH_STATE` action in JSON vs reuse recipe conventions.

---

## 10. Summary

| Topic | Direction |
|--------|-----------|
| Purpose | **Declarative sequences** for **batch** and **LLM** workflows. |
| Format | **JSON** (versioned schema), aligned with `/api/command` payloads. |
| Execution | Shared primitives with UI / Console / **Python library**. |
| Depends on | **`Run_CloudLab_Scripts.md`** for how steps map to `lab_communicator`; can start with **HTTP-only** runner if needed. |

This closes the trilogy: **interactive (Console)** → **programmatic Python (long experiments)** → **declarative JSON (LLM & batch plans)**.

---

## Roadmap (phased checklist)

### Phase 1 — Schema & validation

- [ ] Freeze **v1** JSON schema: `version`, `name`, `steps[]` with `step`, `action`, `component` / `target_id` (pick one canonical field + optional aliases).
- [ ] Document mapping to existing `POST /api/command` body (same `parameters` per action).
- [ ] Validator: JSON Schema or Pydantic; reject unknown `action` values and missing required params.
- [ ] Semantic checks: `tag_*` exists in catalog or current lab state; numeric bounds where feasible.

### Phase 2 — Runner (execution engine)

- [ ] Step runner: for each step, dispatch to HTTP (`/api/command`) and/or Python API from **`Run_CloudLab_Scripts.md`**.
- [ ] Block until idle between steps (`wait_until_idle` or poll `GET /api/lab-state`).
- [ ] Logging: step index, action, timestamps, errors; optional dry-run mode (validate only, no POST).

### Phase 3 — Operator workflow

- [ ] CLI entrypoint, e.g. `python -m … plan.json` (exact module TBD).
- [ ] Human-in-the-loop: optional **pause after each step** or **single approval** before run.
- [ ] Define behavior on partial failure (abort all vs stop and report).

### Phase 4 — UI (optional)

- [ ] “Import plan” file upload + **preview** (validated steps list) + Run.
- [ ] Reuse recipe playback UX where possible; avoid duplicating robot logic.

### Phase 5 — LLM & examples

- [ ] Short **system / tool** snippet for LLMs: allowed actions and parameter shapes (+ link to catalog).
- [ ] One or two **example plans** in-repo under `schemas/` or `examples/`.
- [ ] Document idempotency / re-run policy once product decision is made.

### Phase 6 — API (optional)

- [ ] `POST /api/plan/run` or similar: authenticated batch run on server (only if product needs it).
