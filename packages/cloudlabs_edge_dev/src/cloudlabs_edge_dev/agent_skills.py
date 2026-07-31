"""Agent-skill templates shipped inside every ``cloudlabs_edge/`` skeleton.

These live under ``.agents/skills/`` (not a Cursor-only path) so humans and any
AI assistant can open them while filling Phase 6 adapters. ``cloudlabs-edge
init`` writes them; ``doctor`` checks they are present.
"""

from __future__ import annotations

from pathlib import Path

# Relative paths under the edge root (checked by doctor).
REQUIRED_AGENT_SKILL_FILES: tuple[str, ...] = (
    ".agents/README.md",
    ".agents/skills/cloudlabs-context/SKILL.md",
    ".agents/skills/edge-contract/SKILL.md",
    ".agents/skills/measurables-tensors/SKILL.md",
    ".agents/skills/latency-channels/SKILL.md",
    ".agents/skills/kernels-optimize/SKILL.md",
    ".agents/skills/frames-calibration/SKILL.md",
    ".agents/skills/inventory-honesty/SKILL.md",
)

AGENTS_README = """\
# Agent skills for this edge

This folder ships with every Edge Contract skeleton. It is **tool-agnostic**:
read it yourself, or point any AI assistant at a skill when filling Phase 6
adapters. These are not a second API — they coach how to honor the contract
already defined by `capabilities.json`, `SKELETON.md`, and
`cloudlabs-edge certify`.

Start with [`skills/cloudlabs-context`](skills/cloudlabs-context/SKILL.md) if you
need the product mental model (same story as scientist onboarding). Then open a
topic skill for the adapter you are filling.

| Skill | Open when you are… |
|-------|--------------------|
| [`skills/cloudlabs-context`](skills/cloudlabs-context/SKILL.md) | Orienting: what Cloud Labs is, who talks to whom |
| [`skills/edge-contract`](skills/edge-contract/SKILL.md) | Adding endpoints, verbs, or side APIs |
| [`skills/measurables-tensors`](skills/measurables-tensors/SKILL.md) | Capturing images / shaping MeasurableTensor |
| [`skills/latency-channels`](skills/latency-channels/SKILL.md) | Teleop, live video, or long jobs |
| [`skills/kernels-optimize`](skills/kernels-optimize/SKILL.md) | Scoring frames or closed-loop OPTIMIZE |
| [`skills/frames-calibration`](skills/frames-calibration/SKILL.md) | Table↔robot transforms |
| [`skills/inventory-honesty`](skills/inventory-honesty/SKILL.md) | Store / place / inventory verbs |

After each adapter change, run::

    cloudlabs-edge doctor --path .
    cloudlabs-edge certify <edge-url> --path . --profile stub
"""

