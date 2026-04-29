# Communicator refactor — folder-per-backend with shared template

> Status: **shipped.** Phases 1, 2A, 2B, 2C, 2D, and 3 (docs sweep + final
> lint consolidation) are all complete. Phase 4 (the
> `primitives.py`-per-backend split + `_do_*` → `_primitive_*` rename
> requested after Phase 3) shipped on top — see §11 below for the
> post-roadmap status note.
>
> Sibling docs: `new_primitives.md` (in-air primitives), `fixing.md`
> (cloud-labs ↔ `lab_automation` ownership), `bugs.md` (active
> cross-wall debugging), `labautomation_new_primitives.md` (the contract
> the upstream `lab_automation` repo is asked to honor).

---

## 1. TL;DR

`backend/lab_communicator/` is currently a flat folder with `base.py`
(thin ABC), `mock.py` (~1100 lines), and `real.py` (~2400 lines). Both
backends carry ~60 lines of identical state-machine bookkeeping inside
each primitive — across ~18 primitives that's ~600 lines of literal
duplication on top of the genuine hardware code. Reading either file
to answer "which `lab_automation` function backs each primitive?"
takes far too long because the cross-wall call is buried under
refusals, parameter parsing, BUSY/IDLE transitions, tunables/
measurables writes, and holding-field bookkeeping.

This doc proposes splitting `lab_communicator/` into a package mirroring
the convention already used by `lab_model/` and `lab_primitives/`:

```text
backend/lab_communicator/
├── base.py                # template-method LabCommunicator (state machine + orchestrators)
├── shared/                # cross-lab building blocks (no hardware imports)
├── real/                  # cloud-labs / lab_automation backend
└── mock/                  # file-backed simulator
```

Per-backend folders own their **own** `coordinate_frames.py` (calibration
is intrinsically lab-specific), their own gripper / video / scan
modules, and a tiny `_do_<primitive>` hook for each primitive. The
shared layer owns everything that would be identical for a hypothetical
"Robot B" backend — primitive orchestrators, refusal helpers, snapshot
load skeleton, storage-intent tracking, motor-rotation injection.

After the refactor:

- `real/communicator.py` reads as a manifest: one `_do_<primitive>` per
  HTTP-facing primitive, each ~10–30 lines, each a clear "transform
  args; call `experiment.<primitive>_cloudlab`; return".
- A new robot backend = a new folder with its own `coordinate_frames.py`,
  `gripper.py`, etc., plus `_do_*` hooks. Zero edits to `base.py` or
  `shared/`.
- The Stage C lint (`fixing.md` §9) tightens from "writes to
  `current_location` only inside `set_lab_state`" to "writes to
  `current_location` only inside `_apply_loaded_pose_to_hardware`" —
  one method, one obvious owner.

The refactor is **not** "just moving things around" — Phase 1 is, but
Phase 2 introduces real abstractions (the template-method pattern, the
`_apply_loaded_pose_to_hardware` virtual, the pose-returning `_do_*`
hooks) that delete duplication rather than relocating it. Feasibility
is good; the main risks are concentrated around three primitives
(`set_lab_state`, `move_component`, `optimize_component`) and one
asymmetry (mock is JSON-backed, real is in-memory).

---

## 2. Problem statement

Three concrete pain points, all observable today:

### 2.1 Reading `real.py` is slow

When you open `real.py` to answer a basic question — e.g. "what does
`PICK_COMPONENT` do at the lab level?" — you have to read past:

- Refusal / state-machine gates (~10 lines).
- Parameter parsing from a `Dict[str, Any]` payload (~6 lines).
- `system_status = BUSY` write under the state lock (~3 lines).
- The placeholder branch for `HOVER_PLACEHOLDER_STATE=1` (~25 lines).
- The actual cross-wall call — **5 lines**.
- The post-success commit: tunables write, measurables write, holding
  field write, last_updated, status flip back (~15 lines).
- Catch + log + status-rollback (~10 lines).

The signal-to-noise ratio is low. The cross-wall call is the only part
that distinguishes `pick_component` from any other primitive, but it's
~7% of the function.

### 2.2 Mock and real duplicate the state machine

A direct line-by-line diff of `mock.pick_component` vs
`real.pick_component` (the placeholder branch) shows ~60 of 70 lines
are identical. The only meaningful difference is the hardware step:

- Mock: `await asyncio.sleep(1.2)` + add Gaussian noise to the
  measurables write.
- Real: 5 lines of XY/Z/yaw transform + `asyncio.to_thread(
  experiment.pick_component_cloudlab, comp, safe_z=safe_z)`.

Same shape for `hover_component`, `place_from_hover`,
`scan_rotate_in_place`, `move_component`, `place_from_storage`,
`store_component`, `repack_storage_slot`, `recenter_stored_in_inventory`.

Across 18 primitives, this is ~600 lines of verbatim duplication.

### 2.3 The next-robot story is undefined

If a second physical lab were added tomorrow — different XY rotation,
different Z calibration, different gripper behavior, different
optimizer framework — the maintainer would have to:

1. Copy `real.py` wholesale (~2400 lines).
2. Identify which ~600 lines are "shared state machine".
3. Identify which ~150 lines are "lab-specific calibration".
4. Identify which ~400 lines are "lab-specific optimization runtime".
5. Avoid drift between the two backends as the shared parts evolve.

That's an unbounded amount of work on every shared change.

---

## 3. Why folder-per-communicator

Two consistency arguments and one design argument.

### 3.1 The repo already does this for `lab_model/` and `lab_primitives/`

```text
backend/lab_model/
├── component_model.py     # Tunables / measurables shapes
├── storage_region.py      # Storage-quadrant geometry
├── motor_rotation_store.py# Software angle tracking
└── holding.py             # Top-level holding-state model

backend/lab_primitives/
├── ids.py                 # PrimitiveId enum
├── schemas.py             # Pydantic request shapes
├── registry.py            # PrimitiveId -> handler
├── dispatch.py            # parse + execute
└── protocol.py            # LabPrimitiveSurface protocol
```

Both are concept-organized packages. Files are scoped tightly. The
public surface re-exports through `__init__.py` so callers don't see
the internal shape.

`lab_communicator/` is the only top-level subsystem that's still flat,
and it's also the largest (~3500 lines combined). It's the obvious
next package.

### 3.2 Calibration is intrinsically per-lab

XY rotation, Z origin, yaw offset, recorder camera ports, gripper
geometry — none of these survive a robot swap. They are lab-specific
data that happens to be expressed as code. Putting them in a single
top-level `coordinate_frames.py` would force a future "Robot B" to
either:

- import-and-monkeypatch our constants (fragile), or
- replace the whole module via Python import shenanigans (worse), or
- accept that the file lies about being "shared" and gets edited per
  deploy (worst).

A `real/coordinate_frames.py` that lives **with** the rest of the real
backend is the honest answer. Mock gets its own — usually identity, but
it's the right place to inject deliberate noise or fake calibration
errors for UI testing.

### 3.3 The hardware-step abstraction unblocks reading the file

Once primitives are template-methods with a one-line `_do_<primitive>`
hook, `real/communicator.py` becomes a flat list of "this is how each
primitive talks to the lab":

```python
# real/communicator.py (illustrative, post-Phase-2)
async def _do_pick(self, target_id, pose, params) -> float:
    comp = self.component_map.get(target_id)
    if not comp or not comp.current_location:
        raise PickError(f"no current_location for {target_id}; rescan")
    safe_z = _optional_float(params, "safe_z")
    await asyncio.to_thread(
        self.experiment.pick_component_cloudlab, comp, safe_z=safe_z
    )
    return self._intent_hover_z_lab(target_id)

async def _do_hover(self, target_id, lab_pose, speed):
    comp = self.component_map.get(target_id)
    tx, ty = lab_table_xy_to_robot_xy(lab_pose.x, lab_pose.y)
    tz = self._z_lab_to_robot(target_id, lab_pose.z)
    yaw = lab_rotation_to_robot_yaw(lab_pose.rotation)
    await asyncio.to_thread(
        self.experiment.hover_component_cloudlab, comp, tx, ty, tz, yaw, speed=speed
    )

# ... 16 more, each similarly small ...
```

That's the manifest the user asked for.

---

## 4. Inventory — every method in `real.py` audited

Categorization rule: **"could a hypothetical Robot B use this
unchanged?"** If yes → shared. If no → real-specific. If "yes for the
core, no for the hook" → split.

### 4.1 Module-level helpers (top of file)

| Function | Verdict | Target home |
|---|---|---|
| `lab_table_xy_to_robot_xy` | real-specific (uses `LAB_ROBOT_TABLE_ROTATION_RAD`) | `real/coordinate_frames.py` |
| `robot_table_xy_to_lab_xy` | real-specific | `real/coordinate_frames.py` |
| `lab_rotation_to_robot_yaw` | real-specific | `real/coordinate_frames.py` |
| `robot_yaw_to_lab_rotation` | real-specific | `real/coordinate_frames.py` |
| `_env_float` | shared (utility) | `shared/util.py` |
| `_optional_float` | shared (utility) | `shared/util.py` |

