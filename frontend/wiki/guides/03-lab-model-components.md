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

*Identity (parameters); commanded and reported values of the same degrees of freedom (tunables); observations with no matching tunable (measurables).*

### Parameters — identity

Catalog identity: type (mirror, camera, …), geometry hints, and which
primitives are allowed. Parameters do not change because a motor was jogged;
they change when the part definition changes.

### Tunables — command and report

A **tunable** is a lab degree of freedom you can command—breadboard pose, motor
setpoints, exposure intent, storage placement.

Because the lab may not achieve the command exactly, each such quantity also
has a **reported** value: what the bench currently believes that same DOF is.
Refreshing pose from a camera scan, or reading motor encoders, updates the
*reported* side of the tunable. That is **not** a measurable: there is still a
1:1 link to the command you issued.

In the Twin:

- **Ghost** (semi-transparent) ≈ **commanded** pose (`nominal_pose`)
- **Solid** ≈ **reported** pose (`reported_pose`) after refresh or motion settle

Mutations to commanded tunables go through **primitives** (or Twin panels that
issue them). Clients do not silently patch lab JSON.

### Measurables — observations without a matching tunable

**Measurables** are quantities that do **not** have a 1:1 tunable counterpart.
You obtain them only by measuring; they are often stochastic or
high-dimensional:

- camera frames
- kernel scores / feature vectors
- other sensor summaries that are not “the value of a setpoint”

These refresh when you **record** / capture / probe—not when you merely
re-read a motor angle.

**Working rule:** scripts write commanded tunables via actions; they may
**refresh** reported tunables from the lab; they **capture** measurables.
Optimizers consume measurables (and may actuate tunables). A camera frame is
not a free variable assigned in Python.

## Why the split matters

| Ambiguity | Lab-model resolution |
|-----------|----------------------|
| Did the robot reach the commanded pose? | Compare `nominal_pose` (command) to `reported_pose` (same tunable family) |
| Why does the ghost lead the solid? | Move in progress—command leads report until settle / refresh |
| Is pose a measurable? | No—pose readback refreshes the tunable; cameras and scores are measurables |
| Can a script invent a camera image? | No—only capture primitives refresh images |

## Telemetry (preview)

In addition to state control (tunables / measurables), components may declare
**telemetry**: live feeds and TeleOp sessions. That is the continuous
observation channel, covered with TeleOp and Optimize in the next chapters.

## Practice

1. Wiki → **Catalog** → select a camera tag → read **Physical interpretation**.
2. Inspect **Measurables**—for example `camera_image` as an optical observation,
   not only an `HxWx3` array. Pose fields belong with **tunables** (command vs
   report), not here.
3. In the Twin, move a part and observe ghost (command) versus solid (report).

Next: [Primitives](#)—the actions that change the lab.
