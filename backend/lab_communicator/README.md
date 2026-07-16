# LabCommunicator — edge bridge (intent → physics)

This package is the **Edge** face of Cloud Labs: it turns validated **primitives**
into instrument calls (or a faithful simulation) and keeps the live **runtime**
lab state the Twin polls. The Client SDK and Twin never import drivers. The
Coordinator (`backend/main.py`) only needs *some* communicator that implements
the shared contract.

> **Status of this document (2026-07):** Written to separate **original intent**,
> **current requirements** after measurable tensors and kernels, **what mock /
> real / MuJoCo actually do today**, and **what is outdated or incomplete**.
> The long-term shape of “communicators” may change; this README is the clear
> picture *before* that redesign.

**Related:** [`../lab_model/primitives/README.md`](../lab_model/primitives/README.md),
[`../lab_model/ARCHITECTURE.md`](../lab_model/ARCHITECTURE.md),
Wiki Learn → *OPU and three faces*, *Primitives*, *Kernels*, *Version control*.

---

## 1. How it was supposed to work (original contract)

The original design is still the right mental model for the *role* of this
package:

```text
  Client / Twin                 Coordinator                    Edge
       │                             │                           │
       │  POST /api/command          │                           │
       │  { MOVE_COMPONENT … }       │                           │
       │ ───────────────────────────►│  lease + validate         │
       │                             │  (proxy if edge attached) │
       │                             │ ─────────────────────────►│
       │                             │                           │ LabCommunicator
       │                             │                           │  · refuse if BUSY
       │                             │                           │  · call instrument hook
       │                             │                           │  · update lab_state
       │                             │ ◄── complete / state ─────│
       │ ◄── GET /api/lab-state ─────│                           │
```

**Intended split of labor:**

| Responsibility | Owner |
|----------------|--------|
| Validate command JSON, catalogs, kernels, jobs | `lab_model` + Coordinator |
| Own **live runtime** (poses, measurables, `system_status`) | `LabCommunicator` (+ `RuntimeManager`) |
| Implement each **primitive** against hardware or sim | Per-backend `primitives.py` / hooks |
| Configuration **version control** (commits, stash, pins) | `lab_model` ControlManager — *not* the communicator |
| HTTP / Twin UI | `backend/main.py` / `frontend/` |

**Intended implementation pattern** (still enforced):

1. `base.py` orchestrates busy checks, locks, status transitions, and calls into
   `lab_model.orchestration`.
2. Backends override `_primitive_*` hooks that **must not** read/write
   `current_state` directly; orchestrators commit through `lab_model.state`.
3. A **lab_view bundle** (`LAB_VIEW_PATH`) describes the bench (catalog, layout,
   lasers, recipes). Swap benches by pointing at another folder.

Mock and real were meant to expose the **same primitive vocabulary** upward so
Twin and scripts do not care which edge is attached.

That story did **not** originally include first-class **analysis tensors**, a
**kernel catalog**, or an in-process **ensemble OPTIMIZE** loop with TorchScript.
Those arrived later and changed what “implementing a communicator” means.

---

## 2. What changed: measurable tensors and kernels

The product now treats cameras and scorers as a data plane, not just “save a
PNG somewhere.”

### Wire vs analysis (cameras)

| Layer | Format | Who cares |
|-------|--------|-----------|
| **Wire / storage** in runtime measurables | PNG metadata (`path`, `cam_id`, `format: "png"`) | Twin preview, disk, HTTP |
| **Analysis** for kernels / scripts | BGR `uint8` H×W×3 (`bgr_hwc_uint8`) | `EVAL_KERNEL`, ensemble objectives, SDK resolve |

Shared helpers live in `lab_model.measurables` (`capture_png_for_tag`,
`read_camera_bgr_for_tag`, `observe_for_tag`). The communicator supplies
**capture bridges** (`capture_table_cam`, `capture_overhead_cam`,
`read_camera_bgr`); it should not invent a second tensor convention.

### RECORD_MEASURABLES

Orchestration is in `lab_model.orchestration.measurables_record`. The edge hook
`_primitive_record_measurables` should call into `observe_for_tag` and return
fresh measurable payloads; the orchestrator commits them. Stale frames are
nulled when motion/optimize starts (`_null_measurables_for_targets` in base).

### EVAL_KERNEL

Implemented on **`LabCommunicator` in `base.py`** as `eval_kernel_for_tag`:
capture BGR for the tag → run the kernel id through
`lab_model.optimization.kernels` (builtins / TorchScript / `session.*`).
This is an **authoring / probe** path (`probe_kernel` in the SDK). Mock may
fall back to a synthetic gray frame if capture fails; **real must not invent
frames**.

### OPTIMIZE (two paths)

