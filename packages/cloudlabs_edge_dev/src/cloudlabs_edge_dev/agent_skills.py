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

| Skill | Open when you are… |
|-------|--------------------|
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
  Keeps store, place, and inventory primitives honest on a Cloud Labs edge.
  Use when implementing STORE_COMPONENT, PLACE_FROM_STORAGE, AFFIRM, REPACK,
  RECENTER, REMOVE, or any inventory claim about where components live.
---

# Inventory honesty

## Do

- Treat `data/library.json` + `data/inventory.json` as the source of truth for
  what the lab owns and what is currently tracked (`GET /library`, `GET /inventory`).
- Implement inventory verbs only when the lab stack can actually track storage.
- Until then, keep scaffold `NotImplementedError` → `refused` / `NOT_IMPLEMENTED`.
- Prefer confirming holding/presence from real session + gripper/vision state over guesses.
- Document in capabilities what you truly support; do not advertise inventory you cannot enforce.
- Wire `LOCALIZE_COMPONENTS` to a real scan (`scan_components_cloudlab` or equivalent);
  default scope is inventory tags with `localize != false` and `placement` in
  `{table, storage}`.

## Do not

- Do not fake "stored" / "placed" success to make certify green.
- Do not invent slot maps or AprilTag locations without a source of truth.
- Do not silently no-op STORE/PLACE — refusal is better than a lie.
- Do not keep a separate edge `active_catalog.json`; active tags are inventory keys.

## Related

- Holding confirmation (e.g. `CONFIRM_HOLDING_TAG`) may use session state + gripper
  when that is all the hardware exposes — say so in the result (`source` field).
"""


def skill_file_map() -> dict[str, str]:
    """Map relative edge paths → file contents for ``init``."""
    return {
        ".agents/README.md": AGENTS_README,
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
