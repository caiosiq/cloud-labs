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
- Advertise shared Command Matrix topology in `capabilities.json` as
  `execution_threads`: `arm.0` + `sense.0` only. Do **not** list bare
  `motor.<n>` columns — `motor_id` is per-tag; the coordinator creates
  `motor.<tag_id>.<motor_id>` on demand (see cloud-labs `docs/COMMAND_MATRIX.md`).
- Map outcomes with `contract.py`: `completed` / `refused` / `failed`.
- Raise `NotImplementedError` for unsupported verbs → surface as `refused` + `NOT_IMPLEMENTED`.
- Keep hardware imports inside `adapters/` (and lab runtime helpers), not in `main.py`.

## Do not

- Do not add `get_video_feed()`, per-verb REST routes, or Twin-only shortcuts.
- Do not merge bench geometry into capabilities (or the reverse).
- Do not invent success for missing hardware — refuse or fail honestly.
- Do not import coordinator / SDK packages from the edge process.
- Do not implement a command queue on the edge — `/execute` stays single-shot;
  the coordinator owns queues, HOLDING locks, and OPTIMIZE barriers.
- Do not add Twin-only status endpoints or write Twin `system_status` /
  dirty/stash / `telemetry.teleop.active` to “fix” the UI — the coordinator
  owns BUSY/HOLDING/IDLE/TELEOP around southbound execute (see
  `cloudlabs-context`, `latency-channels`, BACKEND_ISOLATION).

## Check

```text
cloudlabs-edge doctor --path .
cloudlabs-edge certify <url> --path . --profile stub
```

Read `SKELETON.md` and `capabilities.json` `supported_primitives` before adding a verb.
