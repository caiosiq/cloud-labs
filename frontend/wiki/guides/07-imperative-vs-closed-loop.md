# Imperative vs closed-loop

Experiments choose between two execution modes according to **latency** and
**where the decision loop runs**.

![Imperative vs closed-loop](/static/wiki/guides/figures/execution-modes.svg)

*Imperative: one HTTP round-trip per decision. Closed-loop: OPTIMIZE job; the edge runs the inner loop locally.*

## Imperative — author in the loop

Each step is a primitive round-trip: decide → edge acts → observe → decide
again. Author-side Python (plots, SciPy, branching) sits between steps.

```python
lab.components.tag_20.move(x=12.5).wait_until_idle()
score = lab.probe_kernel("tag_22", "camera_image", kernel_id="demo.image_mean_score")
```

| | |
|--|--|
| **Appropriate for** | Setup, teaching, slow sweeps, branching that belongs on the laptop |
| **Cost** | One network RTT per step; poor fit for hundreds of camera evaluations |

## Closed-loop — edge in the loop

The author submits an **OPTIMIZE** job: variables, objective, kernels. The edge
runs capture → score → actuate many times **locally**. The laptop waits for the
job result (and optional progress).

```python
lab.run_cobyla(
    variables=[...],
    match_kernel=lab.kernel_match("tag_22", "camera_image",
                                  kernel_id="demo.image_mean_score", target=0.35),
)
```

| | |
|--|--|
| **Appropriate for** | Alignment loops, overnight optimizers, frame-rate-sensitive work |
| **Cost** | Targets and formulas must be encoded in the job IR / kernels—no mid-loop laptop callback |

## Shared rule

Both modes speak **primitives**. Closed-loop is not a second language; it is
OPTIMIZE owning a long-running action on the edge.

## Practice

- Imperative: `01_hello_lab.py`
- Closed-loop: `03_closed_loop_catalog.py`
- Jobs / DAG: `05_jobs_and_modes.py`

Next: [Connecting and backends](#)—how Twin and scripts pick a lab.
