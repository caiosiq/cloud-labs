# Lab model: tunables vs measurables

This folder is the **`lab_model`** Python package: shared domain logic for the digital twin (not hardware I/O). It explains how **tunables** and **measurables** describe each optical component.

**Related:** [ **`model.md`** ](../../model.md) (saved state vs **observe**), [ **`primitives.md`** ](../../primitives.md) (HTTP vocabulary), [ **`schemas/README.md`** ](../../schemas/README.md) (example **`camera_image`** shape).

---

## 1. What `lab_model` is (and is not)

**`lab_model`** holds **shared domain logic** that does not talk to hardware by itself:

| Module | Role |
|--------|------|
| **`component_model.py`** | Shape of per-component **tunables** vs **measurables**, presence (breadboard / storage / off table), helpers to read and update those dicts. |
| **`storage_region.py`** | Geometry for **inventory quadrant Q3** (negative **x** and negative **y** in lab mm, origin at table center): grid cells, “does this pose fit this slot?”, layout conflict analysis, random placement helpers. Uses **measurables.pose** for “where the part is” and **tunables.storage** for **intent** (slot). |
| **`motor_rotation_store.py`** | **Software-tracked** cumulative motor angles (per tag, per motor id), persisted as ``motor_rotations.json`` inside the ``LAB_VIEW_PATH`` bundle. |

It is **not** the place for mock vs real lab I/O—that lives in **`lab_communicator`** (`mock.py` / `real.py`). Communicators **import** `lab_model` to stay consistent when they update JSON state, run storage checks, or merge motor angles into lab state.

**Related:** command naming and HTTP dispatch are documented in [`../../primitives.md`](../../primitives.md). Read primitives **`GET_TUNABLES` / `GET_MEASURABLES`** return **slices** of the same component entry described here.

---

## 2. Where tunables and measurables live

Each component in lab state is a JSON-shaped object (see e.g. ``lab_view/lab_state.json`` in the mock bundle). Conceptually:

```text
components[tag_id] = {
  "id": "...",
  "type": "...",
  "tunables":   { ... },   ← what we command / intend
  "measurables": { ... }    ← what we observe / report
}
```

The split is **intentional**: it keeps **commands and intent** separate from **reported reality** (vision, optimization results, last known pose), so the UI and recipes can show both “what we asked for” and “what the system thinks is true.”

---

## 3. Tunables (commanded intent)

**Tunables** answer: *What should the system believe we want for this part?*

Defaults (see `default_tunables()` in `component_model.py`) include:

| Area | Meaning |
|------|--------|
| **`presence`** | High-level location: `breadboard`, `storage`, or `off_table`. |
| **`nominal_pose`** | Intended pose in lab frame: `x`, `y`, `rotation` (mm / degrees). Often updated when you command a move or store. |
| **`nominal_motor_positions`** | Map of motor id → commanded or nominal angle (when used). |
| **`storage`** | `in_storage` flag and optional **`slot`** `{ "i", "j" }` for the inventory grid in Q3. |
| **`placement`** | e.g. **`mode`**: `MANUAL`, or a strategy name after optimization—how placement was decided. |

Helpers like `presence_of`, `nominal_pose`, `storage_slot`, and `set_presence_and_storage` keep reads and updates consistent so **`storage_region`** and communicators do not duplicate string keys.

---

## 4. Measurables (lab-reported state)

**Measurables** answer: *What does the lab / mock actually report back?*

Defaults (`default_measurables()`) include:

| Field | Meaning |
|-------|--------|
| **`pose`** | Measured center pose: `x`, `y`, `rotation`. Used for drawing, collision-ish checks, and storage validation. |
| **`last_optimization_score`** | Scalar feedback from the last run, when applicable. |
| **`last_optimized_pose`** | Snapshot of pose after optimization, when applicable. |
| **`camera_image`** | After **record** (`RECORD_MEASURABLES` / `POST .../measurables/record`), mock/real may set an object such as `{ "path", "source", "cam_id", "format" }` (PNG path on disk). Otherwise **`null`**. See **`schemas/README.md`**. |

**Important distinction:** For layout and “where is the part on the table,” **`storage_region`** treats **`measurables.pose`** as the physical center (e.g. fitting a footprint inside a storage cell). **Tunables** carry **nominal** pose and **storage intent** (including slot), which can differ from measured pose when vision lags or the mock adds noise.

---

## 5. How they work together in practice

1. **Move / store / place-from-storage** (via `lab_primitives` and `LabCommunicator`) update both sides: e.g. after a successful move, **nominal** pose in tunables and **measured** pose in measurables are brought in line (mock may add small noise on measurables only).

2. **Optimization (`OPTIMIZE`)** typically adjusts what is “known” about the part in **measurables** (pose, scores) and may set **tunables.placement.mode** to the strategy name—again separating **reported outcome** from **intent**.

3. **Storage Q3 rules** (`storage_region.py`): if a part is **STORED**, **tunables** say *which cell* we intend (`storage.slot`) and **presence**; **measurables.pose** is checked against that cell’s geometry for “fits / doesn’t fit” style diagnostics.

4. **Motor angles** in full lab state may combine **hardware or mock behavior** with **`motor_rotation_store`**: cumulative angles are **tracked in software** from commanded moves unless true encoder readback is modeled elsewhere—so they behave more like **derived state** than raw sensor streams. They are not the same conceptual bucket as `measurables.pose`, but they sit alongside component state when the communicator merges them into the JSON the UI polls.

---

## 6. API surface (read slices vs record)

Without a motion command, the backend exposes:

- **`GET /api/components/{tag_id}/tunables`** → **`return_tunables_for_tag`** (same as legacy **`get_tunables_for_tag`**) → **`tunables`** dict.
- **`GET /api/components/{tag_id}/measurables`** → **`return_measurables_for_tag`** → **`measurables`** dict (saved state only).

To **record** a fresh measurement on the lab (e.g. camera capture into **`camera_image`**):

- **`POST /api/components/{tag_id}/measurables/record`** or **`POST /api/command`** with **`"action": "RECORD_MEASURABLES"`**.

Those reads use **`lab_primitives.fetch_read_primitive`** for **`GET_TUNABLES`** / **`GET_MEASURABLES`**; record is **`PrimitiveId.RECORD_MEASURABLES`** (see [`../../primitives.md`](../../primitives.md)). Full state: **`GET /api/lab-state`**.

---

## 7. Summary

| Concept | One-line |
|--------|-----------|
| **`lab_model`** | Domain helpers: component **tunables/measurables**, **Q3 storage geometry**, **motor angle files**—no direct hardware. |
| **Tunables** | What we **command or intend** (presence, nominal pose, storage slot, placement mode). |
| **Measurables** | What we **observe or report** (measured pose, optimization outputs, etc.). |
| **Why split** | Clear separation between **intent** and **reality** for UI, recipes, and layout checks. |

For JSON examples and migration history, see the repo’s **`schemas/`** directory at the project root and any project notes you keep alongside this package.