CLOUDLABS_CONTEXT_SKILL = """\
---
name: cloudlabs-context
description: >-
  Scientist-facing mental model of Cloud Labs (from docs/Onboarding.md).
  Use when orienting on this edge, explaining why adapters must speak the shared
  vocabulary, or deciding what belongs in the edge vs the coordinator/SDK.
---

# What Cloud Labs is

Cloud Labs lets someone drive a physical optics bench — robot arm, industrial
camera, laser, steppers, breadboard components — the same way they would drive a
simulation: a short script or the browser Twin says move this component, take a
picture, score it, optimize. The promise is **one vocabulary** for everything
you can do or observe, whether the answer comes from a laptop mock, a physics
simulation, or the real laser bench.

That matters because experimental work is iteration: debug logic on mock,
rehearse physics in simulation, run the same sequence on hardware without
rewriting the science.

## Vocabulary (the only language)

- **Primitives** — the only verbs that change or observe the world (move,
  record, optimize, arm live/teleop, …). No private side doors such as
  `get_video_feed()` outside that list.
- **Tunables** — commanded degrees of freedom (nominal pose, exposure, laser
  power, motor angle). You set them; the bench tries to make them true.
- **Measurables** — observations without a setpoint (e.g. camera image). You
  capture them; you do not “write pixels.”
- **Telemetry channels** — live feeds declared in capabilities (video, teleop),
  usable only after the arming primitive.
- **Kernels** — small TorchScript scorers that turn a frame into a score or
  features; closed-loop OPTIMIZE runs them next to the camera.

Rule: if it should happen, it must be nameable in this vocabulary — that is what
keeps scripts portable across benches.

**Edge READY** means `SYNC_RUNTIME` has completed (recordable tunables overwritten
from the world; non-recordable tunables set from `set_at_init`) — not merely that
`/health` responds. Mock/sim may implement sync as copy-from-state/JSON; a real
bench must measure.

## Who talks to whom

The scientist’s script and the **Twin** both talk to a **shared service**
(coordinator). That service validates verbs, holds the session lease, and
forwards work to lab software. The Twin is the same vocabulary with a visual
shell — not a second, more powerful API.

**This folder (`cloudlabs_edge/`)** is the lab software beside the instrument.
It alone may touch the arm, camera, and steppers. It owns messy details
(coordinate frames, camera protocols, motion planning) so none of that leaks
into scientist notebooks. From the edge’s point of view: honor the shared
vocabulary and refuse loudly when you cannot — never invent success.

## Preview vs truth

Live video and teleop are for the human eye (alignment, intuition). Latched
`RECORD_MEASURABLES` / kernel probes are for science: one timestamp (and an
honest latch note) ties image and related fields together. Compose analysis from
latched records, not from two independent live polls.

## Closed loop

Shipping every frame to a laptop to score is the wrong geometry. Capture →
kernel → actuate should stay on the bench; the script submits a job and waits
for the result. Session kernels are how a scientist brings *their* score onto
that path.

## Named labs

`connect("mock.default")` / `"sim.default"` / `"real.default"` (or another
registered id) is how a scientist picks an instrument. Mock, sim, and real are
the same kind of system behind different names — this edge is one of those
instruments.

## Full onboarding

Longer scientist-facing narrative (with figures): Cloud Labs repo
`docs/Onboarding.md` (PDF: `docs/Onboarding.pdf`).
"""

EDGE_CONTRACT_SKILL = """\
---
name: edge-contract
description: >-
  Implements Cloud Labs Edge Contract v1 rules for this cloudlabs_edge folder.
  Use when adding HTTP endpoints, primitives, adapters, or any lab-facing API;
  when tempted to add a side door outside POST /execute; or when mapping
  NotImplementedError to refused/failed responses.
---

# Edge Contract (this folder)

## Do

- Mutation has **one door**: `POST /execute` with `{"primitive", "args"}`.
- Route verbs through `dispatch.dispatch_primitive` → `adapters/*`.
- Keep `GET /capabilities` and `GET /bench` as declarations only (abilities vs geometry).
- Map outcomes with `contract.py`: `completed` / `refused` / `failed`.
- Raise `NotImplementedError` for unsupported verbs → surface as `refused` + `NOT_IMPLEMENTED`.
- Keep hardware imports inside `adapters/` (and lab runtime helpers), not in `main.py`.

## Do not

- Do not add `get_video_feed()`, per-verb REST routes, or Twin-only shortcuts.
- Do not merge bench geometry into capabilities (or the reverse).
- Do not invent success for missing hardware — refuse or fail honestly.
- Do not import coordinator / SDK packages from the edge process.

## Check

```text
cloudlabs-edge doctor --path .
cloudlabs-edge certify <url> --path . --profile stub
```

Read `SKELETON.md` and `capabilities.json` `supported_primitives` before adding a verb.
"""

MEASURABLES_TENSORS_SKILL = """\
---
name: measurables-tensors
description: >-
  Builds canonical camera_image MeasurableTensor envelopes, LazyRefs, and
  latched epochs on a Cloud Labs edge. Use when implementing RECORD_MEASURABLES,
  serving /measurables/... bytes, changing image metadata, or resolving tensors
  for scripts.
---

# Measurables and tensors

## Do

- Emit `camera_image` with the **canonical** envelope (dtype/domain/axes/units/layout)
  matching the coordinator schema — prefer a local `measurables_schema.py` helper.
- Return heavy pixels as a **LazyRef** (`kind=url`, href under this edge); cache the
  exact JPEG from the latched capture so the URL is not a fresh live frame.
- Open/close a latch around capture (`latch.py`); stamp `epoch_ms` + `latch_quality`.
- Serve `GET /measurables/{tag}/camera_image.jpg` from that cache.

## Do not

- Do not invent axis labels, swap H/W, or claim `hardware_triggered_latch` without hardware.
- Do not put full BGR arrays in lab-state JSON.
- Do not let live-stream endpoints substitute for `RECORD_MEASURABLES` science.

## Typical files

- `adapters/observe.py`, `latch.py`, measurable schema helper, stream routes in the server app.

## Check

Certify must pass the measurable-envelope conformance check after changes.
"""

