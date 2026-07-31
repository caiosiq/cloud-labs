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
