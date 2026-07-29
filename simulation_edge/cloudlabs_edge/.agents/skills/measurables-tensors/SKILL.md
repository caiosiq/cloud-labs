---
name: measurables-tensors
description: >-
  Builds canonical camera_image MeasurableTensor envelopes, LazyRefs, and
  latched epochs on a Cloud Labs edge. Use when implementing RECORD_MEASURABLES,
  serving /measurables/... bytes, changing image metadata, or resolving tensors
  for scripts.
---

# Measurables and tensors

## Do

- Emit `camera_image` with the **canonical** envelope (dtype/domain/axes/units/layout)
  matching the coordinator schema — prefer a local `measurables_schema.py` helper.
- Return heavy pixels as a **LazyRef** (`kind=url`, href under this edge); cache the
  exact JPEG from the latched capture so the URL is not a fresh live frame.
- Open/close a latch around capture (`latch.py`); stamp `epoch_ms` + `latch_quality`.
- Serve `GET /measurables/{tag}/camera_image.jpg` from that cache.

## Do not

- Do not invent axis labels, swap H/W, or claim `hardware_triggered_latch` without hardware.
- Do not put full BGR arrays in lab-state JSON.
- Do not let live-stream endpoints substitute for `RECORD_MEASURABLES` science.

## Typical files

- `adapters/observe.py`, `latch.py`, measurable schema helper, stream routes in the server app.

## Check

Certify must pass the measurable-envelope conformance check after changes.
