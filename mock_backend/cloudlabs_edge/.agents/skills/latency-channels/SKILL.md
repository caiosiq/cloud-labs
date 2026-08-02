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

## Typical files

- `adapters/teleop.py`, `adapters/live_feed.py`, `adapters/optimize.py`, server WS/stream routes, `runtime/jobs.py` if present.