LATENCY_CHANNELS_SKILL = """\
---
name: latency-channels
description: >-
  Chooses the correct Cloud Labs edge channel for teleop, live video, and
  deliberate work. Use when implementing START_TELEOP, live feed streams,
  WebSockets, OPTIMIZE jobs, or debating HTTP vs stream vs job.
---

# Latency channels

## Three tiers (all still in the vocabulary)

| Tier | Arm with | Transport | Use for |
|------|----------|-----------|---------|
| A | `START_TELEOP` | Flat WS frames | Interactive jog / pose |
| B | `START_LIVE_FEED` | JPEG / MJPEG | Preview for the eye |
| C | `POST /execute` / jobs | Request or job stream | Move, record, optimize |

## Do

- Reject nested teleop JSON; keep Tier A payloads tiny.
- Return HTTP 409 on stream URLs until the matching START primitive armed the channel.
- Run long OPTIMIZE loops as **jobs** on the edge (progress stream), not a held HTTP body.
- Stamp time on samples; never pretend live preview is a scientific record.

## Do not

- Do not closed-loop optimize by shipping every frame to a laptop.
- Do not open teleop or video without the arming primitive.
- Do not mix Tier A rates with deep schema validation on every frame.

## Typical files

- `adapters/teleop.py`, `adapters/live_feed.py`, `adapters/optimize.py`, server WS/stream routes, `runtime/jobs.py` if present.
"""

KERNELS_OPTIMIZE_SKILL = """\
---
name: kernels-optimize
description: >-
  Provisions TorchScript kernels and runs EVAL_KERNEL / OPTIMIZE on a Cloud Labs
  edge. Use when wiring kernel_host, session kernels, scoring camera frames, or
  closed-loop optimization jobs.
---

# Kernels and optimize

## Do

- Treat kernels as **inputs** to primitives (`EVAL_KERNEL`, `OPTIMIZE`), not new verbs.
- Use `kernel_host.py` for provision (b64 or URI + digest), cache, and `eval_on_bgr`.
- Derive `capabilities.features.torchscript_execution` from real torch availability.
- Run capture → score → actuate **on the edge** inside OPTIMIZE jobs.
- Bake targets into the job before submit (no arbitrary Python callback each eval).

## Do not

- Do not add `/kernel/probe` outside the contract.
- Do not fail with a stack trace when torch is missing — refuse as unavailable.
- Do not re-download unverified artifacts every eval; verify digest, then cache.

## Authoring vs closed loop

- One-shot score: `EVAL_KERNEL` / SDK `probe_kernel`.
- Many evals: `OPTIMIZE` job / SDK `run_cobyla` / `run_optimize`.

## Typical files

- `kernel_host.py`, `adapters/optimize.py`, `adapters/observe.py`.
"""

FRAMES_CALIBRATION_SKILL = """\
---
name: frames-calibration
description: >-
  Owns table-to-robot frame transforms on a Cloud Labs edge with fail-loud
  calibration. Use when implementing MOVE_COMPONENT, teleop pose conversion,
  or wiring LAB_ROBOT_* calibration constants.
---

# Frame calibration

## Do

- Keep table/lab millimetres and degrees as the **only** language Twin/SDK speak.
- Convert to the robot frame inside the edge (e.g. `runtime/frames.py`) at the last moment.
- Fail loud (`FrameCalibrationUnavailable` or equivalent) when a real transform is
  needed and calibration is missing.
- Allow identity only via explicit opt-in (mock / `LAB_ROBOT_FRAME_IDENTITY=1`).
- Report calibration status on `GET /bench` without raising.

## Do not

- Do not silently default a real arm to identity.
- Do not push robot-frame coordinates into the coordinator or SDK.
- Do not scatter rotation/origin/sign constants across adapters — one module owns them.

## Env / configure

Typical names: `LAB_ROBOT_TABLE_ROTATION_RAD`, `LAB_ROBOT_ORIGIN_X_MM` / `_Y_MM`,
`LAB_ROBOT_SIGN_X` / `_Y`, `LAB_ROBOT_RZ_OFFSET_DEG`. Prefer `frames.configure(...)`
in tests; production should set real lab constants.
"""

