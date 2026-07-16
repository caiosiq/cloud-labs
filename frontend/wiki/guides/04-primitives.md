# Primitives — the language of action

If components are the nouns of the laboratory, **primitives** are the verbs: a
validated lab action such as moving a tag, recording a camera, starting TeleOp,
or running OPTIMIZE. The Twin, the Command Console, recipes, and the
`cloudlabs` SDK share this vocabulary. That shared action set is the product
contract — the base of Cloud Labs.

## Semantics

Authors do not assign abstract pose fields out of band. They request that the
lab **perform** an action; the edge **LabCommunicator** maps the request to
instrument calls (or a faithful mock). While the lab is `BUSY` or
`OPTIMIZING`, conflicting commands wait rather than interleaving unsafely.

Exact availability on a given part still depends on that component’s catalog
`capabilities.primitives`. The **Backends** hub lists the subset declared for
each tag. Below is the **full** platform inventory.

## Full inventory

### Read (queries)

| Primitive | Meaning |
|-----------|---------|
| `GET_TUNABLES` | Read tunable values for one tag |
| `GET_MEASURABLES` | Read current measurable values (may be stale / null) |

### Observe / measure

| Primitive | Meaning |
|-----------|---------|
| `RECORD_MEASURABLES` | Capture fresh observations (e.g. camera frame) |
| `EVAL_KERNEL` | One-shot probe: capture and evaluate a measurement kernel |
| `SCAN` | Camera / localization scan pass (pose refresh path) |

### Motion and placement

| Primitive | Meaning |
|-----------|---------|
| `MOVE_COMPONENT` | Place a part at a breadboard pose |
| `STORE_COMPONENT` | Move a part into inventory / storage |
| `PLACE_FROM_STORAGE` | Bring a stored part onto the breadboard |
| `AFFIRM_PLACED_AT_CURRENT` | Affirm hand-placed pose after leaving storage |
| `REPACK_STORAGE` | Repack a storage slot |
| `RECENTER_IN_STORAGE` | Recenter a part in its inventory slot |
| `REMOVE` | Remove a component from the active lab |

### Motors and other setpoints

| Primitive | Meaning |
|-----------|---------|
| `MOVE_MOTOR` | Absolute / relative motor motion |
| `SET_MOTOR_SETPOINT` | Write motor setpoints (`nominal_motor_positions`) |
| `MOTOR_SEND_HOME` | Macro: home a motor (expands to motion) |
| `MOTOR_SET_ZERO` | Zero / reference a motor angle |
| `SET_EXPOSURE` | Camera exposure intent |
| `SET_LASER_OUTPUT` | Laser output-power intent |
| `APPLY_TUNABLES_PATCH` | Macro: patch several tunables in one request |

### In-air manipulation

| Primitive | Meaning |
|-----------|---------|
| `PICK_COMPONENT` | Pick a part into the gripper |
| `HOVER` | Hold / move while held above the table |
| `PLACE_FROM_HOVER` | Place from hover onto the breadboard |
| `SCAN_ROTATE_IN_PLACE` | Rotate while held / scanned for alignment |
| `CONFIRM_HOLDING_TAG` | Confirm which tag is in the gripper |

### TeleOp and live feed

| Primitive | Meaning |
|-----------|---------|
| `START_TELEOP` | Acquire per-component TeleOp lease |
| `END_TELEOP` | Release TeleOp lease |
| `TELEOP_JOG` | Push one jog frame under TeleOp |
| `TELEOP_GOTO` | Absolute goto at TeleOp speed |
| `START_LIVE_FEED` | Open a live video channel for a camera tag |
| `END_LIVE_FEED` | Close that live channel |

### Closed-loop

| Primitive | Meaning |
|-----------|---------|
| `OPTIMIZE` | Closed-loop (or legacy) optimization session on the edge |

## What is not a primitive

- **Registering a kernel** — packaging an artifact (upload), not actuating
  hardware. Kernels are **inputs** to `EVAL_KERNEL` / `OPTIMIZE`.
- **Listing backends** — coordinator bookkeeping.
- **Reading whole lab state** — a coordinator query; formal per-tag reads are
  `GET_TUNABLES` / `GET_MEASURABLES`.

## Session lease

Before scripts issue mutating primitives, they **`connect()`** and acquire a
**session lease**: exclusive right to mutate that backend for the duration of
the session. When the lease ends, another author may enter.

## Practice

```python
from cloudlabs import connect

with connect("mock.default", base_url="http://127.0.0.1:8000") as lab:
    lab.components.tag_20.move(x=12.5).wait_until_idle()
```

Replace `mock.default` with your backend id. The fluent `move` call is sugar
over a `MOVE_COMPONENT` primitive—the same family of requests the Twin sends.

Next: [Version control and lab history](#)—commits, diffs, and the empty baseline.
