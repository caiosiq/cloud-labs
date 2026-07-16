# Progress report — late June through mid-July 2026

**Audience:** Cloud Labs maintainers and advisors catching up on the last few weeks  
**Scope:** Scripting (`cloudlabs` SDK + language ladder), kernels, the OPU / three-faces model, lab version control, and Wiki / packaging work  
**Anchors in git:** `5669850` (MuJoCo) → `5ab1bbd` (version control) → `2d8f6b2` (lab control) → `117fcee` (general optimizer) → `fef6818` (scripting) → `204c8e9` (architecture proposition) plus subsequent Wiki / Learn work on the working tree  

---

## 1. Big picture

The platform story that crystallized over this period is:

> Cloud Labs is the **control plane** for an Optical Processing Unit (OPU). Authors speak a shared **primitive** language from Twin or Python. **Kernels** are measurement inputs to those actions, not peer verbs. **Version control** saves commanded **configuration**, not raw camera noise. The coordinator hosts **backends**; Twin and scripts pick one and take a **session lease**.

That story is now taught in the Wiki **Learn** curriculum, exercised by a six-step language-script ladder, and backed by ControlManager + SDK APIs that match the Twin control graph.

---

## 2. Three faces and the OPU

### Model

We settled on three equal faces rather than a “server does everything” mental model:

| Face | Role |
|------|------|
| **Client** | Twin UI and `cloudlabs` SDK — express intent, hold the session lease |
| **Coordinator** | FastAPI host — validate primitives/jobs, queue, leases, control repos |
| **Edge** | LabCommunicator (mock in-process, or bench PC / MuJoCo) — cameras, motors, tight loops |

“Remote” means intent travels over HTTP; physics and frame-rate closed loops stay on the edge. Authors choose a **backend** (`mock.default`, `real.default`, …); they do not pick Client/Coordinator/Edge from a notebook.

### Where it lives

- Learn chapter: [frontend/wiki/guides/02-opu-and-three-faces.md](../frontend/wiki/guides/02-opu-and-three-faces.md) with figure `figures/three-faces.svg`
- Related: execution modes (imperative vs closed-loop), connecting/backends, maintainer docs under `docs/PROGRAMMABLE_LAB_VISION.md` and `docs/EXECUTION_MODES.md`
- MuJoCo path landed earlier in the window (`5669850`) as another edge runtime option

---

## 3. Scripting (`cloudlabs` SDK)

### Intent

Experimentalists and UROPs should drive the same lab language from Python that Twin uses—not ad hoc JSON patches. The SDK acquired lease-aware `connect()`, fluent component helpers, kernel probe/optimize paths, and control-repo helpers (`commit_configuration`, `load_snapshot`, `reconcile_hardware`, stash/fork/publish).

### Language ladder (`scripts/language/`)

Curated teaching scripts (see [scripts/language/README.md](../scripts/language/README.md)):

| Script | Teaches |
|--------|---------|
| `01_hello_lab.py` | `connect` / lease, `prepare`, move/motor, capture; optional `--reconcile` |
| `02_kernels_and_match.py` | `probe_kernel` (`EVAL_KERNEL`), builtins, `kernel_match` → OPTIMIZE |
| `03_closed_loop_catalog.py` | Catalog pin + catalog TorchScript as OPTIMIZE inputs |
| `04_session_kernels.py` | Author artifact → register → probe → feature OPTIMIZE |
| `05_jobs_and_modes.py` | Objective compile, compiled DAG, closed-loop job |
| `06_client_side_measurable_loop.py` | Laptop-side `for` loop on `camera_image` — **no** kernel / **no** OPTIMIZE |

**Product rule reinforced in the ladder:** you speak **primitives** (move, probe, optimize). `register_kernel` uploads an artifact; it is not a lab verb.

### Multi-backend and leases

Scripts and Twin target one backend at a time. `connect("mock.default")` acquires a session lease; solo/strict-lease knobs exist for laptop vs multi-user mock practice. Learn chapter *Connecting and backends* documents the boot gate, client ids, and Wiki Backends vs Twin session.

---

## 4. Kernels

### Concept

Kernels are **measurement functions** (builtins, TorchScript packages, session packages) that parameterize `EVAL_KERNEL` / `OPTIMIZE`. They are not substitutes for primitives and not “another control plane.”

### What shipped / clarified

- Builtin and catalog kernel inventory exposed through coordinator APIs and Wiki Backends → Kernels
- Session kernel authoring path (`04_session_kernels.py`) for teachable custom scorers
- Clear split between **probe once** (`probe_kernel`) and **closed-loop** (edge OPTIMIZE with kernels as inputs)
- Client-side loop script (`06_…`) shows when *not* to use a kernel: author-in-the-loop analysis of a measurable on the laptop
- Wiki Learn chapters: *Kernels*, *Imperative vs closed-loop*, plus Backends hub kernel browse

### Camera / tensor contract (important correction)

Analysis format for camera measurables is **`bgr_hwc_uint8`** (BGR uint8 HxWx3). PNG is wire/storage, not the analysis type. Wiki measurable tables and handles were aligned to that.

---

