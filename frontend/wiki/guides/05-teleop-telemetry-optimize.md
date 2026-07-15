# TeleOp, telemetry, and optimize

Three capabilities recur in autonomous optics: continuous observation, manual
control, and automated alignment. Cloud Labs names them **telemetry**,
**TeleOp**, and **OPTIMIZE**.

## Telemetry — continuous observation

**Telemetry** attaches the UI (and related clients) to live streams and
sessions: MJPEG feeds, optimization preview, TeleOp heartbeats. It is declared
per component in the catalog (`capabilities.telemetry`).

Tunables and measurables form recorded lab state: tunables hold command and
report for the same degrees of freedom; measurables hold captured observations
(camera frames, scores) with no matching setpoint. Telemetry is the continuous
channel used when ongoing sight of the beam or session is required.

Typical actions:

- **START_LIVE_FEED** / **END_LIVE_FEED** — open or close a stream.
- Twin right panel — monitor the feed during work.

## TeleOp — human in the loop

**TeleOp** is a leased, per-component control mode: jog or goto while the
system records that the current session owns that optic. Primitives include
`START_TELEOP`, `TELEOP_JOG`, `TELEOP_GOTO`, and `END_TELEOP`.

Safety constraints typically include lab idle, part not in storage, and no
concurrent TeleOp on the same component by another session.

## Optimize — closed-loop actuation

**OPTIMIZE** starts a closed-loop (or legacy) session: propose actuator steps,
measure, update the objective, repeat. The preferred path is **ensemble**
optimization: variables, an objective graph, and optional **kernels**.

The author defines what may move (variables) and what constitutes improvement
(objective). The edge walks that landscape. Unless the imperative path is
chosen deliberately, each millidegree step does not require a separate HTTP
round-trip from the laptop.

Kernels (next chapter) supply custom camera math as **inputs** to OPTIMIZE—not
as a second command language.

## How the three fit together

| Need | Mechanism |
|------|-----------|
| Observe the beam continuously | Telemetry / live feed |
| Align manually | TeleOp |
| Align overnight or at frame rate | Optimize (+ kernels) |
| Scripted setup between steps | Imperative primitives |

## Practice

- Twin: start a live feed on a camera-capable tag.
- Catalog: open a kernel and read **Physical interpretation**—that score is
  what OPTIMIZE can target.
- Scripts: `03_closed_loop_catalog.py` for a minimal OPTIMIZE.

Next: [Kernels](#)—measurement functions that guide actions.