| Path | When | Edge hook |
|------|------|-----------|
| **Ensemble (modern)** | `params.mode == "ensemble"` (default closed-loop story) | `_primitive_run_ensemble_optimization` → `mock/ensemble.py` or `real/ensemble.py` |
| **Legacy strategy** | NEWTON / COBYLA via `lab_automation` | `_primitive_prepare/optimize/finalize_component` |

Ensemble loops capture → score (including TorchScript kernels) → actuate
**inside** one OPTIMIZE so author-laptop round-trips do not dominate. Kernel
hooks (`apply_kernel_hooks`) can request camera materialization, measurable
sync, settle delays, etc. That is why a “modern” communicator is more than
move/place wrappers.

### What is *not* the communicator’s job

**ControlManager** (configuration commits, stash, hard checkout) lives in
`lab_model` and Coordinator APIs. Checkout **reconcile** eventually calls
primitives *through* this package, but the communicator does not own the git
graph. Session checkpoints (`shared/session_checkpoint.py`) are restart
recovery of runtime, also not VC.

---

## 3. Folder layout (current)

```text
backend/lab_communicator/
├── base.py                 ← template + RuntimeManager + eval_kernel_for_tag
├── runtime_mode.py         ← mock ↔ MuJoCo proxy (not the factory)
├── shared/                 ← bundle bootstrap, lasers, checkpoint, factory
├── mock/                   ← teaching / CI simulator + lab_view/ + ensemble
├── real/                   ← lab_automation bridge + lab_view/default/ + ensemble
└── mujoco/                 ← v1: MOVE_COMPONENT only (sidecar)
```

**Import rules:** `shared/` must not import concrete backends; `mock/` and
`real/` must not import each other; `lab_model` must not import this package.

**Factory** (`shared/communicator_factory.py`) registers **`mock`** and
**`real`**. MuJoCo is selected as a **runtime overlay** on mock via
`runtime_mode.py`, not as a third factory id.

---

## 4. What we currently have

### Mock — reference implementation (full teaching surface)

Mock is the contract that Twin, Wiki, and language scripts assume:

- Full primitive vocabulary with sleeps / noise / synthetic landscape
- JSON persistence under `mock/lab_view/`
- `RECORD_MEASURABLES` → `observe_for_tag` with synthetic cameras
- `EVAL_KERNEL` via base (synthetic BGR fallback if needed)
- **Ensemble OPTIMIZE** with pose + motor variables, TorchScript-friendly
  synthetic frames, invasive/touch-and-go paths in `mock/ensemble.py`
- Teleop / live-feed simulation, scan preview, session checkpoint

If you are unsure of signatures, **compare `mock/communicator.py` +
`mock/primitives.py` + `mock/ensemble.py`.**

### Real — production bridge (strong on hardware, partial on new data plane)

Real wraps `lab_automation` / `OpticalExperiment` when
`lab_automation_path` is valid.

**In good shape today:**

- Motion / place / pick / hover / motor moves against real controllers
- Table / overhead capture and MJPEG (`real/video.py`)
- Teleop bridge (`real/teleop_bridge.py`)
- Scan / pose refresh (`real/scan.py`) when helpers exist
- `RECORD_MEASURABLES` via shared `observe_for_tag` (real capture bridges)
- Legacy OPTIMIZE (NEWTON / COBYLA) with run directories and place-UI hooks
- **Ensemble OPTIMIZE v1** for **motor continuous variables** + live capture +
  measurable sync (`real/ensemble.py`)

**Incomplete or refuse-by-design relative to mock / modern requirements:**

| Area | Real today |
|------|------------|
| Ensemble **pose / invasive / gripper** variables | `NotImplementedError` in `real/ensemble.py` |
| Inventory add / reactivate from library | Refuses (scan-driven inventory; rescan) |
| `remove_component` | Effectively no-op (scan owns inventory) |
| `scan_rotate_in_place` | No-op if `lab_automation` helpers missing |
| `get_video_feed_status` | Stub / TODO — does not always reflect true camera health |
| `EVAL_KERNEL` | Inherits base — **depends on real BGR capture**; no gray fallback |
| MuJoCo-class breadth | N/A — real is not MuJoCo |

So: real is a **working robot/camera bridge** with a **motor-centric ensemble
v1**, not yet feature-parity with mock’s full closed-loop / invasive story.

### MuJoCo — narrow simulator overlay

- Only **`MOVE_COMPONENT`** is meaningfully overridden.
- Other primitives inherit base stubs (`NotImplementedError` / no-ops).
- Useful for kinematics demos; **not** a drop-in for kernels or ensemble OPTIMIZE.

---

## 5. What is outdated (do not trust without checking)

