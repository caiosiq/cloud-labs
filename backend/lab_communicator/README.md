# Lab communicator — adapters behind every primitive

This package sits **between** the HTTP-facing vocabulary (`lab_primitives`) and a concrete lab (mock simulator, physical bench, future simulator). Each backend is a **folder** (`mock/`, `real/`, or one you add) that subclasses `LabCommunicator` from `base.py`.

**Related:** [`../lab_primitives/README.md`](../lab_primitives/README.md) (commands that call into here), [`../lab_model/README.md`](../lab_model/README.md) (tunables / measurables shapes).

---

## 0. Rule that applies to every communicator: **`LAB_VIEW_PATH`**

Before **any** communicator runs, `backend/main.py` calls `bootstrap_lab_view()` (`shared/lab_view_config.py`). That reads **`LAB_VIEW_PATH`** from the environment (repo-relative or absolute), validates files, configures storage geometry from **`layout.json`**, and exposes paths for lasers, catalogs, recipes, saved UI states, camera captures, motor rotations, etc.

So a communicator implementation **never** hardcodes catalog paths or repo-root `schemas/` folders. It pulls bundle paths via `get_lab_view_paths()` when it needs disk locations (see `mock/communicator.py`, `real/communicator.py`).

### 0.1 Files that **must** exist in every lab bundle

These are enforced at process startup (`bootstrap_lab_view`). If any are missing, the server exits with an explicit message:

| File | Role |
|------|------|
| **`layout.json`** | Lab bounds, danger zone, storage grid (`negative_xy`), breadboard spacing — feeds `lab_model.storage_region`. |
| **`laser_lines.json`** | Laser overlays (`GET /api/laser-line`, `/api/laser-lines`). |
| **`component_library.json`** | Full parts catalog keyed by `tag_id`. |
| **`active_catalog.json`** | `{ "tag_ids": [...] }` — intersection + order for `GET /api/catalog` (`catalog_bundle.py`). |
| **`motor_rotations.json`** | Software-tracked motor angles (may start as `{}`). |

### 0.2 Created automatically if absent

| Path | Role |
|------|------|
| **`stored_intent.json`** | Written by `bootstrap_lab_view` when missing (storage-slot intent manifest). |
| **`recipes/`**, **`states/`**, **`camera_captures/`** | Directories ensured at bootstrap (recipes, UI snapshots, table-cam PNG paths). |

### 0.3 Strongly recommended for each deployment

| File | Role |
|------|------|
| **`lab_state.json`** | Authoritative snapshot for **mock** (and useful seed for placeholders). Real builds live state from hardware but still benefits from a known-good JSON for tests. |

Bundled references:

- Mock demo bundle: `backend/lab_communicator/mock/lab_view/`
- Real starter + migrated snapshots: `backend/lab_communicator/real/lab_view/default/`

---

## 1. Quick start: scaffold a new backend

From the **repository root**:

```bash
python scripts/create_lab_communicator.py my_backend --with-lab-view
```

This creates:

```
backend/lab_communicator/my_backend/
├── __init__.py       # lazy export MyBackendLabCommunicator
├── communicator.py   # checklist class (stub hooks + `_persist_state` no-op)
├── primitives.py     # asyncio.sleep stubs — replace with real hardware
└── lab_view/           # only with --with-lab-view; copied from real/lab_view/default + minimal lab_state.json if needed
```

Then:

1. Set **`LAB_VIEW_PATH`** to that `lab_view/` directory (or any bundle that satisfies the mandatory files in **section 0.1**).
2. Add a **`LAB_MODE`** branch in `backend/main.py` that imports `MyBackendLabCommunicator` and assigns `lab = ...`.
3. Replace stub bodies in `primitives.py` (and optional hooks in `communicator.py`) with real behavior.

Run `python scripts/create_lab_communicator.py --help` for `--lab-view-root`, `--force`, etc.

---

## 2. Folder layout (repository)

```text
backend/lab_communicator/
├── README.md                  ← this file
├── base.py                    ← LabCommunicator template (state machine + orchestrators)
├── shared/                    ← cross-lab helpers (no hardware imports from concrete backends)
│   ├── lab_view_config.py     ← LAB_VIEW_PATH bootstrap, laser JSON helpers
│   ├── catalog_bundle.py      ← merged catalog rows/maps from lab_view JSON
│   ├── snapshot.py
│   ├── state_machine.py
│   ├── commits.py
│   ├── catalog_lookup.py
│   ├── motor_state.py
│   ├── storage_intent.py
│   ├── placement_ui.py
│   └── util.py
├── mock/
│   ├── __init__.py
│   ├── communicator.py
│   ├── primitives.py
│   ├── persistence.py
│   └── lab_view/              ← committed MOCK bundle (see **section 0**)
├── real/
│   ├── __init__.py
│   ├── communicator.py
│   ├── primitives.py
│   ├── coordinate_frames.py
│   ├── gripper.py
│   ├── optimization.py
│   ├── scan.py
│   ├── video.py
│   └── lab_view/default/      ← REAL starter bundle (+ optional states/, …)
└── <your_backend>/            ← add new folders here (see **section 1**)
```

