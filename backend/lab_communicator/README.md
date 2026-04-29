# Lab communicator: the backend behind every primitive

This package is the **adapter layer** between the HTTP-facing primitive vocabulary (`lab_primitives`) and whatever physical lab is on the other side. Today there are two backends shipped — **`mock`** (file-backed simulator, the canonical UI-exercise environment) and **`real`** (cloud-labs / `lab_automation` integration) — but the architecture is explicitly designed so adding a third is **purely additive**: you create a new folder, you don't touch `base.py` or `shared/`.

**Related:** [`../lab_primitives/README.md`](../lab_primitives/README.md) (the HTTP command vocabulary that calls into here), [`../lab_model/README.md`](../lab_model/README.md) (the **tunables / measurables** domain shapes), [`../../communicator_refactor.md`](../../communicator_refactor.md) (the design doc that drove the current folder structure, with the full method inventory and per-phase migration history).

> ⓘ **Want to add a new backend?** Jump to [§5 "Adding a new backend"](#5-adding-a-new-backend) for the file-by-file recipe.

---

## 1. Folder layout

```text
backend/lab_communicator/
├── README.md                  ← this file
├── base.py                    ← LabCommunicator template class (state machine + orchestrators)
├── shared/                    ← cross-lab building blocks (no hardware imports)
│   ├── snapshot.py            ← LabPose dataclass; snapshot load + merge
│   ├── state_machine.py       ← refuse_if_* helpers (HOLDING / STORED / busy gates)
│   ├── commits.py             ← commit_* helpers (per-primitive state writes)
│   ├── catalog_lookup.py      ← catalog accessors (catalog_wh, motor_catalog_ok)
│   ├── motor_state.py         ← inject_motor_rotations_into_state
│   ├── storage_intent.py      ← storage-intent JSON persistence helpers
│   ├── placement_ui.py        ← Newton place-UI hook helpers (cloudlab progress callback)
│   └── util.py                ← env_float, optional_float
├── real/                      ← cloud-labs / lab_automation backend
│   ├── __init__.py            ← PEP 562 lazy re-export of RealLabCommunicator
│   ├── communicator.py        ← class checklist: __init__, state-side hooks, primitive delegations
│   ├── primitives.py          ← the API-call list: primitive_<name> free functions
│   ├── coordinate_frames.py   ← XY/Z/yaw transforms + calibration constants
│   ├── gripper.py             ← gripper status + boot reconciliation helpers
│   ├── optimization.py        ← Newton/COBYLA progress-watcher background thread
│   ├── scan.py                ← physical-scan inventory rebuild
│   └── video.py               ← MJPEG streams + table-cam capture
└── mock/                      ← file-backed simulator
    ├── __init__.py            ← PEP 562 lazy re-export of MockLabCommunicator
    ├── communicator.py        ← same checklist shape as real, no hardware
    ├── primitives.py          ← simulated hardware steps (asyncio.sleep + noise)
    └── persistence.py         ← JSON state-file load/save helpers
```

The split is **load-bearing**: `base.py` and `shared/` are off-limits for backend-specific logic, and the three banned import edges (`shared/` → `base.py` / `lab_automation`, `real/` ↔ `mock/`, `base.py` → `real/` / `mock/`) are enforced by CI lints in [`backend/tests/test_lab_primitives.py`](../tests/test_lab_primitives.py).

---

## 2. The two-audience design

Each backend folder has **two files** with two distinct audiences. Reading either one top-to-bottom should answer one question and not the other:

| File | Question it answers | Reading time |
|---|---|---|
| **`communicator.py`** | "What does this backend implement?" | The full checklist of hooks, in one place. |
| **`primitives.py`** | "What's the API call for each primitive?" | One function per primitive — `lab_automation` / SDK call front and center. |

This split is what made the post-refactor file sizes bearable:

```text
                       pre-refactor    today      reduction
real/communicator.py   ~95 KB          36.2 KB    −62%
mock/communicator.py   ~52 KB          21.0 KB    −60%
real/primitives.py      —              21.6 KB    (new)
mock/primitives.py      —              13.5 KB    (new)
```

The refactor history is in [`../../communicator_refactor.md`](../../communicator_refactor.md) (Phases 1, 2A–2D, 3, 4 — all shipped).

### 2.1 The `_primitive_*` ↔ `primitive_*` pairing

Each primitive is implemented as **one method on the class** (a 1-line delegation) **+ one free function in `primitives.py`** (the actual hardware step):

| Class hook (in `communicator.py`)         | Free function (in `primitives.py`)     |
|-------------------------------------------|----------------------------------------|
| `_primitive_move_motor`                   | `primitive_move_motor`                 |
| `_primitive_motor_set_zero`               | *(base default no-op)*                 |
| `_primitive_move_component`               | `primitive_move_component`             |
| `_primitive_pick_component`               | `primitive_pick_component`             |
| `_primitive_hover_component`              | `primitive_hover_component`            |
| `_primitive_place_from_hover`             | `primitive_place_from_hover`           |
| `_primitive_scan_rotate_in_place`         | `primitive_scan_rotate_in_place`       |
| `_primitive_observe_measurables`          | `primitive_observe_measurables`        |
| `_primitive_prepare_optimization_run`     | `primitive_prepare_optimization_run`   |
| `_primitive_optimize_component`           | `primitive_optimize_component`         |
| `_primitive_finalize_optimization_run`    | `primitive_finalize_optimization_run`  |
| `_primitive_add_component_to_state`       | `primitive_add_component_to_state`     |

The `_primitive_*` body is **always** one line of delegation:

```python
async def _primitive_pick_component(
    self, target_id: str, commanded: LabPose, params: Dict[str, Any]
) -> float:
    from lab_communicator.real.primitives import primitive_pick_component
    return await primitive_pick_component(self, target_id, commanded, params)
```

If you find yourself writing more than that, the logic belongs in `primitives.py`.

---

## 3. What `base.py` does for you

`LabCommunicator` is a **concrete template class** (post Phase 2A) — not an ABC. It owns the state machine and orchestrates every primitive. Backends inherit and override the small set of hooks; everything cross-cutting is already wired up:

| Owned by `base.py` | What this means for a backend |
|---|---|
| `self.current_state` dict (system_status, components, holding, optimization_step, …) | You don't seed the canonical structure from scratch. `super().__init__()` gives you a safe-to-access default. |
| `self._state_lock` (`threading.RLock`) | All state mutations go through it. You never acquire it manually. |
| `self.catalog_map` | Backend populates it; base reads it via `_catalog_meta_for_tag`. |
| **Primitive orchestrators** (~18 methods) — `move_motor`, `move_component`, `store_component`, `place_from_storage`, `pick_component`, `hover_component`, `place_from_hover`, `scan_rotate_in_place`, `optimize_component`, `observe_measurables_for_tag`, `affirm_placed_at_current`, `add_component_to_state`, `confirm_holding_tag`, `remove_component`, `repack_storage_slot`, `recenter_stored_in_inventory`, `set_lab_state`, … | Refusals, status flips, pose lookup, commits, lock discipline, persistence calls, storage-intent updates, `is_placed` propagation. **All of it.** |
| `_set_status`, `_set_holding`, `_clear_holding`, `_persist_state` (caller side) | Status / holding writes always under the lock with `last_updated` stamped. |

A backend's primitive contributes **only the hardware step** — the part that distinguishes "talk to a robot" from "sleep + add noise".

---

## 4. What `shared/` provides

`shared/` is the cross-lab building-block layer. It can be imported by `base.py`, `real/`, and `mock/`; it cannot import any of them (CI-enforced).

| Module | Role |
|---|---|
| **`snapshot.py`** | `LabPose` (frozen dataclass: `x, y, z, rotation`); `merge_snapshot_components`; `normalize_loaded_state`. |
| **`state_machine.py`** | Refusal helpers: `refuse_if_holding`, `refuse_if_holding_other_tag`, `refuse_if_not_holding`, `refuse_if_stored`, `refuse_if_not_stored`, `refuse_if_not_in_state`, `refuse_if_not_on_breadboard`, `refuse_if_in_storage_quadrant`, `refuse_if_status_not_idle`, `refuse_if_z_lab_out_of_bounds`. Each returns a `Refusal` dataclass or `None`. |
| **`commits.py`** | Per-primitive commit helpers that write tunables / measurables / holding fields under the lock: `commit_pick`, `commit_hover`, `commit_place_from_hover`, `commit_scan_rotation`, `commit_move_to_breadboard`, `commit_move_to_storage`, `commit_affirm_placed`, `commit_observed_camera_image`, `commit_optimization_complete`. |
| **`catalog_lookup.py`** | `catalog_wh(tag, catalog_map, default_w, default_h)`, `motor_catalog_ok(catalog_map, target_id, motor_id)`. |
| **`motor_state.py`** | `inject_motor_rotations_into_state(state, lab_mode)` — merges `motor_rotation_store` JSON into the polled lab state. |
| **`storage_intent.py`** | Storage-intent JSON persistence (`load_intent`, `save_intent`, …). Used by real for the side-channel slot file; mock doesn't need it. |
| **`placement_ui.py`** | Newton place-UI hook helpers (Stage 9 of `new_primitives.md` / cloudlab progress callback). |
| **`util.py`** | `env_float(env_var, default)`, `optional_float(params, key)`. |

If two backends would write the same helper, it goes in `shared/`. If one backend would, it stays in that backend's folder.

---

## 5. Adding a new backend

Suppose you want to add a **`myrobot`** backend. The recipe is **purely additive**: create one folder, three required files, and as many optional helper modules as your hardware needs. **Don't edit `base.py` or `shared/`** — if you find yourself wanting to, the abstraction is wrong, not your code.

### 5.1 Required: `myrobot/__init__.py`

Verbatim pattern, just rename the symbol — this enables `from lab_communicator.myrobot import MyRobotLabCommunicator` while keeping heavy imports lazy:

```python
"""<one-line description of the backend>."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lab_communicator.myrobot.communicator import MyRobotLabCommunicator

__all__ = ["MyRobotLabCommunicator"]


def __getattr__(name: str):
    if name == "MyRobotLabCommunicator":
        from lab_communicator.myrobot.communicator import MyRobotLabCommunicator
        return MyRobotLabCommunicator
    raise AttributeError(f"module 'lab_communicator.myrobot' has no attribute {name!r}")
```

### 5.2 Required: `myrobot/communicator.py` — the **checklist**

Reading this file top-to-bottom should answer "what does this backend implement?". Four sections, in order:

#### a) Class declaration + `__init__`

```python
class MyRobotLabCommunicator(LabCommunicator):
    log_prefix = "[MYROBOT LAB]"   # appears in every log line from base + shared

    def __init__(self):
        super().__init__()
        # super() seeds: self.current_state defaults, self._state_lock,
        #                self.catalog_map = {}

        # Then YOU populate:
        # 1. self.current_state — load from disk / scan / however you bootstrap
        # 2. self.catalog_map — { tag_id: catalog_entry_dict }
        # 3. backend-specific fields (your robot client, file paths, etc.)

        self._reconcile_holding_on_boot()  # see new_primitives.md §6.3
```

#### b) State-side virtual hooks (override only the ones you need)

Hooks for managing the glue between in-memory state and your hardware-side cache / persistence. **They are not primitives** — primitives are §c. All of these have base-default no-ops; override only what your backend actually needs.

| Hook | When it fires | What real does | What mock does |
|---|---|---|---|
| `_apply_loaded_pose_to_hardware(target_id, lab_pose)` | Once per component during `set_lab_state` snapshot load | Forward-transforms XY/Z/yaw → robot frame and writes `OpticalComponent.current_location` | no-op |
| `_post_apply_snapshot()` | Once at end of `set_lab_state` | Rebuilds storage-intent JSON file | no-op |
| `_persist_state()` | After every state mutation | no-op (state is in memory) | Writes `current_state` to disk |
| `_after_move_to_storage(target_id, slot_i, slot_j)` | After successful `store_component` | Writes storage-intent file | no-op |
| `_after_move_out_of_storage(target_id)` | After successful `place_from_storage` / `move_component` from storage | Removes target from storage-intent file | no-op |
| `_apply_is_placed_flag(target_id, value)` | After `affirm_placed_at_current` | Sets `OpticalComponent.is_placed` | no-op |

#### c) Primitive hooks — 1-line delegations to `primitives.py`

Same shape for every backend. Twelve hooks, each one line:

```python
async def _primitive_move_motor(self, target_id, motor_id, distance):
    from lab_communicator.myrobot.primitives import primitive_move_motor
    await primitive_move_motor(self, target_id, motor_id, distance)

async def _primitive_move_component(self, target_id, commanded):
    from lab_communicator.myrobot.primitives import primitive_move_component
    return await primitive_move_component(self, target_id, commanded)

# … same pattern for: pick_component, hover_component, place_from_hover,
#                     scan_rotate_in_place, observe_measurables,
#                     prepare_optimization_run, optimize_component,
#                     finalize_optimization_run, add_component_to_state.
```

`_primitive_motor_set_zero` is a base-default no-op; both real and mock inherit it. Override only if your robot has a "zero this motor" hardware call.

#### d) Backend-specific UI methods

Anything the UI calls that isn't on the `LabCommunicator` template — e.g. `get_video_stream`, `capture_table_cam`, `get_cobyla_reference_status`, gripper-status overrides. These are **not** primitives; they're additional surface for your hardware. Keep them thin and delegate to per-backend helper modules (`video.py`, `gripper.py`, …).

### 5.3 Required: `myrobot/primitives.py` — the **API-call list**

Reading this file top-to-bottom should answer "what does each primitive actually call?". Pattern:

```python
from __future__ import annotations
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional

from lab_communicator.shared.snapshot import LabPose
from lab_communicator.shared.util import optional_float

if TYPE_CHECKING:
    from lab_communicator.myrobot.communicator import MyRobotLabCommunicator


async def primitive_pick_component(
    communicator: "MyRobotLabCommunicator",
    target_id: str,
    commanded: LabPose,
    params: Dict[str, Any],
) -> float:
    """Hardware step: <one-sentence description of the API call>."""
    safe_z = optional_float(params, "safe_z")
    await asyncio.to_thread(
        communicator.your_robot_client.pick,
        target_id,
        safe_z=safe_z,
    )
    return communicator._intent_hover_z_lab(target_id)
```

**Architectural rules** for `primitives.py` (CI-enforced):

1. **State-clean.** Never read or write `communicator.current_state`. Take what you need as arguments. The orchestrator already passes commanded poses, params, and any progress-callback you need.
2. **No imports from `lab_communicator.base`** (would be a circular-import trap — `shared/` rule §5.1 in the design doc).
3. **No imports from sibling backends.** `myrobot/` cannot import `lab_communicator.real` or `lab_communicator.mock`. Cross-backend logic belongs in `shared/`.
4. **Long-running primitives** (currently just `primitive_optimize_component`) accept a `progress_callback` kwarg and call it periodically — the architectural escape hatch that lets you publish state updates without holding the lock.

### 5.4 Optional: backend-specific helper modules

Whatever your hardware needs:

- **`coordinate_frames.py`** — XY/Z/yaw transforms + calibration constants (real has one because the robot table is rotated 90° relative to the lab frame; mock doesn't need one because mock *is* the reference lab frame).
- **`video.py`**, **`gripper.py`**, **`scan.py`**, **`optimization.py`**, **`persistence.py`** — see real's tree for examples.

The rule of thumb: **anything that would only be useful to your specific backend** lives in your folder. Anything that two backends would write the same way moves to `shared/`.

### 5.5 Wire it up

After your backend is implemented, the only repo-wide edit is the dispatch in [`backend/main.py`](../main.py) where `LabCommunicator` is instantiated based on `LAB_MODE`. Add a branch for your `LAB_MODE` value and import your class. (Today there are two branches: `MOCK` and `REAL`.)

### 5.6 Verification

The CI lints in [`backend/tests/test_lab_primitives.py`](../tests/test_lab_primitives.py) will tell you if anything is wrong:

- ✅ `test_primitive_hooks_do_not_touch_current_state` — your `_primitive_*` class methods don't read or write `self.current_state`.
- ✅ `test_primitive_free_functions_do_not_touch_current_state` — your `primitive_*` free functions in `primitives.py` don't touch `communicator.current_state`.
- ✅ `test_real_and_mock_do_not_import_each_other` (extends to your folder) — `myrobot/` doesn't import sibling backends.
- ✅ `test_shared_does_not_import_banned_modules` — `shared/` is unmodified.
- ✅ `test_base_does_not_import_concrete_backends` — `base.py` is unmodified.

For end-to-end coverage, the `*RoundTripTests` test classes in the same file are templates — copy and adapt them for `MyRobotLabCommunicator` to verify each primitive end-to-end.

---

## 6. Summary

| Concept | One-line |
|---|---|
| **`base.py`** | Concrete template class: state machine + 18 primitive orchestrators. Off-limits to backend logic. |
| **`shared/`** | Cross-lab building blocks: refusal helpers, commit helpers, snapshot dataclasses, catalog accessors. Hardware-agnostic. |
| **`<backend>/communicator.py`** | The class checklist: `__init__`, 6 state-side virtual hooks, 12 `_primitive_*` 1-line delegations, any backend-specific UI extras. |
| **`<backend>/primitives.py`** | The API-call list: 12 `primitive_<name>` free functions, each showing the exact call to your hardware (or simulated step for mock). |
| **Adding a new backend** | One folder, three required files (`__init__.py`, `communicator.py`, `primitives.py`), as many helper modules as you need. **Zero edits** to `base.py` or `shared/`. |

For the full architectural rationale and per-phase migration history, see [`../../communicator_refactor.md`](../../communicator_refactor.md).
