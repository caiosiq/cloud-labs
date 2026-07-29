# Agent skills for this edge

This folder ships with every Edge Contract skeleton. It is **tool-agnostic**:
read it yourself, or point any AI assistant at a skill when filling Phase 6
adapters. These are not a second API — they coach how to honor the contract
already defined by `capabilities.json`, `SKELETON.md`, and
`cloudlabs-edge certify`.

Start with [`skills/cloudlabs-context`](skills/cloudlabs-context/SKILL.md) if you
need the product mental model (same story as scientist onboarding). Then open a
topic skill for the adapter you are filling.

| Skill | Open when you are… |
|-------|--------------------|
| [`skills/cloudlabs-context`](skills/cloudlabs-context/SKILL.md) | Orienting: what Cloud Labs is, who talks to whom |
| [`skills/edge-contract`](skills/edge-contract/SKILL.md) | Adding endpoints, verbs, or side APIs |
| [`skills/measurables-tensors`](skills/measurables-tensors/SKILL.md) | Capturing images / shaping MeasurableTensor |
| [`skills/latency-channels`](skills/latency-channels/SKILL.md) | Teleop, live video, or long jobs |
| [`skills/kernels-optimize`](skills/kernels-optimize/SKILL.md) | Scoring frames or closed-loop OPTIMIZE |
| [`skills/frames-calibration`](skills/frames-calibration/SKILL.md) | Table↔robot transforms |
| [`skills/inventory-honesty`](skills/inventory-honesty/SKILL.md) | Store / place / inventory verbs |

After each adapter change, run::

    cloudlabs-edge doctor --path .
    cloudlabs-edge certify <edge-url> --path . --profile stub
