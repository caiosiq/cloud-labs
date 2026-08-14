---
name: latency-channels
description: >-
  Chooses the correct Cloud Labs edge channel for teleop, live video, and
  deliberate work. Use when implementing START_TELEOP, live feed streams,
  WebSockets, OPTIMIZE jobs, debating HTTP vs stream vs job, or when Twin
  teleop UI never shows loading/controls after a successful START_TELEOP.
---

# Latency channels

## Three tiers (all still in the vocabulary)

| Tier | Arm with | Transport | Use for |
|------|----------|-----------|---------|
| A | `START_TELEOP` | Flat WS frames | Interactive jog / pose |
| B | `START_LIVE_FEED` | JPEG / MJPEG | Preview for the eye |
| C | `POST /execute` / jobs | Request or job stream | Move, record, optimize |

## Twin UI teleop session vs edge hardware lease

These are **two different leases**. Confusing them is the usual “robot grabbed
the part but Twin never entered teleop mode” bug.

| Layer | Owns | What Twin needs |
|-------|------|-----------------|
| **This edge** | Hardware arming (LiveControl / robot enter), WS path, jog/goto execution | Honest `completed` / `refused` on `START_TELEOP` / `END_TELEOP` |
| **Coordinator (Twin)** | `components[tag].telemetry.teleop.active` / `ready` / `mode`, and Twin `system_status=TELEOP` | Commit after remote execute succeeds |

- Edge `START_TELEOP` may return a **flat** result
  (`{tag_id, active: true, ws_path}`) — that is enough. You do **not** need to
  invent a nested Twin `telemetry` blob for the UI to work on current Cloud Labs.
- Edge `GET /lab-state` may keep `telemetry.teleop.active=false` as a static
  template. Twin merge **preserves coordinator teleop session flags** and only
  overlays edge streams (`live_feed`, samples). Do not “fix” Twin by writing
  Twin FSM fields from the edge.
- Twin shows LOADING when `active && !ready`, and jog controls when
  `active && ready`. After a successful remote START, the coordinator commits
  both (hardware setup already finished on the edge).

If START succeeds on the arm but the UI stays idle: check the **coordinator**
remote-commit path / lab-state poll — not a missing inventory camera and not a
frontend-only bug.

## Do

- Reject nested teleop JSON; keep Tier A payloads tiny.
- Return HTTP 409 on stream URLs until the matching START primitive armed the channel.
- Run long OPTIMIZE loops as **jobs** on the edge (progress stream), not a held HTTP body.
- Stamp time on samples; never pretend live preview is a scientific record.
- **START_TELEOP mode routing** (must match Twin `rz` vs `pose3d`):
  - On table / not holding this tag → table Rz enter (`start_table_rotation` / equivalent).
  - Holding this tag → held Cartesian enter (`start_held_cartesian` / equivalent).
  - Log which enter path ran; refuse clearly if the arm holds a *different* tag.

## Do not

- Do not closed-loop optimize by shipping every frame to a laptop.
- Do not open teleop or video without the arming primitive.
- Do not mix Tier A rates with deep schema validation on every frame.
- Do **not** always call held-Cartesian on START_TELEOP — that breaks on-table Rz TeleOp
  (`start_held_cartesian: not holding a part` while the Twin expected Rz).
- Do not invent Twin-only teleop status endpoints or write Twin
  `telemetry.teleop.active` / `system_status=TELEOP` from the edge so the UI
  “looks right” — the coordinator commits those after remote START/END.
- Do not require every lab to mirror Twin’s teleop FSM inside edge `/lab-state`
  for certify; arm the hardware and return contract outcomes.

## Typical files

- `adapters/teleop.py`, `adapters/live_feed.py`, `adapters/optimize.py`, server WS/stream routes, `runtime/jobs.py` if present.