## 5. Lab model corrections (tunables vs measurables)

Several product clarifications landed so Twin, Wiki, and scripts stay consistent:

- **Motor angles** are tunables (`nominal_motor_positions`). Lab observe / trackers **recalculate** that same field — they are **not** measurables and not a separate “reported motor” tunable.
- **Pose** is a tunable. Twin **ghost** = UI draft before confirm; **solid** = current lab state. Ghost confirm is a **primitive**, not a ControlManager **commit**.
- Removed or hidden legacy “pose / motor_rotations as measurable” and “reported_pose” product concepts from Wiki surfaces.
- Learn *Components* chapter rewritten around parameters / tunables / measurables with the component-triple figure.

---

## 6. Version control (ControlManager)

### Implementation window

Anchored by commit `5ab1bbd` (“version control”) and follow-on lab-control work: local git-like **configuration** history under `{lab_view}/control/{repo_id}/`, Twin control graph, reconcile-on-hard-checkout, stash, fork, soft/hard/adopt checkout, observations/setups, and publish → catalog pins.

### Concepts we now teach experimentally

| Term | Meaning |
|------|---------|
| **Runtime** | Live lab state (working tree) — not a commit node |
| **Configuration** | Commanded intent (tunables + holding) — what commits store |
| **Empty / zeroth baseline** | `EMPTY_CONFIGURATION` — shared empty bench for dirty detection |
| **Diff** | Field-level configuration change → reconcile plan of primitives |
| **Local control repo** | Twin commits / branches / stash |
| **Catalog pins** | Owner-approved remote snapshots (Wiki Backends → Snapshots) |

**Not VC:** session checkpoint (`session_last_lab_state.json`), recipe goldens, motor `MOTOR_SET_ZERO`.

### Documentation

New Learn chapter: [frontend/wiki/guides/05-version-control.md](../frontend/wiki/guides/05-version-control.md) (*Version control and lab history*), with figures `vc-layers.svg` and `vc-checkout.svg`. Curriculum renumbered so VC sits after Primitives and before TeleOp/telemetry. Maintainer depth remains in `docs/CONTROL_RUNTIME_AND_VERSIONING.md` and `docs/LAB_SURFACES_VC_AND_INITIALIZATION.md` (note: the former may still say “proposal” in its header even though the code path is live).

---

## 7. Wiki Learn + Backends hub + advisor packs

### Learn curriculum (current order)

1. Why Cloud Labs  
2. OPU and three faces  
3. Components and the lab model  
4. Primitives  
5. **Version control and lab history** *(new)*  
6. TeleOp, telemetry, and optimize  
7. Kernels  
8. Imperative vs closed-loop  
9. Connecting and backends  
10. Your first experiment  

### Backends hub

Live Wiki **Backends** section: gallery with hero images, per-backend Overview / Components / Kernels / Snapshots, catalog rows with physical interpretation, tunables, measurables (tensor axes), primitives.

### Offline / advisor deliverables

- Offline HTML zip builder: `scripts/ops/build_wiki_offline_html_zip.py` → `dist/cloudlabs-wiki-offline.zip`  
  Pre-rendered Learn + static Backends hub for **mock.default** (hero inlined + file copy; no localhost required to open).  
- Earlier: abstraction overview PDF and screenshot-based Wiki PDF experiments; offline zip became the preferred shareable pack.

---

## 8. Rough timeline (git landmarks)

| Date (approx.) | Landmark | Theme |
|----------------|----------|--------|
| 2026-06-24 | `5669850` | MuJoCo simulator runtime / UI |
| 2026-07-02 | `5ab1bbd` | Version control (ControlManager) |
| 2026-07-03 | `2d8f6b2`, `117fcee` | Lab control polish; general optimizer |
| 2026-07-12 | `fef6818` | `cloudlabs` scripting focus |
| 2026-07-15 | `204c8e9` | Architecture proposition packaging |
| Mid-July (working tree) | Wiki Learn rewrite, Backends hub, offline zip, VC Learn chapter + figures, measurable/tunable corrections, `06_client_side_measurable_loop.py` |

---

## 9. Open follow-ups (not blocking the story)

- Flip maintainer VC design docs from “proposal” status to “implemented” where accurate.  
- Optional language script dedicated to commit / stash / checkout (Learn points at Twin + `01_hello_lab.py --reconcile` today).  
- Rebuild the offline wiki zip after the new VC chapter/figures if advisors need the pack updated.  
- Keep Operations free of commit/stash UI per surface policy; verify no regressions.  

---

## 10. One-paragraph summary for advisors

Over the past couple of weeks we locked the **OPU / three-faces** control-plane story, shipped a coherent **Python scripting ladder** on the `cloudlabs` SDK, clarified that **kernels are measurements feeding primitives** (with a clean client-side measurable loop for the non-kernel case), finished the **git-like configuration version control** story in Twin and Learn (runtime vs configuration, empty baseline, diff/reconcile, pins), and packaged the Wiki for teaching via a **Learn curriculum** plus a **mock.default offline Backends pack**. Product corrections around pose/motors as tunables (not measurables) keep Twin, Wiki, and scripts speaking one lab model.
