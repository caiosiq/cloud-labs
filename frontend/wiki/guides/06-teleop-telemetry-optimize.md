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

## Optimize — closed-loop on the edge

**OPTIMIZE** (ensemble) ships a **pipeline** once: variables, objective terms
(usually **edge kernels**), solver. The edge runs capture → kernel → loss →
actuate locally; Twin subscribes to the job stream (loss curve + stage debug).

### Twin Optimization mode (operator path)

1. **Objective** — prefer **edge kernel presets** (active catalog). Legacy
   measurables (derived centroid / power readback) are fallbacks.
2. **Variables** — motors / pose axes to move.
3. **Tune** — weights and targets (negative weight allowed, e.g. maximize beam shift).
4. **Solver** — eval budget, trust region, **max Δ from start** (deg/mm),
   **keep best** / **rollback to x0 on abort**, stage-debug toggle.
5. **Run** — plan summary (capture → kernel → actuate) + optional VC reconcile/commit.
6. **Results** — best loss, setpoints, and **per-stage debug** for the last eval.

### Stage debug (catch failures early)

Each eval reports three health cards:

| Stage | Means | Typical failure |
|-------|--------|-----------------|
| **capture** | Frame / measurable latch | Camera offline, empty tensor, wrong tag |
| **kernel** | TorchScript features / scalar | Missing `.pt`, torch error, empty features |
| **actuate** | Apply + clearance + max-delta | Arm still holding, step > max Δ, motor error |

Live panel and Results show `c✓ k✓ a✓` marks; refused steps (max-delta) do not
move hardware.

### Safety defaults

- Continuous blocks require **optical path clear** (arm not holding).
- **Max Δ from start** refuses oversized candidates vs the applied x0 / VC node.
- Abort: **keep best** (default) or **rollback to x0**.

Kernels (next chapter) supply camera math as **inputs** to OPTIMIZE—not as a
second command language.

## How the three fit together

| Need | Mechanism |
|------|-----------|
| Observe the beam continuously | Telemetry / live feed |
| Align manually | TeleOp |
| Align overnight or at frame rate | Optimize (+ edge kernels) |
| Scripted setup between steps | Imperative primitives |

## Practice

- Twin: Optimization mode on `real.default` with a kernel preset + one motor ±0.5°.
- Backends → Kernels: confirm `artifact_present` for the presets you need.
- Scripts: `03_closed_loop_catalog.py` for a minimal OPTIMIZE.

Next: [Kernels](#)—measurement functions that guide actions.
