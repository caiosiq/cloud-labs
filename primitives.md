# Primitives and HTTP (quick reference)

Implementation detail lives in **`backend/lab_primitives/README.md`** and **`backend/lab_primitives/ROADMAP.md`**. The **return vs observe** split for measurables is described in **`model.md`**.

---

## Read primitives (no `POST /api/command`)

| Primitive / route | Behavior |
|-------------------|----------|
| **`GET_TUNABLES`** → `GET /api/components/{tag_id}/tunables` | **`return_tunables_for_tag`** — saved intent only. |
| **`GET_MEASURABLES`** → `GET /api/components/{tag_id}/measurables` | **`return_measurables_for_tag`** — saved lab-reported slice only. |

These go through **`lab_primitives.fetch_read_primitive`**.

---

## Observe (poll / refresh measurables)

| Primitive / route | Behavior |
|-------------------|----------|
| **`OBSERVE_MEASURABLES`** | **`observe_measurables_for_tag`** — may capture (e.g. **`OPTICAL_CAMERA`** → table cam) and write **`measurables.camera_image`**. |
| `POST /api/components/{tag_id}/measurables/observe` | Same as above; response includes **`{ "status": "ok", "measurables": { ... } }`**. |
| `POST /api/command` with `"action": "OBSERVE_MEASURABLES"` | Awaited inline; returns **`measurables`** in the JSON body (not background-queued). |

---

## Command actions (`POST /api/command`)

Validated by **`lab_primitives`** (`schemas.py`). Examples: **`MOVE_COMPONENT`**, **`MOVE_MOTOR`**, **`MOTOR_SEND_HOME`** (macro), **`MOTOR_SET_ZERO`**, **`OPTIMIZE`**, **`STORE_COMPONENT`**, **`PLACE_FROM_STORAGE`**, **`AFFIRM_PLACED_AT_CURRENT`**, **`REPACK_STORAGE`**, **`RECENTER_IN_STORAGE`**, **`SCAN`**, **`REMOVE`**, **`OBSERVE_MEASURABLES`**.

See **`backend/lab_primitives/registry.py`** for the full list and handler names.

---

## Related

- **`model.md`** — vocabulary for **saved state** vs **observation**.
- **`backend/lab_model/README.md`** — field shapes for tunables/measurables, including **`camera_image`**.
