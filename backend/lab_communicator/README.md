# LabCommunicator — the bridge from cloud intent to bench physics

Think of the digital twin as speaking a **laboratory dialect**: “move tag 20,”
“record the camera,” “optimize this motor.” Instruments speak something else —
joint angles, OpenCV frames, serial quirks. The **LabCommunicator** is the
translator that stands between those worlds so the rest of Cloud Labs never
has to know which robot brand is bolted to the table.

In the **Three-Face** model it lives on the **Edge Agent** (the bench PC, or a
faithful mock). The **Central Coordinator** only needs to know that *some*
communicator will execute validated **primitives**. The **Client SDK** never
imports drivers at all.

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

---

## What this package is responsible for

| Responsibility | Here? |
|----------------|--------|
| Own **lab state** (poses, measurables, `system_status`) | Yes — `LabCommunicator` in `base.py` |
| Implement each **primitive** against hardware or simulation | Yes — per-backend `primitives.py` |
| Validate command JSON / metric catalogs | No — that is `lab_model` |
| Serve HTTP or the Twin UI | No — that is `backend/main.py` / `frontend/` |

**Related:** [`../lab_model/primitives/README.md`](../lab_model/primitives/README.md),
[`../lab_model/ARCHITECTURE.md`](../lab_model/ARCHITECTURE.md).

**For people new to Cloud Labs:** open the in-app Wiki **Learn** section
(`/wiki#learn/why`) before diving into hooks below — that track explains the
OPU model, primitives, and how to connect from Python.

---

## The lab deployment bundle (`LAB_VIEW_PATH`)

Before any communicator starts, the coordinator reads **`LAB_VIEW_PATH`** from
`.env` and bootstraps a **bundle**: one directory that describes *this* bench’s
geometry, catalog, lasers, and recipes. Swap benches by pointing that variable
at another folder — not by rewriting application code.

### Files that must exist

| File | Physical / ops meaning |
|------|-------------------------|
| `lab_manifest.json` | Which communicator (`mock` / `real`) and where `lab_automation` lives |
| `layout.json` | Table size, danger zone, storage grid — the “map” of the room |
| `component_library.json` | What parts *could* exist (mirrors, cameras, …) |
| `active_catalog.json` | Which tags are on *this* deployment |
| `laser_lines.json` | Overlay lines for alignment guides |
| `table_cam_preview.json` | JPEG tuning for real table cameras |
| `motor_rotations.json` | Software-tracked motor angles (may start empty) |

Directories such as `recipes/`, `states/`, and `camera_captures/` are created
automatically when missing. A `lab_state.json` seed is strongly recommended for
mock teaching labs.

Reference bundles:

- Mock: `backend/lab_communicator/mock/lab_view/`
- Real starter: `backend/lab_communicator/real/lab_view/default/`

---

## Scaffold a new backend

From the repository root:

```bash
python scripts/ops/create_lab_communicator.py my_backend --with-lab-view
```

You get a package with two audiences:

| File | Answers |
|------|---------|
| `communicator.py` | Lifecycle hooks, persistence, video helpers |
| `primitives.py` | One free function per primitive — **no** direct writes to `current_state` |

Then:

1. Point **`LAB_VIEW_PATH`** at the new `lab_view/`.
2. Register the communicator id in the factory / manifest.
3. Replace stub primitives with real instrument calls (or keep sleeps for a dry-run).

---

## Folder layout

```text
backend/lab_communicator/
├── base.py                 ← shared state machine + orchestration
├── shared/                 ← bundle bootstrap, lasers, catalog helpers
├── mock/                   ← teaching / CI simulator + lab_view/
├── real/                   ← xArm / lab_automation bridge + lab_view/default/
└── <your_backend>/         ← add new folders here
```

**Import rules:** `shared/` must not import concrete backends; `mock/` and
`real/` must not import each other; `lab_model` must not import this package.

---

## How a state capture feels (RECORD_MEASURABLES)

When the Twin or SDK asks to **record** a camera measurable, the sequence is:

```text
Author                Coordinator              Edge Communicator           Instrument
  │                        │                          │                        │
  │ RECORD_MEASURABLES     │                          │                        │
  │───────────────────────►│  proxy / dispatch        │                        │
  │                        │─────────────────────────►│                        │
  │                        │                          │ capture frame          │
  │                        │                          │───────────────────────►│
  │                        │                          │◄── PNG / BGR ──────────│
  │                        │                          │ commit measurables     │
  │                        │                          │ into lab_state         │
  │                        │◄── { measurables } ──────│                        │
  │◄── updated lab-state ──│                          │                        │
```

Closed-loop **OPTIMIZE** follows the same communicator, but the edge may run
many capture→score→actuate cycles **inside** one OPTIMIZE action so round-trips
to the author laptop do not dominate the loop.

---

## Mock vs real

| Mode | Intuition |
|------|-----------|
| **Mock** | A physics-flavoured simulator: sleeps, noise, synthetic landscapes. Ideal for students and CI. |
| **Real** | Wraps `lab_automation` / `OpticalExperiment`: true robot frames, true cameras. Requires a valid `lab_automation_path`. |

Both expose the **same primitive vocabulary** upward. That is the point of the
bridge.

---

## For implementers

- Orchestration (busy checks, locks, status transitions) lives in **`base.py`**.
- Hardware steps live in **`primitives.py`** as `_primitive_*` → `primitive_*` pairs.
- Do not hardcode catalog paths — use `get_lab_view_paths()` after bootstrap.
- Full checklist and wiring notes remain in the scaffold script help and
  `lab_communicator/README` sections above; compare `mock/communicator.py` when
  unsure of signatures.
