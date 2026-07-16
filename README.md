# Cloud Labs — an Optical Processing Unit for the physical world

Imagine a computer that does not only add numbers, but **steers light**.
Mirrors tilt, stages creep by fractions of a millimetre, cameras watch the beam,
and somewhere a loop decides the next move. That machine is not science fiction —
it is an optics bench. What has been missing is a **shared language** for asking
it to work: something a student can script, a researcher can trust, and IT can
operate without living inside the robot driver’s manuals.

**Cloud Labs** treats the autonomous optics lab as an **Optical Processing Unit
(OPU)**: a remote-facing control plane that matches *intent* (what you want to
happen) with *instrumentation* (what the bench can actually do). You do not log
into the robot PC to “drive hardware.” You speak a small vocabulary of **lab
actions** — move, measure, optimize — and a coordinator decides *which* lab
and *which* edge process will carry them out.

---

## The three faces of the system

Everything in this repository is easiest to understand as three cooperating roles:

```text
  Author laptop                         Cloud / lab network              Bench PC
 ┌──────────────────┐                 ┌─────────────────────┐         ┌──────────────────┐
 │  Client SDK & UI │ ── HTTP/API ──► │ Central Coordinator │ ◄────── │   Edge Agent     │
 │  (scripts, Twin) │                 │ (matchmaking, lease,│         │ (owns hardware / │
 │                  │ ◄── state ───── │  jobs, catalog)     │ ──────► │  mock lab)       │
 └──────────────────┘                 └─────────────────────┘         └──────────────────┘
```

| Face | Where it runs | What it is for |
|------|----------------|----------------|
| **Client SDK (author laptop)** | Your notebook or the browser Twin | Write experiments in Python or click them in the UI. Acquire a **lease** so only one author owns the bench at a time. |
| **Central coordinator (cloud server)** | FastAPI in this repo (`backend/main.py`) | Matchmaking: which backend is active, who holds the lease, which jobs are queued. Serves Twin, Operations, and Wiki. |
| **Edge agent (bench PC)** | Process next to the instruments (or a mock) | Owns the **LabCommunicator** — the bridge to cameras, motors, and the robot. Runs closed-loop optimization where latency matters. |

A useful metaphor: the coordinator is **air-traffic control**; the edge is the
**aircraft**; your script is the **flight plan**. The plan never hands the
throttle cable directly to the passenger — it goes through the tower.

---

## What you can do here

| Surface | URL | Audience |
|---------|-----|----------|
| **Landing** | `/` | Orient yourself; pick a lab |
| **Twin** | `/twin` | Direct control + digital twin of the table |
| **Operations** | `/operations` | Watch jobs and edge health |
| **Wiki** | `/wiki` | **Learn** curriculum + **Backends** hub (components, kernels, snapshots) |

**Python scripting** lives in the `cloudlabs` package — see
[`packages/cloudlabs/README.md`](packages/cloudlabs/README.md) and the
[`scripts/language/`](scripts/language/) ladder.

---

## Quick start (mock lab on one machine)

```powershell
# Terminal 1 — coordinator (serves UI + API)
$env:PYTHONPATH="backend"
python backend/main.py

# Optional Terminal 2 — mock edge (distributed path)
$env:PYTHONPATH="backend"
python scripts/ops/mock_edge_agent.py

# Terminal 3 — author script
pip install -e ./packages/cloudlabs
python scripts/language/01_hello_lab.py
```

Open **http://127.0.0.1:8000/** — Twin, Operations, and Wiki are linked
from the landing page.

**New here?** Start in the in-app Wiki → **Learn**
(http://127.0.0.1:8000/wiki#learn/why) — architecture, primitives, backends,
and how to call the lab from Python. Wiki → **Backends** is the live hub for
components, kernels, and frozen snapshots per lab.

Point **`LAB_VIEW_PATH`** in `.env` at a lab deployment bundle when **running
the server** (catalog, layout, lasers, recipes). That is operator setup, not
something script authors configure. Bundles and scaffolding are explained in
[`backend/lab_communicator/README.md`](backend/lab_communicator/README.md).

---

## How intent reaches hardware

1. You issue a **primitive** (move a mirror, record a camera, run OPTIMIZE).
2. The coordinator validates the command, checks the **lease**, and either
   runs it in-process (simple mock) or **proxies** it to an attached edge agent.
3. The edge’s **LabCommunicator** turns that intent into instrument calls
   (or a faithful simulation) and updates **lab state**.
4. The Twin polls lab state so solids (measured poses) and ghosts (intent)
   stay honest.

**Kernels** (TorchScript scores and feature extractors) are not separate verbs.
They are **measurement functions** you attach as *inputs* to actions such as
OPTIMIZE — the same way a spectrometer is an instrument the optimizer may use,
not a second programming language.

---

## Deeper reading

| Topic | Document |
|-------|----------|
| Communicator / lab bundles | [`backend/lab_communicator/README.md`](backend/lab_communicator/README.md) |
| Scripting language & modes | [`packages/cloudlabs/README.md`](packages/cloudlabs/README.md) |
| Platform architecture | [`backend/lab_model/ARCHITECTURE.md`](backend/lab_model/ARCHITECTURE.md) |
| Execution modes (imperative / DAG / closed-loop) | [`docs/EXECUTION_MODES.md`](docs/EXECUTION_MODES.md) |
| Session & catalog kernels | [`docs/SESSION_KERNELS.md`](docs/SESSION_KERNELS.md) |
| Loss-kernel design (planned) | [`docs/LOSS_KERNELS.md`](docs/LOSS_KERNELS.md) |
| Surfaces & VC | [`docs/LAB_SURFACES_VC_AND_INITIALIZATION.md`](docs/LAB_SURFACES_VC_AND_INITIALIZATION.md) |
| Ops scripts | [`scripts/ops/README.md`](scripts/ops/README.md) |
| Language demos | [`scripts/language/README.md`](scripts/language/README.md) |

---

## Repository map (short)

```text
backend/          Coordinator API, lab_model (semantics), lab_communicator (hardware bridge)
frontend/         Twin, Operations, Wiki (static ES modules)
packages/cloudlabs/   Author-facing Python SDK
scripts/language/ Language demos    scripts/ops/    Edge agent, scaffolds, builders
schemas/          Reference JSON, approved kernel manifests
docs/             Design narratives and roadmaps
```

---

## For lab IT

- One process owns HTTP (**coordinator**). Optional second process owns hardware (**edge**).
- Swap benches by changing **`LAB_VIEW_PATH`**, not by rewriting the UI.
- Real benches need a reachable `lab_automation` tree (path in `lab_manifest.json`).
- Mock mode is the default teaching and CI path — no robot required.

Welcome. The OPU is ready when the coordinator is listening and a lab (mock or
real) is attached.
