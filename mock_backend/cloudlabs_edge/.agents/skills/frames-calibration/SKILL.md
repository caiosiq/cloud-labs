---
name: frames-calibration
description: >-
  Owns table-to-robot frame transforms on a Cloud Labs edge with fail-loud
  calibration. Use when implementing MOVE_COMPONENT, teleop pose conversion,
  or wiring LAB_ROBOT_* calibration constants.
---

# Frame calibration

## Do

- Keep table/lab millimetres and degrees as the **only** language Twin/SDK speak.
- Convert to the robot frame inside the edge (e.g. `runtime/frames.py`) at the last moment.
- Fail loud (`FrameCalibrationUnavailable` or equivalent) when a real transform is
  needed and calibration is missing.
- Allow identity only via explicit opt-in (mock / `LAB_ROBOT_FRAME_IDENTITY=1`).
- Report calibration status on `GET /bench` without raising.

## Do not

- Do not silently default a real arm to identity.
- Do not push robot-frame coordinates into the coordinator or SDK.
- Do not scatter rotation/origin/sign constants across adapters — one module owns them.

## Env / configure

Typical names: `LAB_ROBOT_TABLE_ROTATION_RAD`, `LAB_ROBOT_ORIGIN_X_MM` / `_Y_MM`,
`LAB_ROBOT_SIGN_X` / `_Y`, `LAB_ROBOT_RZ_OFFSET_DEG`. Prefer `frames.configure(...)`
in tests; production should set real lab constants.