**Import boundaries** (CI-enforced in `backend/tests/test_lab_primitives.py`): `shared/` must not import `base.py` or concrete backends; `real/` and `mock/` must not import each other; `base.py` must not import `real/` or `mock/`.

---

## 3. Two files per backend, two audiences

| File | Answers |
|------|---------|
| **`communicator.py`** | “What hooks does this backend implement?” (`__init__`, persistence hooks, `_primitive_*` delegations, extra UI/video helpers). |
| **`primitives.py`** | “What API call or simulated step implements each primitive?” — one free function per primitive; **must not** read/write `communicator.current_state`. |

### 3.1 `_primitive_*` ↔ `primitive_*` pairing

Each primitive is a **one-line** `_primitive_*` method on the class plus a **`primitive_*`** free function:

```python
async def _primitive_pick_component(
    self, target_id: str, commanded: LabPose, params: Dict[str, Any]
) -> float:
    from lab_communicator.myrobot.primitives import primitive_pick_component
    return await primitive_pick_component(self, target_id, commanded, params)
```

If you need more than one line in `_primitive_*`, the logic belongs in `primitives.py`.

Common hooks match mock/real naming; compare your scaffold to `mock/communicator.py` for signatures (`_primitive_hover_component` passes `speed: int`, scan/optimize use keyword-only parameters matching base).

---

## 4. What `base.py` does for you

`LabCommunicator` owns `current_state`, `catalog_map`, the state lock, refusal helpers, status transitions, snapshot merge, motor-angle injection, and orchestration for every primitive. Your backend supplies:

- **`__init__`**: seed `catalog_map` / `current_state` (often using `merged_catalog_maps()` + optional `lab_state.json`).
- **`_persist_state`**: mock writes JSON; real is typically a no-op.
- Optional virtual hooks — `_apply_loaded_pose_to_hardware`, `_post_apply_snapshot`, `_after_move_to_storage`, `_after_move_out_of_storage`, `_apply_is_placed_flag` (see real/mock).
- **`_primitive_*`** methods delegating to `primitives.py`.
- **`get_video_stream`** / **`capture_table_cam`** / etc., where defaults are insufficient (`base.py` raises `NotImplementedError` for `get_video_stream`; mock/real override).

---

## 5. Adding a backend **without** the script

Same layout as **section 1**:

1. **`your_pkg/__init__.py`** — PEP 562 lazy export pattern (`mock/__init__.py` is the template).
2. **`your_pkg/communicator.py`** — subclass `LabCommunicator`; implement checklist (copy scaffold from §1 output or from `mock/`).
3. **`your_pkg/primitives.py`** — state-free primitive functions.

Do **not** edit `base.py` or violate `shared/` import rules unless the abstraction genuinely belongs there.

Provide or reuse a **lab_view** directory satisfying **section 0.1**.

---

## 6. Wire into `main.py` and `.env`

`LAB_VIEW_PATH` is resolved **before** the communicator is constructed.

Add a branch alongside `MOCK` / `REAL`:

```python
elif LAB_MODE == "MY_BACKEND":
    from lab_communicator.my_backend import MyBackendLabCommunicator
    lab = MyBackendLabCommunicator()
```

`.env` example:

```env
LAB_MODE=MY_BACKEND
LAB_VIEW_PATH=backend/lab_communicator/my_backend/lab_view
```

Video routes: **`LAB_MODE == "REAL"`** is what triggers MJPEG from `lab.get_video_stream` today; other modes serve the static SVG unless you extend `main.py`.

---

## 7. Verification

`backend/tests/test_lab_primitives.py` encodes the architectural rules:

- Primitive hooks / free functions must not touch `current_state`.
- No cross-imports between sibling backends.
- `shared/` and `base.py` isolation.

Copy/adapt the mock/real round-trip test classes when your backend grows beyond stubs.

---

## 8. Summary

| Concept | One-liner |
|---------|-----------|
| **`LAB_VIEW_PATH`** | Per-deployment bundle; required JSON + dirs created at bootstrap — communicators consume it via `get_lab_view_paths()`. |
| **`base.py`** | Template class — orchestrators + state machine; backends only plug hooks. |
| **`shared/`** | Shared helpers — hardware-agnostic; includes lab_view bootstrap + catalog merge. |
| **`<backend>/communicator.py`** | Checklist: init, persistence hooks, `_primitive_*` delegations, optional UI/video. |
| **`<backend>/primitives.py`** | Hardware/simulation steps — state-clean free functions. |
| **New backend** | Run `scripts/create_lab_communicator.py ...`, implement primitives, wire `LAB_MODE` + `LAB_VIEW_PATH`. |