| Outdated idea | Current reality |
|---------------|-----------------|
| Communicator “owns lab state” as a raw dict only | Live state is gated by **`RuntimeManager`** in `base.py` |
| OPTIMIZE = one opaque `lab_automation` strategy call | Prefer **ensemble** + kernel hooks; legacy NEWTON/COBYLA still exists on real |
| Camera measurable = “whatever OpenCV returned” | **PNG on wire**, **BGR HWC uint8** for analysis / kernels |
| Kernels live inside the communicator | Kernel **catalog and TorchScript runtime** live in **`lab_model.optimization.kernels`**; edge only captures and invokes |
| Folder layout = mock + real only | **`mujoco/`** exists; factory still lists mock/real |
| “Phase 1: ensemble not implemented” comments in base | Mock and real both override `_primitive_run_ensemble_optimization` |
| `HOVER_PLACEHOLDER_STATE` env bypass (old real comments) | Removed; ignore leftover comment blocks |
| `lab_model/ARCHITECTURE.md` “RuntimeManager planned” | RuntimeManager is **wired** in the communicator; ControlManager is in `lab_model`, not here |
| Scaffold “replace all stub primitives” | Real primitives are largely filled; gaps are **ensemble invasive**, inventory policy, video status honesty |

---

## 6. Requirements for a modern communicator (checklist)

Use this before redesigning or adding a backend. Grounded in what Twin, SDK,
and `lab_model` already call.

### Must

1. **RuntimeManager** — all runtime mutations through the base lock / manager.
2. **`_primitive_*` hooks** for every primitive your catalog declares (or early
   refuse via `supports_primitive`).
3. **No state writes in `primitives.py`** — return results; let orchestrators commit.
4. **Camera data plane**
   - PNG capture for table/overhead tags
   - `read_camera_bgr(tag)` → analysis tensor for kernels
   - Live-feed / exposure hooks if the catalog exposes them
5. **RECORD_MEASURABLES** — delegate to `lab_model.measurables.observe_for_tag`.
6. **Null stale measurables** before BUSY / OPTIMIZING motion.
7. **EVAL_KERNEL** — inherit `eval_kernel_for_tag`; real edges must capture real
   frames (no synthetic fallback in production).
8. **Ensemble OPTIMIZE** — `_primitive_run_ensemble_optimization` with a backend that:
   - Applies continuous motor (and eventually pose) variables
   - Captures BGR / materializes `camera_image` when kernel hooks require it
   - Honors settle / sync_measurables / TorchScript flags from `apply_kernel_hooks`
9. **Register** in the factory (or document a runtime overlay like MuJoCo).
10. **lab_view bundle** via `get_lab_view_paths()` — no hardcoded catalog paths.

### Should (parity with mock / product story)

- Teleop prepare / live-feed start-end against real hardware
- Legacy OPTIMIZE prepare/finalize if the bench still uses NEWTON/COBYLA UI
- Session checkpoint when the manifest enables it
- Honest video / camera health in `get_video_feed_status`
- Invasive / touch-and-go ensemble blocks if objectives need gripper moves

### Must not

- Own ControlManager / commit DAG (Coordinator + `lab_model`)
- Define kernel ids or TorchScript loading rules (kernel registry owns that)
- Treat PNG bytes as the analysis type for kernels
- Cross-import mock ↔ real

---

## 7. Lab deployment bundle (`LAB_VIEW_PATH`)

Before any communicator starts, the coordinator bootstraps a **bundle** for the
selected backend’s lab view.

| File | Meaning |
|------|---------|
| `lab_manifest.json` | Communicator id (`mock` / `real`), `lab_automation` path, flags |
| `layout.json` | Table size, danger zone, storage grid |
| `component_library.json` | Parts that *could* exist |
| `active_catalog.json` | Tags on this deployment |
| `laser_lines.json` | Alignment overlays |
| `table_cam_preview.json` | JPEG tuning for real table cameras |
| `motor_rotations.json` | Software-tracked motor angles (may start empty) |

Also used at runtime: `control/` (ControlManager repos), optional `kernels/`,
`recipes/`, `camera_captures/`, and for mock a seed `lab_state.json`.

Reference bundles:

- Mock: `backend/lab_communicator/mock/lab_view/`
- Real starter: `backend/lab_communicator/real/lab_view/default/`

---

## 8. Scaffold a new backend

```bash
python scripts/ops/create_lab_communicator.py my_backend --with-lab-view
```

| File | Answers |
|------|---------|
| `communicator.py` | Lifecycle, persistence, video helpers, ensemble entry |
| `primitives.py` | One free function per primitive — **no** direct writes to `current_state` |

Then point the backend registry / manifest at the new `lab_view/`, implement
the checklist in §6 (especially camera BGR + ensemble), and treat **mock** as
the behavioral gold standard until real (or a future edge shape) matches it.

---

## 9. For implementers (short)

- Orchestration stays in **`base.py`** + `lab_model.orchestration`.
- Hardware steps stay in **`_primitive_*` → `primitives.py`**.
- Kernel **catalog** stays in **`lab_model`**; you only capture and invoke.
- When the communicator redesign lands, keep this split of **data plane
  (tensors / kernels)** vs **actuation plane (primitives)** even if the class
  hierarchy changes.