INVENTORY_HONESTY_SKILL = """\
---
name: inventory-honesty
description: >-
  Keeps store, place, inventory, and runtime-sync primitives honest on a Cloud
  Labs edge. Use when implementing STORE/PLACE, RECORD_TUNABLES, SYNC_RUNTIME,
  LOCALIZE_COMPONENTS (alias), or any claim about where components live / what
  tunables are.
---

# Inventory honesty

## Do

- Treat `data/library.json` + `data/inventory.json` as the source of truth for
  what the lab owns and what is currently tracked (`GET /library`, `GET /inventory`).
- Implement inventory verbs only when the lab stack can actually track storage.
- Until then, keep scaffold `NotImplementedError` → `refused` / `NOT_IMPLEMENTED`.
- Prefer confirming holding/presence from real session + gripper/vision state over guesses.
- Document in capabilities what you truly support; do not advertise inventory you cannot enforce.
- Declare per-tunable metadata in the library (authored JSON — doctor/load
  **fail hard**; nothing silently backfills missing capabilities):
  - `recordable: true` → `RECORD_TUNABLES` may overwrite from the world
    (pose writes **`nominal_pose`**, never a shadow `reported_pose`).
  - `recordable: false` → **`set_at_init` is required**; SYNC sets that value.
- When `features.runtime_sync` is true, `RECORD_TUNABLES` + `SYNC_RUNTIME` must
  be in `supported_primitives`, and every non-`nominal_pose` tunable must declare
  `recordable` explicitly (doctor strict mode).
- Wire `RECORD_TUNABLES` (and `SYNC_RUNTIME` = RECORD(recordable) + SET(set_at_init)
  for inventory tags). Edge is not READY until SYNC completes.
- Mock/sim may implement RECORD as copy-from-state/JSON; real edges must measure.
- `LOCALIZE_COMPONENTS` is a **deprecated alias** of RECORD for `nominal_pose` only;
  prefer RECORD_TUNABLES / SYNC_RUNTIME. Default inventory scope: `localize != false`
  and `placement` in `{table, storage}`.

## Do not

- Do not fake "stored" / "placed" success to make certify green.
- Do not invent slot maps or AprilTag locations without a source of truth.
- Do not silently no-op STORE/PLACE — refusal is better than a lie.
- Do not keep a separate edge `active_catalog.json`; active tags are inventory keys.
- Do not invent `reported_*` shadow tunables; recording overwrites the real tunable.
- Do not advertise READY / accept motion before SYNC_RUNTIME has succeeded.
- Do not silently backfill / infer missing library `capabilities` or `recordable`
  metadata at load time — fix the authored JSON so doctor fails for the real cause.

## Related

- Holding confirmation (e.g. `CONFIRM_HOLDING_TAG`) may use session state + gripper
  when that is all the hardware exposes — say so in the result (`source` field).
- Design brief: Cloud Labs `docs/RECORD_TUNABLES_AND_SYNC_RUNTIME.md`.
"""


def skill_file_map() -> dict[str, str]:
    """Map relative edge paths → file contents for ``init``."""
    return {
        ".agents/README.md": AGENTS_README,
        ".agents/skills/cloudlabs-context/SKILL.md": CLOUDLABS_CONTEXT_SKILL,
        ".agents/skills/edge-contract/SKILL.md": EDGE_CONTRACT_SKILL,
        ".agents/skills/measurables-tensors/SKILL.md": MEASURABLES_TENSORS_SKILL,
        ".agents/skills/latency-channels/SKILL.md": LATENCY_CHANNELS_SKILL,
        ".agents/skills/kernels-optimize/SKILL.md": KERNELS_OPTIMIZE_SKILL,
        ".agents/skills/frames-calibration/SKILL.md": FRAMES_CALIBRATION_SKILL,
        ".agents/skills/inventory-honesty/SKILL.md": INVENTORY_HONESTY_SKILL,
    }


def write_agent_skills(edge_root: Path) -> None:
    """Write the standard ``.agents/skills`` tree under ``edge_root``."""
    root = edge_root.resolve()
    for rel, content in skill_file_map().items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
