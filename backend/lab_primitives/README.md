# Lab primitives

This package is the **single place** for lab **primitive identifiers**, **Pydantic validation** of command bodies, a **registry** (metadata + `LabCommunicator` handler names), and **dispatch** helpers used by the FastAPI app and recipe runner.

Long-form design notes live in [`../../primitives.md`](../../primitives.md). **Planned work:** [`ROADMAP.md`](ROADMAP.md).

---

## Current status (what actually runs)

**Yes, this package is on the hot path:**

| Call site | What runs |
|-----------|-----------|
| **`main.py`** `POST /api/command` | `parse_command_payload` → `schedule_validated_command` → `execute_validated_command` → atomic `lab.*` calls (see macros below). **`OBSERVE_MEASURABLES`** is awaited inline and returns fresh **`measurables`**. |
| **`main.py`** GET tunables/measurables | `fetch_read_primitive` → `return_tunables_for_tag` / `return_measurables_for_tag` (saved state only) |
| **`main.py`** `POST /api/components/{tag_id}/measurables/observe` | `observe_measurables_for_tag` then same slice as GET measurables |
| **`execute_recipe`** | same parse + **await** `execute_validated_command` |

### Macros (implemented)

**`MOTOR_SEND_HOME`** (`PrimitiveKind.MACRO`): reads cumulative angle via **`lab_model.motor_rotation_store.get_angle`**, then **`await`s** the atomic **`MOVE_MOTOR`** path with `distance = -angle`. It does **not** call `LabCommunicator.motor_send_home` from dispatch (that method remains on the communicator for **direct** callers / parity tests).

**`PRIMITIVE_REGISTRY`** lists **`macro_expands_to`** for macros. **`MACRO_PRIMITIVE_IDS`** in **`ids.py`** mirrors ids implemented as composition in **`dispatch.py`**.

**Atomic** commands still map to **one** `LabCommunicator` method inside **`_invoke_atomic`**.

---

## What it is (and is not)

| Responsibility | Here? |
|----------------|--------|
| Enum of primitive ids, kind (`ATOMIC` / `MACRO`), read-only flag | **Yes** — `ids.py`, `registry.py` |
| Validate `POST /api/command` JSON (and recipe-shaped dicts) | **Yes** — `schemas.py` + `parse_command_payload` |
| Schedule async work on `LabCommunicator` (HTTP) or await it (recipes) | **Yes** — `execute_validated_command`, `schedule_validated_command` |
| Read tunables/measurables for one tag (GET routes) | **Yes** — `fetch_read_primitive` + `TagQuery` |
| Implement moves, optimization, mock vs real hardware | **No** — that is **`lab_communicator`** |

Domain shapes for components (tunables/measurables, storage geometry) are in [**`../lab_model/`**](../lab_model/README.md).

---

## Modules

| File | Role |
|------|------|
| **`ids.py`** | `PrimitiveId`, `PrimitiveKind`, `READ_PRIMITIVE_IDS`, `MACRO_PRIMITIVE_IDS`. |
| **`schemas.py`** | Discriminated Pydantic models for each **`action`** in a command body (`MoveComponentBody`, `OptimizeBody`, …), shared pieces (`PoseTargetParameters`, `TagQuery`), and `COMMAND_ADAPTER`. |
| **`registry.py`** | `PRIMITIVE_REGISTRY`: per id → `kind`, `read_only`, `handler` (method name on `LabCommunicator`), optional `http` hint for read primitives. |
| **`dispatch.py`** | `parse_command_payload`, `fetch_read_primitive`, `execute_validated_command`, `schedule_validated_command`, `validation_error_detail`; structured logs per primitive step. |
| **`protocol.py`** | `LabPrimitiveSurface` `Protocol` for fakes / drift checks. |
| **`__init__.py`** | Public exports for `main.py` and tests. |

---

## Two ways primitives run

### 1. Commands — `POST /api/command`

Body shape:

```json
{
  "action": "MOVE_COMPONENT",
  "target_id": "TAG",
  "parameters": { "target_x": 0, "target_y": 0, "rotation": 0 }
}
```

Flow:

1. **`parse_command_payload(dict)`** validates with the discriminated union (Pydantic v2).  
   - Recipe steps may use **`"action": "PLACE"`**; it is normalized to **`MOVE_COMPONENT`** before validation.
2. **`schedule_validated_command(lab, cmd, background_tasks)`** queues **`execute_validated_command`** on Starlette’s background tasks and returns the usual `{"status": "accepted", "message": "..."}` JSON.

**`main.py`** handles lab busy (`409`) before parsing; **`422`/`400`** on validation uses `validation_error_detail`.

### 2. Read primitives — GET routes

Not part of the POST union. **`GET /api/components/{tag_id}/tunables`** and **`.../measurables`** call:

```text
fetch_read_primitive(lab, PrimitiveId.GET_TUNABLES | GET_MEASURABLES, tag_id)
```

which validates **`tag_id`** with **`TagQuery`** and calls **`get_tunables_for_tag` / `get_measurables_for_tag`** on the communicator.

---

## Recipes

The recipe executor builds the same envelope as HTTP (`action`, `target_id`, `parameters`) per step, runs **`parse_command_payload`**, then **`await execute_validated_command(lab, cmd)`** (no background queue), then the existing delay between steps.

---

## Registry vs schemas

- **`PRIMITIVE_REGISTRY`**: ids, **`kind`** (`ATOMIC` / `MACRO`), **`read_only`**, **handler**, optional **`macro_expands_to`**, optional **`http`** for GET reads. Dispatch implements **`MACRO`** rows in code (see *Macros* above); **`kind`** is also documentation.
- **Actual argument lists and types** are enforced by the **Pydantic models** in **`schemas.py`**, not by a second validation layer in `main.py`.

---

## Summary

| Export / symbol | Use |
|-----------------|-----|
| `parse_command_payload` | Validate incoming command dict → typed body |
| `schedule_validated_command` | HTTP: background + response message |
| `execute_validated_command` | Recipes: await one command |
| `fetch_read_primitive` | GET tunables/measurables |
| `PRIMITIVE_REGISTRY`, `PrimitiveId`, `PrimitiveKind`, `MACRO_PRIMITIVE_IDS` | Docs, tooling, future tests |

For the conceptual inventory (pick/place family, hover gap, etc.), see [`../../primitives.md`](../../primitives.md).