### 4.2 Storage intent (cloud-labs UI concept; pure JSON IO)

| Function | Verdict | Target home |
|---|---|---|
| `_stored_intent_path` | shared | `shared/storage_intent.py` |
| `_load_stored_intent_from_disk` | shared | `shared/storage_intent.py` |
| `_save_stored_intent_to_disk` | shared | `shared/storage_intent.py` |
| `_stored_intent_set_slot` | shared | `shared/storage_intent.py` |
| `_stored_intent_remove` | shared | `shared/storage_intent.py` |
| `_rebuild_stored_intent_from_lab_state` | shared | `shared/storage_intent.py` |
| `get_stored_intent_for_layout` | shared | `shared/storage_intent.py` |

These all touch the same JSON file at
`backend/data/stored_intent.json` and never reference `lab_automation`.
Currently duplicated in mock as a no-op (mock doesn't track intent).
Move to `shared/storage_intent.py` as a `StorageIntentStore` class;
both backends instantiate one.

### 4.3 Catalog and motor-state shaping

| Function | Verdict | Target home |
|---|---|---|
| `_catalog_wh` | shared | `shared/catalog_lookup.py` |
| `_component_height_mm` | shared | `shared/catalog_lookup.py` |
| `_motor_catalog_ok` | shared | `shared/catalog_lookup.py` |
| `_inject_motor_rotations_into_state` | shared (mock has identical copy) | `shared/motor_state.py` |

Catalog format is a cloud-labs convention defined by
`schemas/component_catalog.{real,mock}.json`. Both backends parse it
the same way.

### 4.4 Frame-aware Z helpers

| Function | Verdict | Target home |
|---|---|---|
| `_z_lab_to_robot` | real-specific (uses real calibration) | `real/coordinate_frames.py` (instance method) |
| `_z_robot_to_lab` | real-specific | `real/coordinate_frames.py` |
| `_intent_hover_z_lab` | real-specific (asks lab_automation) | `real/coordinate_frames.py` or `real/gripper.py` |

Mock doesn't need these; mock's "z" is direct — no transform.

### 4.5 Lab-state read / write surface

| Function | Verdict | Target home |
|---|---|---|
| `get_lab_state` | shared | `base.py` |
| `set_lab_state` (skeleton) | shared, **with virtual hook** | `shared/snapshot.py` |
| `set_lab_state` (cross-wall write) | real-specific | `real/communicator.py::_apply_loaded_pose_to_hardware` |
| `_inject_motor_rotations_into_state` | shared | `shared/motor_state.py` |
| `refresh_pose_from_camera` | real-specific (calls `_initialize_state`) | `real/scan.py` |
| `refresh_state` | shared (alias) | `base.py` |

The trickiest split. `set_lab_state` is currently ~80 lines that mostly
do shared work (transforms, holding reset, intent rebuild, snapshot
merge), with a single per-component cross-wall write
(`comp.current_location = Pose(...)`) that lives in real. The hook is
clean: `_apply_loaded_pose_to_hardware(comp, lab_pose)`. Mock implements
it as a no-op.

### 4.6 Initial scan and physical I/O (real-only by nature)

| Function | Verdict | Target home |
|---|---|---|
| `_initialize_state` | real-specific | `real/scan.py` |
| `_camera_images_base_dir` | real-specific | `real/video.py` |
| `_send_recorder_cmd` | real-specific | `real/video.py` |
| `_start_recorder_processes` | real-specific | `real/video.py` |
| `_shutdown_recorders` | real-specific | `real/video.py` |
| `get_video_feed_status` | real-specific | `real/video.py` |
| `get_video_stream` | real-specific | `real/video.py` |
| `capture_table_cam` | real-specific | `real/video.py` |

No surprises. Mock has its own thin equivalents (a fake video stream
and a synthetic `capture_table_cam` PNG) which stay in `mock/video.py`
or just inline in `mock/communicator.py`.

### 4.7 Optimization runtime

| Function | Verdict | Target home |
|---|---|---|
| `_make_optimization_run_dir` | real-specific | `real/optimization.py` |
| `_apply_optimization_output_dir_kw` | real-specific (introspects strategy classes) | `real/optimization.py` |
| `_get_optimization_watch_dirs` | real-specific | `real/optimization.py` |
| `_get_latest_optimization_png` | real-specific | `real/optimization.py` |
| `_optimization_step_from_image_path` | real-specific | `real/optimization.py` |
| `_monitor_optimization_dir` | real-specific | `real/optimization.py` |
| `get_optimization_stream` | real-specific | `real/optimization.py` |
| `set_cobyla_reference_*` | real-specific (Cobyla strategy is in lab_automation) | `real/optimization.py` |
| `clear_cobyla_reference` | real-specific | `real/optimization.py` |
| `get_cobyla_reference_status` | real-specific | `real/optimization.py` |
| `get_cobyla_reference_png_bytes` | real-specific | `real/optimization.py` |
| `optimize_component` (skeleton) | shared (status flip + post-success commit) | `base.py` |
| `optimize_component` (strategy construction + monitor) | real-specific | `real/optimization.py::_do_optimize` |

The user's instinct here was correct: optimization runtime is largely
real-flavored. The `_apply_optimization_output_dir_kw` introspection
over `NewtonPlacementStrategy_cloudlab` and
`CobylaAlignmentStrategy_cloudlab` is a hard dependency on
`lab_automation`. The shared template is small but worth keeping for
status-lifecycle consistency.

### 4.8 Placement UI hook (lab_automation `place_progress` callback)

| Function | Verdict | Target home |
|---|---|---|
| `_tag_id_for_component` | shared | `shared/util.py` |
| `_ui_pose_for_placement_tick` | shared (pure pose math) | `shared/placement_ui.py` |
| `_apply_placement_ui_phase` | shared (writes to current_state) | `shared/placement_ui.py` |
| `_cloudlab_progress_callback` | real-specific (callback signature is lab_automation) | `real/optimization.py` |
| `_install_cloudlab_place_ui_hook` | real-specific | `real/optimization.py` |
| `_remove_cloudlab_place_ui_hook` | real-specific | `real/optimization.py` |

The writer (state mutation) is generic; the subscription is
real-specific. Mock doesn't subscribe at all, so the writer becomes
unused in mock — that's fine, it's available if a future mock test
wants to drive UI ticks synthetically.

### 4.9 Gripper status

| Function | Verdict | Target home |
|---|---|---|
| `get_gripper_status` | real-specific | `real/gripper.py` |
| `_reconcile_holding_on_boot` | real-specific (calls experiment) | `real/gripper.py` |

Mock has its own simulated copies (driven by
`MOCK_GRIPPER_CLOSED_ON_BOOT`); they stay in `mock/communicator.py`.

### 4.10 The 18 user-facing primitives

These are the methods that are currently duplicated between mock and
real. Section 6 walks each one with the shared/real/mock split.

| Method | Phase-2 home |
|---|---|
| `move_component` | `base.move_component` orchestrator + `real._do_move` + `mock._do_move` |
| `move_motor` | same shape |
| `motor_send_home` | same shape |
| `motor_set_zero` | same shape |
| `optimize_component` | same shape (skeleton in base, fat hook in real) |
| `remove_component` | same shape |
| `add_component_to_state` | same shape |
| `store_component` | same shape |
| `affirm_placed_at_current` | same shape |
| `repack_storage_slot` | same shape |
| `recenter_stored_in_inventory` | same shape |
| `place_from_storage` | same shape |
| `pick_component` | same shape |
| `hover_component` | same shape |
| `place_from_hover` | same shape |
| `scan_rotate_in_place` | same shape (held vs placed dispatch in `_do_scan_rotate`) |
| `confirm_holding_tag` | same shape |
| `observe_measurables_for_tag` | same shape |

---

## 5. Target architecture

### 5.1 Folder layout

```text
backend/lab_communicator/
├── __init__.py                       # re-exports LabCommunicator, RealLabCommunicator, MockLabCommunicator
├── base.py                           # template class: state machine + primitive orchestrators
├── shared/
│   ├── __init__.py
│   ├── util.py                       # _env_float, _optional_float, _tag_id_for_component
│   ├── catalog_lookup.py             # _catalog_wh, _component_height_mm, _motor_catalog_ok
│   ├── motor_state.py                # _inject_motor_rotations_into_state
│   ├── storage_intent.py             # StorageIntentStore (the 8-method cluster)
│   ├── snapshot.py                   # set_lab_state skeleton (with hook for hardware apply)
│   ├── state_machine.py              # refusal helpers (HOLDING gate, STORED gate, status writes)
│   └── placement_ui.py               # _apply_placement_ui_phase, _ui_pose_for_placement_tick
├── real/
│   ├── __init__.py                   # exports RealLabCommunicator
│   ├── communicator.py               # the class: lifecycle + _do_* hooks (the manifest)
│   ├── coordinate_frames.py          # XY/Z/yaw transforms + calibration constants
│   ├── gripper.py                    # gripper polling, holding reconciliation
│   ├── optimization.py               # output-dir polling, monitor, progress hooks, Cobyla ref
│   ├── video.py                      # recorder processes, MJPEG streaming, capture_table_cam
│   └── scan.py                       # _initialize_state, refresh_pose_from_camera
└── mock/
    ├── __init__.py                   # exports MockLabCommunicator
    ├── communicator.py               # the class: persistence + _do_* hooks (sleep + noise)
    ├── coordinate_frames.py          # identity transforms (mock state is in lab frame)
    └── persistence.py                # _read_state / _write_state to mock_lab_state.json
```

Architectural rules enforced by convention (and a CI lint at the end of
Phase 2):

- **`shared/` never imports from `real/` or `mock/` or `lab_automation`.**
  Pure domain code.
- **`real/` never imports from `mock/`** and vice versa.
- **`base.py` only imports from `shared/` and `lab_model` / `lab_primitives`.**
  Never from a backend folder.
- **The `experiment` attribute (the lab_automation handle) lives only in
  `RealLabCommunicator`.** `base.py` and `shared/` never reference it.
- **`shared/` never imports `LabCommunicator` from `base.py`, even
  `TYPE_CHECKING`-gated for type hints.** `base.py` imports from
  `shared/`, so the reverse direction is a circular-import trap. If a
  `shared/` helper needs to mutate communicator state, it must accept
  the specific primitive(s) it touches (`current_state: dict`,
  `lock: threading.Lock`, `catalog_map: dict`) — not the
  `LabCommunicator` instance. This rule is enforceable by the same lint
  that bans `lab_automation` imports in `shared/`.

### 5.2 Shape of `base.py`

Approximately 500 lines, structured as three blocks:

```python
class LabCommunicator:
    # === Owned state ===
    self.current_state: dict
    self._state_lock: threading.Lock
    self.catalog_map: dict
    self.storage_intent: StorageIntentStore

    # === Shared accessors (delegate to shared/) ===
    def get_lab_state(self) -> dict: ...
    def get_catalog(self) -> list: ...
    def return_tunables_for_tag(self, tag_id) -> dict: ...
    def return_measurables_for_tag(self, tag_id) -> dict: ...
    def _component_height_mm(self, tag_id) -> float: ...
    def _catalog_wh(self, tag_id) -> tuple: ...
    def _set_status(self, status: str) -> None: ...

    # === Snapshot lifecycle ===
    def set_lab_state(self, state: dict) -> None:
        # delegate to shared.snapshot.apply_snapshot, passing
        # self._apply_loaded_pose_to_hardware as the per-component hook
        ...
    def _apply_loaded_pose_to_hardware(self, comp, lab_pose) -> None:
        raise NotImplementedError  # subclass

    # === Primitive orchestrators (template methods) ===
    async def pick_component(self, target_id, params):
        # 1. shared.state_machine.refuse_if_holding_or_stored(...)
        # 2. read pose from current_state
        # 3. self._set_status(BUSY)
        # 4. settled_z = await self._do_pick(target_id, pose, params)
        # 5. shared.commit_pick(self.current_state, pose, settled_z)
        # 6. self._set_status(HOLDING)
        ...
    async def _do_pick(self, target_id, pose, params) -> float:
        """Return the actual settled z_lab (mm)."""
        raise NotImplementedError

    async def hover_component(self, target_id, target_pose):
        ...
    async def _do_hover(self, target_id, lab_pose, speed) -> Optional[LabPose]:
        """Return the lab-frame pose actually achieved (or None to mean
        'commanded pose'). Mock returns commanded + noise; real returns None."""
        raise NotImplementedError

    async def optimize_component(self, target_id, strategy_name, params):
        # 1. refuse if STORED
        # 2. self._set_status(OPTIMIZING)
        # 3. progress_callback writes optimization_step / optimization_run_dir
        #    under the state lock -- the hook never touches state directly
        # 4. await self._do_optimize(
        #        target_id, strategy_name, params, progress_callback)
        # 5. shared.commit_optimization(...) on success
        # 6. self._set_status(IDLE) in finally
        ...
    async def _do_optimize(
        self,
        target_id: str,
        strategy_name: str,
        params: Dict[str, Any],
        progress_callback: ProgressCallback,
    ) -> None:
        """Long-running optimization. The hook MUST NOT mutate
        self.current_state directly; instead it pumps progress events
        through ``progress_callback``, which serializes writes through
        the state lock owned by base.py."""
        raise NotImplementedError

    # ... 15 more orchestrator + hook pairs, each with its own return shape ...
```

**Per-primitive return types, on purpose** (see §7.2 abstraction 2 and
§8 Q2). `_do_pick` returns `float` (settled z_lab); `_do_hover` /
`_do_move` return `Optional[LabPose]` (actual pose, or None for "use
commanded"); `_do_place_from_hover` / `_do_optimize` return `None`. The
shared committer for each primitive is small enough that one shape per
hook is cheaper than fighting a uniform return type with sentinel
values. We do NOT introduce a kitchen-sink `PrimitiveResult` dataclass.

**Progress callbacks for long-running hooks.** `_do_optimize` takes a
`progress_callback: ProgressCallback` parameter. The callback is the
*only* way the hook is allowed to mutate `current_state` while running:
it serializes step counts, image-basename updates, and partial scores
through the state lock owned by `base.py`. This preserves the "base
owns the state" invariant without forcing the optimizer to release
control of its event loop. Same pattern is available to any future
long-running hook (e.g. a future `_do_calibrate`) that needs to stream
intermediate values.

Each orchestrator is ~15 lines. Each hook is `raise NotImplementedError`.
Total base.py: ~500 lines and obvious to read.

### 5.3 Shape of `real/communicator.py`

Approximately 400 lines:

```python
class RealLabCommunicator(LabCommunicator):
    def __init__(self):
        # Initialize state, component_map, catalog
        # Spawn recorder processes (delegates to real.video)
        # Connect to lab_automation experiment manager
        # Reconcile holding on boot (delegates to real.gripper)

    # === Snapshot hardware apply ===
    def _apply_loaded_pose_to_hardware(self, comp, lab_pose):
        # transform XY/Z/yaw and write comp.current_location

    # === Primitive hooks (the manifest) ===
    async def _do_move(self, target_id, lab_pose): ...           # ~10 lines
    async def _do_pick(self, target_id, pose, params): ...        # ~10 lines
    async def _do_hover(self, target_id, lab_pose, speed): ...    # ~10 lines
    async def _do_place_from_hover(self, target_id, lab_pose): ...# ~10 lines
    async def _do_scan_rotate(self, target_id, params, mode): ... # ~15 lines
    async def _do_optimize(self, target_id, strategy_name, params): ...  # ~150 lines (delegates heavy lifting to real.optimization)
    # ... etc ...
```

The non-hook methods (`__init__`, gripper bridge, video bridge, scan
bridge) wire up subsystems but don't carry primitive logic.

### 5.4 Shape of `mock/communicator.py`

Approximately 250 lines:

```python
class MockLabCommunicator(LabCommunicator):
    def __init__(self):
        # Load state from mock_lab_state.json or seed from catalog
        # Optional: simulate gripper-closed-on-boot

    # === Persistence (mock-specific) ===
    def _persist_state(self):
        # write self.current_state to mock_lab_state.json
    def _reload_state(self):
        # read from disk (used in tests for isolation)

    # === Snapshot hardware apply ===
    def _apply_loaded_pose_to_hardware(self, comp, lab_pose):
        pass  # mock has no robot

    # === Primitive hooks (mostly sleep + noise) ===
    async def _do_move(self, target_id, lab_pose):
        await asyncio.sleep(1.2)
        # Noise added by the shared committer if it asks for the actual settled pose

    async def _do_pick(self, target_id, pose, params):
        await asyncio.sleep(1.2)
        return DEFAULT_HOVER_Z_MM

    # ... etc, 6-line methods ...
```

---

## 6. Per-primitive split plan

For each primitive: what's shared (lives in `base.py` orchestrator),
what real implements in `_do_*`, what mock implements in `_do_*`.

| Primitive | Shared orchestrator | Real `_do_*` | Mock `_do_*` |
|---|---|---|---|
| `pick_component` | HOLDING/STORED gates; read pose; BUSY; commit HOLDING with returned settled-z; tunables + measurables + holding write | `comp.current_location` sentinel; XY/Z/yaw transform; `experiment.pick_component_cloudlab(comp, safe_z)`; query `_intent_hover_z_lab` for return value | `await asyncio.sleep(1.2)`; return `DEFAULT_HOVER_Z_MM` |
| `hover_component` | HOLDING gate; same-tag gate; parse pose; bounds-check z_lab; BUSY; commit HOLDING with returned pose | XY/Z/yaw transform; `experiment.hover_component_cloudlab(comp, ...)` | sleep 1.2s; return commanded pose + small noise |
| `place_from_hover` | HOLDING gate; same-tag gate; parse pose; BUSY; clear holding; commit IDLE | XY/yaw transform; `experiment.place_from_hover_cloudlab(comp, ...)` | sleep 1.2s |
| `scan_rotate_in_place` | held-vs-placed dispatch from current_state; param validation; BUSY; commit final rotation; return to HOLDING (held) or IDLE (placed) | dispatch to `experiment.scan_rotate_held_cloudlab` or `experiment.scan_rotate_placed_cloudlab`; transform args | simulate stepwise rotation with sleep + state writes |
| `move_component` | STORED gate; storage-quadrant gate; parse pose; BUSY; commit IDLE; tunables + measurables + presence write | XY/yaw transform; `experiment.place_component_wo_home_specific_xy_cloudlab(comp, ..., angle=[-180, 0, -rot])` (carries the deferred sign-convention comment from `fixing.md` §3.1) | sleep 1.2s + small noise |
| `place_from_storage` | STORED-required gate; storage-quadrant guard; parse pose; BUSY; commit IDLE; transition presence STORAGE → BREADBOARD; clear stored intent | calls `_do_move` internally (this primitive is a thin wrapper) | same: calls `_do_move` |
| `store_component` | BREADBOARD-required gate; allocate next free slot via `StorageIntentStore`; parse pose to slot center; BUSY; commit IDLE; transition to STORAGE | calls `_do_move` internally | calls `_do_move` |
| `affirm_placed_at_current` | STORED-required gate; clear stored intent; transition STORAGE → BREADBOARD; commit IDLE | sets `comp.is_placed = True` (legitimate `is_placed` write per Stage C6) | no-op |
| `repack_storage_slot` | STORED-required gate; allocate next free slot via `StorageIntentStore`; commit IDLE | calls `_do_move` internally | calls `_do_move` |
| `recenter_stored_in_inventory` | STORED-required gate; recompute slot center; commit IDLE | calls `_do_move` internally | calls `_do_move` |
| `move_motor` | catalog gate; BUSY; commit IDLE; update `motor_rotation_store` | `experiment.move_motor(comp, motor_id, distance)` | sleep 0.5s |
| `motor_send_home` | catalog gate; read tracked angle from `motor_rotation_store`; call `move_motor(-angle)`; clear angle tracking | falls through to `_do_move_motor` | falls through |
| `motor_set_zero` | catalog gate; clear angle tracking only | no-op (no hardware) | no-op |
| `optimize_component` | STORED gate; OPTIMIZING status; **constructs the `progress_callback` that serializes step counts / image basenames into `current_state` under the state lock**; post-success commit (last_optimization_score, last_optimized_pose, placement.mode); status flip back to IDLE | strategy class construction; output-dir setup; **invokes `progress_callback` to stream live updates — never touches `current_state` directly**; `experiment.optimize_component(comp, strategy)`; monitor thread | synthetic image generation + sleep + fake score; calls `progress_callback` to drive UI updates same as real |
| `remove_component` | catalog gate; remove from current_state | no hardware step | no hardware step |
| `add_component_to_state` | catalog gate; insert into current_state with default tunables/measurables | no hardware step | no hardware step |
| `confirm_holding_tag` | HOLDING_UNCONFIRMED gate; clear `requires_operator_confirm` | no hardware step | no hardware step |
| `observe_measurables_for_tag` | catalog gate; commit observed pose | trigger camera read via `lab_automation` | return current measurables verbatim |

Notable cases:

- **`store_component`, `place_from_storage`, `repack_storage_slot`,
  `recenter_stored_in_inventory`** all reduce to `_do_move` + slot
  bookkeeping. After the refactor, they become thin wrappers around
  `move_component` orchestration. This is a real cleanup — today
  they each have their own copy of move-with-some-state-twist logic.
- **`motor_send_home`** is interesting: it's already a macro in the
  primitive registry (`PrimitiveId.MOTOR_SEND_HOME` is a macro that
  expands to `MOVE_MOTOR(-tracked_angle)`). The base orchestrator can
  collapse it into a `move_motor` call directly.
- **`optimize_component`** has the smallest shared surface and the
  largest `_do_*`. We're not pretending the hook is small here — it
  legitimately has ~150 lines of strategy construction and monitor
  setup. The shared part is worth keeping for status-lifecycle
  consistency, but we should not contort the hook to make it "fit"
  some smaller signature.

### 6.1 Hook signatures — per-primitive return types

We deliberately do **not** unify the `_do_*` hooks on a single return
type. Forcing every primitive to return `Optional[LabPose]` would mean
`_do_pick` fabricating mathematically meaningless x/y/rotation values
just to surface its only real result (the settled z_lab). A catch-all
`PrimitiveResult(optional_pose, optional_z, optional_score, ...)`
dataclass would push the same complexity onto every reader of every
hook. Instead, each hook returns exactly what its orchestrator needs,
and the shared committer for that primitive knows the shape.

The full table:

| Hook | Return type | Notes |
|---|---|---|
| `_do_pick` | `float` | The settled z_lab (mm). Real asks `_intent_hover_z_lab`; mock returns `DEFAULT_HOVER_Z_MM`. |
| `_do_hover` | `Optional[LabPose]` | Actual pose achieved. `None` means "use commanded pose verbatim". Real returns `None`; mock returns commanded + small noise. |
| `_do_place_from_hover` | `None` | Pose is fully determined by orchestrator inputs; nothing to surface. |
| `_do_scan_rotate` | `None` | Final rotation is the commanded `theta_max`. |
| `_do_move` | `Optional[LabPose]` | Same shape as `_do_hover`; mock adds noise. |
| `_do_move_motor` | `None` | Angle delta is bookkeeping in `motor_rotation_store`. |
| `_do_motor_send_home` | `None` | Macro: orchestrator delegates to `_do_move_motor(-tracked_angle)`. |
| `_do_motor_set_zero` | `None` | No hardware. |
| `_do_optimize` | `None` | All progress flows through `progress_callback`; final score is committed by orchestrator after the hook returns. |
| `_do_remove` | `None` | Catalog only. |
| `_do_add_to_state` | `None` | Catalog only. |
| `_do_affirm_placed_at_current` | `None` | Real writes `comp.is_placed = True` (the legitimate Stage C6 exemption). |
| `_do_observe_measurables` | `Optional[LabPose]` | Real triggers a camera read and returns the observed pose; mock returns `None` (use saved measurables). |
| `_do_confirm_holding_tag` | `None` | Pure state-flag clear. |

`_do_store_component`, `_do_place_from_storage`, `_do_repack_storage_slot`,
`_do_recenter_stored_in_inventory` reduce to wrappers around `_do_move`
(see "Notable cases" above) — they don't get their own hooks.

### 6.2 Long-running hooks: the `progress_callback` rule

`_do_optimize` (and any future long-running hook like a
`_do_calibrate`) takes a `progress_callback: ProgressCallback`
parameter. The hook MUST NOT mutate `self.current_state` directly.
Instead it pumps progress events through the callback, which is
constructed by the orchestrator and serializes writes through the
state lock owned by `base.py`. Pseudocode:

```python
# In base.py
async def optimize_component(self, target_id, strategy_name, params):
    # ... refusals, status flip ...
    def progress_callback(event: ProgressEvent) -> None:
        with self._state_lock:
            if event.kind == "step":
                self.current_state["optimization_step"] = event.step
            elif event.kind == "image":
                self._last_optimization_image_basename = event.basename
            # ... other events ...
    try:
        await self._do_optimize(target_id, strategy_name, params, progress_callback)
        # ... commit final score ...
    finally:
        # ... status flip back, optimization_step = 0, etc ...
```

This preserves the invariant **"the hook never touches state directly"**
without forcing the optimizer to return control mid-run. The pattern
generalizes to any future long-running primitive that needs to stream
intermediate values to the UI.

`ProgressEvent` lives in `shared/progress.py` as a small tagged-union
dataclass (`Step`, `Image`, `PartialScore`, `Cancel`). Both backends
construct the same events; only the call sites differ.

---

## 7. Feasibility — is this just moving things around?

Honest answer: **Phase 1 is mostly moving things around. Phase 2 is
not — it deletes duplication.**

### 7.1 Phase 1 = relocation, no behavior change

Phase 1 creates the folder structure and moves pure-helper modules
(coordinate frames, storage intent, motor state injection, catalog
lookup) into their target homes. Primitive methods stay in their
current backend's `communicator.py` — same code, just relocated.

That's pure code-mover work. No semantic change. The only risks are:

- **Import path stability.** External callers (e.g.
  `backend/main.py`, frontend tests) currently do
  `from lab_communicator.real import RealLabCommunicator`. Phase 1
  preserves that via `lab_communicator/real/__init__.py`. Easy.
- **Stage C lint regex.** `backend/tests/test_lab_primitives.py::
  StageCInvariantsTests` currently parses
  `backend/lab_communicator/real.py`. After Phase 1 the file is
  `backend/lab_communicator/real/communicator.py`. One-line update.
- **Documentation cross-references.** `fixing.md`, `bugs.md`,
  `new_primitives.md`, `labautomation_new_primitives.md` reference
  line numbers in `real.py`. We can either leave the line numbers
  stale (they were already drifting before this refactor) or sweep
  them at the end of Phase 1. I suggest a brief sweep — replace
  brittle line numbers with logical anchors ("the in-air manipulation
  block in `real/communicator.py`") wherever practical.

Phase 1 risk is low. ~1 day of work. Touches ~6 files. Should ship as a
single PR with the test suite passing unchanged.

### 7.2 Phase 2 = real abstraction; can introduce bugs

Phase 2 introduces three new abstractions, each of which can leak:

#### Abstraction 1 — `_apply_loaded_pose_to_hardware(comp, lab_pose)`

Replaces the cross-wall write inside `set_lab_state`. **Risk:** if the
hook signature is wrong, snapshot loading breaks. Specifically the
existing code transforms XY+Z+yaw and constructs a `Pose(roll=180,
pitch=0)`; the hook needs to receive enough information to do that
faithfully. Signature-wise, `(comp, lab_pose)` should suffice, where
`lab_pose` is a small dataclass `LabPose(x, y, z, rotation)`. The
real hook does the transform; the mock hook is a no-op. Mitigation:
ship a regression test that loads a snapshot in real mode and verifies
`comp.current_location.x/y/z/yaw` end up at the expected values.
(Currently deferred per `fixing.md` Stage A6 — this refactor unblocks
it.)

#### Abstraction 2 — primitive orchestrator + `_do_*` hook

The risk concentrates around the hook's return type. Each primitive's
hook can return either:

- nothing (place_from_hover, move, scan_rotate),
- the actual settled pose (mock returns commanded + noise; real returns
  commanded — there's no top-camera read to update measurables), or
- a single value like `settled_z_lab` (pick).

**Decision: per-primitive return types, not a uniform shape.** A
uniform `Optional[LabPose]` looks tidy on paper but breaks down on the
first hook that doesn't fit. `_do_pick` is the obvious counter-example:
it cares about the settled z_lab and nothing else. Forcing it to
construct a `LabPose(x=0, y=0, z=settled_z, rotation=0)` would make the
return type lie — readers would assume the x/y/rotation fields are
meaningful and write code against them, eventually shipping a bug. A
catch-all `PrimitiveResult(optional_pose=..., optional_z=..., ...)`
dataclass pushes the same complexity onto every hook caller. Per-
primitive return types are cheap (the orchestrators already know which
hook they're calling) and they keep the type system honest. See §6.1
for the full table of hook return types.

Two specific shapes worth flagging:

- **`Optional[LabPose]`**: `_do_hover`, `_do_move`,
  `_do_observe_measurables`. `None` means "use commanded pose
  verbatim"; a value means "this is what the lab actually achieved".
  The shared committer for each primitive picks one or the other.
- **`float`**: `_do_pick` returns the settled z_lab in mm. Mock returns
  `DEFAULT_HOVER_Z_MM`; real asks `_intent_hover_z_lab`.

This is a small behavior change in mock (today mock writes commanded
+ noise to `measurables.pose` directly inside `pick_component`; under
the new shape mock surfaces commanded + noise as the hook return value
and the shared committer writes it). No change in real.

#### Abstraction 2.5 — `progress_callback` for long-running hooks

`_do_optimize` is the first hook that runs for minutes and needs to
push intermediate updates to the UI mid-run. **Risk:** if the hook
mutates `self.current_state` directly, we either break the "base owns
the state" rule (and have to remember the lock everywhere) or we
deadlock (the orchestrator is holding the lock waiting for the hook
which is trying to acquire the lock to write progress).

**Decision:** the hook never touches state directly. The orchestrator
constructs a `progress_callback: ProgressCallback` closure that
captures `self._state_lock` and `self.current_state`, and passes it
into the hook. The hook calls the callback to publish step counts,
image basenames, partial scores, etc. The callback is the single
permitted side channel.

This keeps the lock owned by base, keeps `current_state` mutations in
one file (base.py), and gives the hardware a clean pipeline to the UI
poll loop. The pattern generalizes — any future long-running hook
(e.g. a calibration sweep) gets the same treatment. See §6.2 for the
sketch.

#### Abstraction 3 — state-machine refusal helpers

`shared/state_machine.py` will own functions like
`refuse_if_holding(state) -> bool`, `refuse_if_stored(state, tag_id)
-> bool`, `refuse_if_in_storage_quadrant(state, tag_id, x, y) ->
bool`. **Risk:** subtle edge cases where mock and real differ (e.g.,
mock allows pick on a part with `is_placed=False` but real doesn't).
Mitigation: a careful audit of the existing refusal logic per
primitive, side-by-side mock vs real, before extracting.

### 7.3 Bugs we should expect during Phase 2

Concrete regressions to watch for (each is testable):

1. **Snapshot load on a hover state.** Currently exercised after Stage A.
   The new hook needs to keep z_lab forward-transformed (Stage A's
   teach-and-repeat property). Easy to break if the orchestrator or
   the hook drops the z field somewhere.
2. **Holding state during scan_rotate (held mode).** Today the system
   stays HOLDING during the scan. The shared orchestrator must thread
   this through (don't flip to BUSY → IDLE; flip to BUSY → HOLDING).
3. **Cancel / failure paths.** Each primitive currently has its own
   `try/finally` to roll back `system_status`. The shared orchestrator
   needs to do this on the hook's behalf, and the hook should be able
   to signal "fatal failure, status should rollback to where it was
   before BUSY". Cleanly — propagate the exception and let base do the
   rollback.
4. **`HOVER_PLACEHOLDER_STATE` removal.** After Phase 2, mock IS the
   placeholder behavior. The flag becomes redundant. Easy to delete,
   but we should grep callers — a frontend test or a recipe script
   may reference it.
5. **Hook calls back into state directly during a long run.** Risk
   specific to `_do_optimize` (and any future long-running hook). If
   the hook bypasses `progress_callback` and tries to write directly
   into `self.current_state`, the result is either a violation of the
   "base owns the state" rule (subtle — works in tests, leaks in
   production once a second writer enters the picture) or, worse, a
   deadlock if the orchestrator is holding the lock. Mitigation: in
   Phase 2D, the architectural lint also flags any read/write of
   `self.current_state` inside a `_do_*` method body — that's a hard
   contract violation. Hooks should only see state through their input
   args and only write through `progress_callback`.
6. **Circular import trap in `shared/`.** Risk: a `shared/` helper
   wants type hints on its arguments and naively does `from
   lab_communicator.base import LabCommunicator`. Because `base.py`
   imports from `shared/`, this creates a cycle and crashes on import.
   Mitigation: the architectural lint added in Phase 1 (`shared/` may
   not import `lab_automation` OR the package's own `base.py`) catches
   this. Helpers that need to mutate communicator state take the
   specific primitives (`current_state: dict`, `lock: threading.Lock`,
   `catalog_map: dict`) — not the `LabCommunicator` instance. Type
   hints stay precise (`dict`, `Lock`) and the import graph stays
   acyclic.
7. **Mock state persistence semantics.** Today mock does
   `_read_state()` at the start of each primitive and `_write_state()`
   at the end. This is partly for crash recovery and partly for test
   isolation. After Phase 2, mock could hold state in memory like real
   and persist via a `_persist_state()` virtual hook called by the
   shared orchestrator's commit step. **Proposal:** keep the
   read-modify-write idiom in mock by overriding the hook to
   read-from-disk + write-to-disk wrappers around its `_do_*`. Less
   change, easier to verify.
8. **Optimization cancellation.** `optimize_component` today allows
   cancellation by checking `self._active_optimization_image_dir`.
   The hook needs to preserve this contract. Risk is medium because
   the cancellation logic is intertwined with the monitor task.
   `progress_callback` is also the natural place to surface a
   cancel signal back to the hook (a `Cancel` event the hook checks
   periodically), but the existing dir-sentinel mechanism is a fine
   fallback for Phase 2D.

### 7.4 What this does NOT solve

Things that are out of scope and we should be honest about:

- **`bugs.md` issues #1–#3** about `lab_automation` cross-wall
  semantics. Those are upstream `lab_automation` fixes; this refactor
  does not change them.
- **The deferred `move_component` rotation reconciliation** (`fixing.md`
  §3.1 / Stage A4). The `[-180, 0, -rot]` ad-hoc negation stays in
  place inside the new `_do_move` hook with the same comment. It can
  only be reconciled on a physical-robot test.
- **Test parallelization.** Mock currently shares a single
  `mock_lab_state.json` across tests, which forces serial execution.
  This refactor doesn't fix that (in-memory state per-instance would,
  but that's a separate concern).
- **The HTTP/Pydantic primitive surface.** `lab_primitives/` is
  unchanged. Same primitive IDs, same payload schemas, same registry.
  The only change is what the registry calls dispatch into.
- **A second physical lab.** This refactor unblocks adding one but does
  not include one. Adding a second backend is a separate PR.

---

## 8. Open questions to settle before/during implementation

These are decisions we should make explicitly so we don't drift mid-PR.
Each row records the default I lean toward and the resolution status.

| # | Question | Resolution | Rationale |
|---|---|---|---|
| Q1 | Hook naming: `_do_pick` vs `_pick_hardware` | **Resolved 2026-04-28: `_do_*`** | Shorter; standard Pythonic Template Method idiom (used heavily in `asyncio` and the stdlib). |
| Q2 | Hook return type uniform (always `LabPose | None`) vs per-primitive | **Resolved 2026-04-28: per-primitive** | Forcing `_do_pick` to return a `LabPose` would mean fabricating meaningless x/y/rotation values to surface its only real result (settled z_lab). A `PrimitiveResult` catch-all dataclass pushes the same complexity onto every reader. Per-primitive return types keep the type system honest. See §6.1 for the full table. |
| Q3 | Mock state: in-memory + persist hook, vs keep file-backed read-modify-write | **Resolved 2026-04-28: keep file-backed** | Smaller change, lower regression risk; over-engineering an in-memory mock is not justified right now. |
| Q4 | Should `shared/` import `lab_model`? | **Resolved 2026-04-28: yes** | `lab_model.holding`, `lab_model.component_model` are domain types; that's the correct dependency direction. |
| Q5 | Should `shared/` import `lab_primitives`? | **Resolved 2026-04-28: no** | Preserves the domain-vs-transport boundary; primitives talk to the communicator, not the other way around. |
| Q6 | `HOVER_PLACEHOLDER_STATE` — keep or delete? | **Resolved 2026-04-28: delete in Phase 2** | Mock is now the placeholder; flag is redundant. |
| Q7 | Phase 1 PR shape: one big move, or split per moved module? | **Resolved 2026-04-28: one PR** | Phase 1 is structural; reviewers verify CI passes. Multiple PRs add CI overhead for no value. |
| Q8 | Phase 2 PR shape: one PR per primitive, or grouped (motor / in-air / heavy / optimize)? | **Resolved 2026-04-28: grouped (4 PRs total)** | Grouping by category keeps reviewers in one mental model and makes semantic changes much easier to evaluate. |
| Q9 | Order Phase 2 with `bugs.md` resolution | bugs first, then refactor *(default; not yet locked)* | Refactoring during cross-wall debugging is high-risk. |
| Q10 | When do we tighten the Stage C lint? | **Resolved 2026-04-28: during Phase 2A** | Tied directly to the PR that introduces `_apply_loaded_pose_to_hardware`. The lint moves from "writes only in `set_lab_state`" to "writes only in `_apply_loaded_pose_to_hardware`". |
| Q11 | Backward-compat exports in `lab_communicator/__init__.py` | yes *(default; not yet locked)* | Avoid changing every import in `main.py` and elsewhere. |
| Q12 | Documentation line numbers across `*.md` files | one sweep at end of Phase 1 *(default; not yet locked)* | Not blocking; replace brittle line refs with logical anchors. |
| Q13 | New CI lint: forbid `lab_automation` imports inside `shared/` | **Resolved 2026-04-28: must have** | Cited as the single most important mechanism to keep the architecture from degrading back into a monolith. The same lint also forbids `shared/` from importing `lab_communicator.base` (the circular-import trap), per §5.1 rule 5. |

---

## 9. Out of scope for this refactor

To keep the scope honest:

- Adding a second physical-robot backend.
- Test parallelization improvements for mock.
- Changes to `lab_model/` or `lab_primitives/` — these are already in
  the right shape.
- Changes to the HTTP primitive surface, payload schemas, dispatch.
- Changes to frontend code (the API surface is unchanged).
- Changes to `lab_automation` itself — that has its own roadmap in
  `labautomation_new_primitives.md` and `bugs.md`.
- Resolving the deferred `[-180, 0, -rot]` rotation convention in
  `move_component`. It travels with the function unchanged.

---

## 10. Roadmap

> **Status (2026-04-29):** Phase 1, Phase 2A, Phase 2B, Phase 2C, and
> Phase 2D have all shipped. The template-method migration is
> complete: every primitive lives on `LabCommunicator` with a tiny
> `_do_*` hook on each backend. Phase 3 (docs sweep + final
> consolidated lint) is the remaining work.

### Phase 0 — prerequisites *(blocked on `bugs.md` resolution)*

Land the three bug fixes from `bugs.md`:
- **Fix #1**: `is_placed` not set after table scan.
- **Fix #2**: Hover / place-from-hover sign mismatch with pick & place.
- **Fix #3**: Pick / scan-rotate `target_loc` reconciliation.

These are upstream `lab_automation` fixes (and one cloud-labs follow-up
in `real.py`). Refactoring before they land means we're moving moving
targets. Once they're stable, Phase 1 can begin.

### Phase 1 — folder restructure, no behavior change *(SHIPPED)*

Mechanical code-move. No primitive logic touched.

#### P1.1 — Create folders + `__init__.py` files
- `backend/lab_communicator/{base.py,shared/,real/,mock/}` skeleton.
- `__init__.py` re-exports for backward compat:
  - `lab_communicator/__init__.py` re-exports
    `LabCommunicator`, `RealLabCommunicator`, `MockLabCommunicator`.
  - `lab_communicator/real/__init__.py` exports `RealLabCommunicator`.
  - `lab_communicator/mock/__init__.py` exports `MockLabCommunicator`.

#### P1.2 — Move pure helpers into `shared/`
- `shared/util.py` ← `_env_float`, `_optional_float`,
  `_tag_id_for_component`.
- `shared/catalog_lookup.py` ← `_catalog_wh`, `_component_height_mm`,
  `_motor_catalog_ok`.
- `shared/motor_state.py` ← `_inject_motor_rotations_into_state`
  (deduplicated from both backends).
- `shared/storage_intent.py` ← `StorageIntentStore` class wrapping the
  8-method cluster.
- `shared/placement_ui.py` ← `_apply_placement_ui_phase`,
  `_ui_pose_for_placement_tick`.

#### P1.3 — Move real-specific code into `real/`
- `real/coordinate_frames.py` ← `lab_table_xy_to_robot_xy`,
  `robot_table_xy_to_lab_xy`, `lab_rotation_to_robot_yaw`,
  `robot_yaw_to_lab_rotation`, `_z_lab_to_robot`, `_z_robot_to_lab`,
  `_intent_hover_z_lab`, calibration constants
  (`TABLE_Z0_ROBOT_MM`, `GRASP_OFFSET_MM`,
  `LAB_ROBOT_TABLE_ROTATION_RAD`, `MAX_SAFE_HOVER_Z_LAB_MM`).
- `real/gripper.py` ← `get_gripper_status`, `_reconcile_holding_on_boot`.
- `real/optimization.py` ← all 12 optimization helpers + Cobyla
  reference methods + `_install_cloudlab_place_ui_hook`,
  `_remove_cloudlab_place_ui_hook`, `_cloudlab_progress_callback`.
- `real/video.py` ← recorder process methods, video stream methods,
  `capture_table_cam`, `_camera_images_base_dir`.
- `real/scan.py` ← `_initialize_state`, `refresh_pose_from_camera`.
- `real/communicator.py` ← what's left of `real.py` (the constructor,
  the 18 primitives, `set_lab_state`, `get_lab_state`).

#### P1.4 — Move mock-specific code into `mock/`
- `mock/persistence.py` ← `_read_state`, `_write_state`,
  `_ensure_state`, `_load_catalog`.
- `mock/coordinate_frames.py` ← identity transforms (no calibration).
- `mock/communicator.py` ← what's left.

#### P1.5 — Update Stage C lint paths
- `backend/tests/test_lab_primitives.py::StageCInvariantsTests` updates
  to read `real/communicator.py` instead of `real.py`.

#### P1.6 — Sweep documentation line numbers
- `fixing.md`, `bugs.md`, `new_primitives.md` get a quick pass to
  replace brittle line numbers with logical anchors.

#### P1 acceptance criteria
- [ ] All 9 existing tests in `test_lab_primitives.py` pass.
- [ ] Backend boots in mock mode (`uvicorn backend.main:app`).
- [ ] Backend boots in real mode (with `lab_automation` available).
- [ ] Frontend connects, lab state polls, primitives dispatch correctly.
- [ ] No new linter errors.
- [ ] `from lab_communicator.real import RealLabCommunicator` still
  works (and any other existing imports outside the package).

### Phase 2 — template-method migration *(SHIPPED)*

Each PR refactored one group of primitives. After each PR, mock + real
both work and tests pass.

#### Phase 2A — Base infrastructure + motor primitives *(SHIPPED)*
*Foundation. Smallest hardware footprint.*

- Promote `LabCommunicator` to a concrete template class:
  - Move `current_state`, `_state_lock`, `catalog_map`,
    `storage_intent` ownership from subclasses to base.
  - Add `set_status(s)`, `set_holding(...)`, `clear_holding()` methods.
  - Add the `_apply_loaded_pose_to_hardware(comp, lab_pose)` virtual
    and migrate `set_lab_state` to use it.
  - Mock implements `_apply_loaded_pose_to_hardware` as no-op.
  - Real implements with the existing XY/Z/yaw transform.
- Add `shared/state_machine.py` with refusal helpers.
- Add `shared/snapshot.py` with the `apply_snapshot(state, hook)`
  function.
- Refactor `move_motor`, `motor_send_home`, `motor_set_zero` into
  template methods. Tighten Stage C lint to "writes to
  `current_location` only inside `_apply_loaded_pose_to_hardware`".
- Add CI lint forbidding (a) `lab_automation` imports in `shared/`,
  (b) `lab_communicator.base` imports in `shared/` (the circular-
  import trap from §5.1 rule 5), (c) `self.current_state` access
  inside any `_do_*` method body in `real/` or `mock/` (hooks must use
  inputs and `progress_callback` only), and (d) cross-backend imports
  (`real/` ↔ `mock/`).

**Acceptance:** all 9 tests pass; mock + real both boot; the new lint
rejects synthetic violations; existing motor-related primitives work
end-to-end.

#### Phase 2B — In-air primitives *(SHIPPED)*
*Highest-value cleanup; recently shipped so well-tested.*

- Refactor `pick_component`, `hover_component`, `place_from_hover`,
  `scan_rotate_in_place` into template methods.
- Each primitive: extract refusal logic into `shared/state_machine.py`;
  extract commit logic into a `shared/commits.py` (or inline in base);
  reduce real and mock implementations to `_do_*` hooks of ~10 lines
  each.
- Delete `HOVER_PLACEHOLDER_STATE` and the placeholder branch from
  `real/communicator.py`. Mock now serves the same purpose.
- Add a regression test for each primitive: parameterize over mock/real
  (real test mocks `experiment.*_cloudlab` calls).

**Acceptance:** existing in-air UX is byte-identical (status
transitions, holding field, tunables/measurables writes, golden state
snapshots); the placeholder mode is gone; new tests cover each
primitive's hook contract.

#### Phase 2C — Heavy state primitives *(SHIPPED)*
*Most code volume; lowest novelty.*

- Refactor `move_component`, `place_from_storage`, `store_component`,
  `repack_storage_slot`, `recenter_stored_in_inventory`,
  `affirm_placed_at_current`, `add_component_to_state`,
  `remove_component`, `confirm_holding_tag`,
  `observe_measurables_for_tag` into template methods.
- Watch for the "shared `_do_move` wrapper" pattern emerging across
  store-related primitives — they should reduce to thin wrappers around
  `move_component` orchestration + storage-intent updates.
- The deferred `[-180, 0, -rot]` rotation in `move_component` travels
  unchanged into `real/_do_move`.

**Acceptance:** all storage / move primitives behave identically;
storage-intent JSON file format unchanged; stored-intent rebuild on
snapshot load still works.

#### Phase 2D — Optimization *(SHIPPED)*
*Most complex hook; saved for last so the pattern is well-established.*

Implemented with a deliberately simpler progress-callback shape than
the original `ProgressEvent` tagged-union sketch -- the only stepwise
update primitive `_do_optimize` actually publishes is "iteration N
landed", so `progress_callback(*, step: int)` was sufficient. Image-
basename updates continue to flow through `monitor_optimization_dir`
(real's PNG file watcher), and per-component pose updates during a
Newton run continue to flow through `cloudlab_progress_callback` /
`apply_placement_ui_phase` (already shared, lock-aware). Cancellation
remains tied to the dir-sentinel; routing it through a `Cancel` event
stayed deferred because the existing path works.

What shipped:

- `LabCommunicator.optimize_component` orchestrator on `base.py`:
  catalog gate, STORED refusal, BUSY-equivalent OPTIMIZING flip,
  `optimization_step` reset, `optimization_run_dir` write, hook
  dispatch, post-run commit via `commit_optimization_complete`,
  status reset in `finally`.
- New virtual hooks: `_prepare_optimization_run(target_id,
  strategy_name) -> Optional[str]` (real builds the per-run images
  subdir; mock returns `None`) and `_finalize_optimization_run()`
  (real removes the Newton place-UI hook and clears
  `_active_optimization_image_dir`; mock no-op).
- `_do_optimize(*, target_id, strategy_name, params,
  progress_callback) -> Optional[Dict]`. Real builds the strategy
  (NEWTON or COBYLA), wires `cloudlab_progress_callback`, dispatches
  `experiment.optimize_component` on a worker thread, returns
  `{"score": 1.0, "final_pose": None}` so the orchestrator copies
  the live `measurables.pose` into `last_optimized_pose`. Mock loops
  six step ticks via `progress_callback(step=k)` and returns
  `{"score": 0.99, "final_pose": ...}` with a small Gaussian
  rotation drift.
- `commit_optimization_complete` in `shared/commits.py` writes
  `tunables.placement.mode`, `measurables.last_optimization_score`,
  and `measurables.last_optimized_pose` (and updates
  `measurables.pose` when the hook returned an explicit final pose).
- Three regression tests under
  `OptimizePrimitiveMockRoundTripTests`: round-trip commit, refusal
  on STORED parts, refusal on unknown tags.

### Phase 3 — Cleanup and documentation *(remaining)*

The architectural lints from this phase already shipped alongside
Phase 2A (`CommunicatorArchitectureLints` in
`backend/tests/test_lab_primitives.py` -- 4 lints) and Phase 2C
(`StageCInvariantsTests::test_is_placed_writes_only_in_allowed_sites`
re-pointed to `_apply_is_placed_flag`). What's left is a docs pass:

- Update `new_primitives.md`, `fixing.md`, `bugs.md`,
  `labautomation_new_primitives.md` to reference the new file
  structure (replace any remaining `real.py` / `mock.py` mentions
  with `lab_communicator/{real,mock}/communicator.py`).
- Add a short `lab_communicator/README.md` describing the package
  layout and how to add a new backend.
- Sweep for any straggler `affirm_placed_at_current` /
  `set_lab_state` lint references in older notes (the canonical
  `is_placed` write site is now `_apply_is_placed_flag`).

Architectural lints already in place:

- `shared/` files don't import `lab_automation`, `real`, `mock`, or
  `lab_communicator.base` (the four cross-cutting bans).
- `real/` and `mock/` don't import each other.
- `base.py` doesn't import `real/` or `mock/`.
- No `_do_*` method body in `real/` or `mock/` reads or writes
  `self.current_state` (use orchestrator inputs and `progress_callback`).
- `current_location` writes only inside `_apply_loaded_pose_to_hardware`.
- `is_placed` writes only inside `_apply_loaded_pose_to_hardware`
  and `_apply_is_placed_flag`.

---

## 11. Acceptance criteria for the whole refactor

A reader looking at the post-refactor codebase should be able to:

1. Open `real/communicator.py` and answer "what does primitive X do at
   the lab level?" in under 30 seconds. (Today: minutes.)
2. Add a new robot backend by creating one folder
   (`lab_communicator/robotb/`) with their own `coordinate_frames.py`,
   `gripper.py`, `optimization.py`, `video.py`, `scan.py`,
   `communicator.py` — and zero edits anywhere else.
3. Trace the flow of a primitive request from HTTP → dispatch → base
   orchestrator → backend hook → `lab_automation` call (or mock sleep)
   → state commit, by reading at most three files.
4. Verify the Stage C invariants (no `inventory_location` access; no
   `current_location` writes outside the snapshot hook;
   no `is_placed` writes outside the snapshot hook or
   `affirm_placed_at_current`) by reading the architectural rule lint,
   not by manually scanning a 2400-line file.

---

## 12. What we still don't know

A list of "we'll learn this during implementation":

- **Exact shared signature for `_do_optimize`.** The hook is fat
  (~150 lines) and we may discover the orchestrator can be even
  thinner than estimated, OR we may discover that more of the
  optimization runtime should leak into base. Defer the decision
  until Phase 2D.
- **Whether `place_from_storage` / `store_component` /
  `repack_storage_slot` / `recenter_stored_in_inventory` truly reduce
  to `_do_move` wrappers.** They look like they should but the storage
  intent updates around them may resist the pattern. If so, they get
  their own hooks with no shame.
- **How aggressively to dedupe the gripper-status code in mock.** Today
  mock has its own simulated `get_gripper_status` and
  `_reconcile_holding_on_boot`. There may be a small `shared/gripper_
  protocol.py` that defines the shape, or it may not be worth the
  abstraction for two implementations.
- **Whether `set_lab_state`'s snapshot-merge logic can move entirely to
  `shared/snapshot.py`.** Today it lives inside `RealLabCommunicator`;
  mock has its own copy. If they're truly equivalent (modulo the
  hardware apply hook), they can collapse. If there's subtle behavior
  difference, base + hook is the right shape but the merge stays in
  base.

---

## 13. Document history

- **2026-04-27 (initial draft)** — full inventory of `real.py` (~70
  methods) categorized into shared / real-specific / split. Folder
  layout proposed mirroring `lab_model/` and `lab_primitives/`.
  Per-primitive split table for all 18 user-facing primitives.
  Feasibility analysis identifies three abstractions
  (`_apply_loaded_pose_to_hardware`, `_do_*` hooks,
  state-machine refusal helpers) and six likely regressions during
  Phase 2. Roadmap split into Phase 0 (depend on `bugs.md`), Phase 1
  (folder restructure, no behavior change, ~1 day), Phase 2 (template-
  method migration, 4 PRs by primitive group, ~1 week), Phase 3
  (cleanup + architectural lint). Open questions Q1–Q13 listed with
  defaults.
- **2026-04-28 (review pass — three traps + Q1–Q13 sign-off)** —
  Reviewer flagged three concrete traps in the initial draft. All
  three corrected:
  - **Trap 1 (uniform return type).** Q2 default flipped from
    "uniform `LabPose | None`" to "per-primitive". Forcing `_do_pick`
    to return a `LabPose` would mean fabricating mathematically
    meaningless x/y/rotation values to surface the only field it
    cares about (settled z_lab); a catch-all `PrimitiveResult`
    dataclass pushes the same complexity onto every reader. Added
    §6.1 with the full per-hook return-type table. §7.2 abstraction 2
    rewritten to lock the per-primitive policy.
  - **Trap 2 (state locking during long hooks).** Added §6.2
    "`progress_callback` for long-running hooks" and §7.2 abstraction
    2.5. `_do_optimize` (and any future long-running hook) takes a
    `progress_callback` parameter; the hook NEVER touches
    `self.current_state` directly. Phase 2D updated to add
    `shared/progress.py` with `ProgressEvent` and the
    `ProgressCallback` alias, and to gate the lint on `_do_*`-body
    state access. New §7.3 entry #5 makes this an explicit
    expected-bug.
  - **Trap 3 (circular import in `shared/`).** Added §5.1 rule 5
    forbidding `shared/` from importing `lab_communicator.base`,
    even `TYPE_CHECKING`-gated. Helpers that need to mutate state
    take primitives (`current_state: dict`, `lock: threading.Lock`,
    etc.), not the `LabCommunicator` instance. New §7.3 entry #6
    documents the trap. Phase 2A and Phase 3 lint scopes expanded to
    enforce this, alongside the `lab_automation` ban (Q13) and the
    `_do_*`-body state-access ban (Trap 2).
  - Q1, Q3, Q4, Q5, Q6, Q7, Q8, Q10, Q13 marked **Resolved 2026-04-28**
    in the §8 table with reviewer rationale inline. Q2 flipped and
    locked. Q9, Q11, Q12 still defaults (not yet locked).

---

## 11. Phase 4 — `primitives.py`-per-backend split *(SHIPPED 2026-04-29)*

After Phase 3, a reader looking for "what does the real backend
actually call when the UI asks for `pick_component`?" still had to
scroll through ~1200 lines of `real/communicator.py` because the
`_do_pick` body sat in the middle of the file alongside `__init__`,
state-side virtual hooks, helper methods, and the video / cobyla
extras. The hook was tightly written but the file structure still
mixed three audiences: "what's a backend's checklist?", "what state
glue does this backend need?", and "what's the API call for each
primitive?".

Phase 4 extracts the third audience into a dedicated file:

```text
backend/lab_communicator/
├── base.py                    # template-method LabCommunicator (orchestrators)
├── shared/                    # cross-lab building blocks
├── real/
│   ├── communicator.py        # __init__ + state-side hooks + 1-line _primitive_* delegations
│   ├── primitives.py          # NEW: primitive_<name>(communicator, ...) free functions
│   ├── coordinate_frames.py
│   ├── gripper.py
│   ├── optimization.py
│   ├── scan.py
│   └── video.py
└── mock/
    ├── communicator.py        # NEW shape: __init__ + state-side hooks + delegations
    ├── primitives.py          # NEW: simulated hardware steps
    ├── persistence.py
    └── ...
```

### 11.1 The class hook → free function split

Two parallel naming conventions, with one-for-one mapping:

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

The renames are mechanical (`_do_X` → `_primitive_<descriptive>`); the
semantic change is the body of each `_primitive_*` method becoming a
single-line delegation:

```python
async def _primitive_pick_component(
    self, target_id: str, commanded: LabPose, params: Dict[str, Any]
) -> float:
    from lab_communicator.real.primitives import primitive_pick_component
    return await primitive_pick_component(self, target_id, commanded, params)
```

`primitives.py` then carries the actual `lab_automation` call:

```python
async def primitive_pick_component(
    communicator: "RealLabCommunicator",
    target_id: str,
    commanded: LabPose,
    params: Dict[str, Any],
) -> float:
    """Hardware step: pick_component_cloudlab + intent hover-z lookup."""
    comp = communicator.component_map.get(target_id)
    if not comp or not comp.current_location:
        raise RuntimeError(...)
    safe_z = optional_float(params, "safe_z")
    await asyncio.to_thread(
        communicator.experiment.pick_component_cloudlab,
        comp,
        safe_z=safe_z,
    )
    return communicator._intent_hover_z_lab(target_id)
```

### 11.2 Two audiences, two files

- **`communicator.py` answers "what does this backend implement?"**
  Reading it top-to-bottom is the new-backend checklist: `__init__`,
  the state-side virtual hooks (`_apply_loaded_pose_to_hardware`,
  `_post_apply_snapshot`, `_persist_state`, `_after_move_to_storage`,
  `_after_move_out_of_storage`, `_apply_is_placed_flag`), the full
  list of `_primitive_*` hook delegations, and any backend-specific
  UI methods (`get_video_stream`, `capture_table_cam`,
  `get_cobyla_reference_status`, ...).
- **`primitives.py` answers "what's the API call for each primitive?"**
  Reading it top-to-bottom is the cross-wall contract: each
  `primitive_<name>` function shows the exact `lab_automation`
  function it calls (or, for mock, the simulated step it performs),
  the kwargs it builds, the worker-thread dispatch (`asyncio.to_thread`),
  and the return-shape the orchestrator is expecting.

### 11.3 Architectural lint update

`backend/tests/test_lab_primitives.py` gains a second invariant on
top of the one from §7.2 abstraction 2.5:

- The original lint (`test_primitive_hooks_do_not_touch_current_state`)
  scans `_primitive_*` *class methods* in `real/communicator.py` and
  `mock/communicator.py` for `self.current_state` access. Post-Phase-4
  these are all 1-line delegations, so the lint is now a regression
  guard against future inlining.
- The new lint (`test_primitive_free_functions_do_not_touch_current_state`)
  scans `primitive_*` *free functions* in `real/primitives.py` and
  `mock/primitives.py` for `communicator.current_state` (the new
  through-arg form) and `self.current_state`. This is the
  architecturally meaningful one going forward — the hook bodies live
  in `primitives.py`, so that's where the state-purity invariant has
  to be enforced.

### 11.4 Delivered impact

```text
                       pre-P4    post-P4   Δ
real/communicator.py   52.2 KB   36.2 KB   −16 KB (−31%)
mock/communicator.py   33.4 KB   21.0 KB   −12 KB (−37%)
real/primitives.py        —      21.6 KB   (new, 11 functions)
mock/primitives.py        —      13.5 KB   (new,  9 functions)
```

Plus a latent-bug cleanup: `mock/communicator.py` carried a stale
`scan_rotate_in_place` override (Phase 2B residue using the old
`_read_state` / `_write_state` pattern) that shadowed the base
orchestrator. Deleted as part of P4 — mock now goes through the
template-method path like every other primitive.

The full test suite (31 tests, including round-trip regressions for
every primitive) still passes after the rename.
