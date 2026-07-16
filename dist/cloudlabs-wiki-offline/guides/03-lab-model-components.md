# Components and the lab model

On the bench, experimenters reason about named parts—mirrors, cameras,
motors—not undifferentiated configuration records. The **lab model** turns that
structure into a programmable contract.

## Components and tags

Every part on the breadboard (or in the library) has a **`tag_id`** (for
example `tag_20`). The Twin renders it; scripts address it; the catalog states
what it can do.

Within each component, Cloud Labs separates three concepts that are often mixed
in ad hoc lab software:

![Parameters, tunables, and measurables](/static/wiki/guides/figures/component-triple.svg)

*Identity (parameters); degrees of freedom you set via primitives (tunables); observations with no matching setpoint (measurables).*

### Parameters — identity

Catalog identity: type (mirror, camera, …), geometry hints, and which
primitives are allowed. Parameters do not change because a motor was jogged;
they change when the part definition changes.

### Tunables — degrees of freedom you control

A **tunable** is a lab degree of freedom you can set—breadboard pose, motor
angles, exposure, storage placement. There is **one** value for that DOF in
the model. Scripts and Twin panels change it by issuing **primitives** (or
confirming a Twin edit)—not by silently patching lab JSON.

**Lab observe recalculates the same tunable.** A camera scan or motor tracker
updates that DOF’s value from the bench. That is **not** a measurable and
**not** a second “reported tunable.” Getting data from the lab about a DOF is
how you refresh that tunable (and whatever depends on it).

#### Ghost vs solid in the Twin (UI only)

In the Twin canvas:

- **Ghost** (semi-transparent) is your **visual control** for a pose tunable:
  drag / place the intended pose, then **confirm** so the change is sent as a
  primitive.
- **Solid** shows the part’s **current** pose in lab state (after motion
  settles or after a scan recalculates the tunable).

Ghost is not a separate field in the lab model. It is how the UI lets you
compose a change before you commit it.

### Measurables — observations without a matching tunable

**Measurables** are quantities that do **not** have a 1:1 tunable counterpart.
You obtain them only by measuring; they are often stochastic or
high-dimensional:

- camera frames
- kernel scores / feature vectors
- other sensor summaries that are not “the value of a setpoint”

These refresh when you **record** / capture / probe—not when you merely
recalculate a motor angle or pose tunable from the lab.

**Working rule:** scripts **set** tunables via actions (or Twin confirm);
the lab may **recalculate** those same tunables from observe; scripts
**capture** measurables. Optimizers consume measurables (and may actuate
tunables). A camera frame is not a free variable assigned in Python.

## Why the split matters

| Ambiguity | Lab-model resolution |
|-----------|----------------------|
| Did my Twin move land? | Confirm ghost → primitive runs → solid catches up to the updated tunable |
| Why is the ghost offset from the solid? | You are editing a proposed pose; it is not committed until you confirm |
| Is pose a measurable? | No—pose is a tunable; scan/observe recalculates that tunable |
| Is encoder angle a measurable? | No—it recalculates `nominal_motor_positions` (same tunable) |
| Can a script invent a camera image? | No—only capture primitives refresh images |

## Telemetry (preview)

In addition to state control (tunables / measurables), components may declare
**telemetry**: live feeds and TeleOp sessions. That is the continuous
observation channel, covered with TeleOp and Optimize in the next chapters.

## Practice

1. Wiki → **Backends** → open a lab → select a camera tag → read **Physical interpretation**.
2. Inspect **Measurables**—for example `camera_image` as an optical observation,
   not only an `HxWx3` array. Pose and motor angles belong under **Tunables**.
3. In the Twin, drag the **ghost**, confirm the move, and watch the **solid**
   follow once the lab applies / settles that tunable.

Next: [Primitives](#)—the actions that change the lab.
