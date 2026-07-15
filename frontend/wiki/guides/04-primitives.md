# Primitives — the language of action

If components are the nouns of the laboratory, **primitives** are the verbs: a
validated lab action such as moving a tag, recording a camera, starting TeleOp,
or running OPTIMIZE. The Twin, the Command Console, recipes, and the
`cloudlabs` SDK share this vocabulary. That shared action set is the product
contract.

## Semantics

Authors do not assign abstract pose fields out of band. They request that the
lab **perform** an action; the edge **LabCommunicator** maps the request to
instrument calls (or a faithful mock). While the lab is `BUSY` or
`OPTIMIZING`, conflicting commands wait rather than interleaving unsafely.

## Common primitives

| Primitive | Meaning |
|-----------|---------|
| `MOVE_COMPONENT` | Place a part at a breadboard pose |
| `SET_MOTOR_SETPOINT` / motor moves | Adjust tip/tilt or rotation stages |
| `RECORD_MEASURABLES` | Capture a fresh observation (e.g. camera frame) |
| `EVAL_KERNEL` | One-shot probe: capture and evaluate a measurement function |
| `OPTIMIZE` | Closed-loop (or legacy) optimization session |
| `START_TELEOP` / `TELEOP_JOG` | Human-in-the-loop control session |
| `START_LIVE_FEED` | Open a live video channel |

Exact availability depends on each component’s catalog capabilities. The
**Catalog** tab of this Wiki lists what the selected backend declares.

## What is not a primitive

- **Registering a kernel** — packaging an artifact (upload), not actuating
  hardware.
- **Listing backends** — coordinator bookkeeping.
- **Reading lab state** — a query, not a mutation (except formalized read
  primitives such as `GET_MEASURABLES`).

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

Next: [TeleOp, telemetry, and optimize](#)—live control and closed-loop work.
