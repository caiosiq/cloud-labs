# Why Cloud Labs

Autonomous optics work typically sits behind fragmented software: vendor drivers,
ad hoc notebooks, and lab-specific scripts that diverge from whatever the UI
last executed. Cloud Labs addresses that fragmentation by exposing a **shared
laboratory language**—the same actions available in the Twin UI are available
from Python, under one mental model whether the bench is a teaching mock or a
physical arm with cameras.

The lab is modeled as an **Optical Processing Unit (OPU)**: a remotely
controlled machine that processes light paths. Authors do not SSH into the
instrument PC to drive hardware directly. They express **intent**—move,
measure, optimize—as validated actions. A **coordinator** validates and
schedules that intent; an **edge** process that owns the instruments executes
it. The following chapters develop this architecture in detail.

## Design goals for experimentalists

1. **One vocabulary for UI and scripts.** Twin panels and the `cloudlabs` SDK
   issue the same **primitives**.
2. **Components as scientific objects.** Each part separates *identity*
   (parameters), *commanded and reported degrees of freedom* (tunables), and
   *observations without a matching setpoint* (measurables) — rather than an
   undifferentiated configuration blob.
3. **Exclusive lease.** One session author mutates a backend at a time, which
   prevents silent contention between notebooks or UI windows.
4. **Closed-loop on the edge.** Capture → score → actuate loops that must run
   near camera rates stay next to the hardware; the author laptop is not on the
   critical path for every frame.
5. **Mock-first.** The same APIs support teaching, CI, and demos without a
   robot, then carry forward to a real communicator.
6. **Catalog and kernels with interpretation.** Frozen experiment pins and
   measurement functions are documented with physical meaning, not only tensor
   shapes.

## Surfaces

| Surface | Role |
|---------|------|
| **Twin** (`/twin`) | Interactive control and breadboard visualization |
| **Scripts** (`cloudlabs`) | Reproducible experiments and batch jobs |
| **Operations** | Job and edge health |
| **Catalog** (app + Wiki tab) | Frozen configurations and live capability lookup |
| **This Wiki** | Usage and architecture (Learn), then live Catalog for a chosen backend |

## Practice

1. Open the **Twin** and select a mirror. Note ghost (commanded pose) versus
   solid (reported pose)—same tunable family, not a measurable.
2. Return here → **Catalog** → the same tag → read **Physical interpretation**.
3. With the coordinator running, pick a backend at the boot gate, then run
   `python scripts/language/01_hello_lab.py --backend <id>`.

Next: [The OPU and three faces](#)—Client, Coordinator, and Edge.
