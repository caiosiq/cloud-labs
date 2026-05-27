# OPTIMIZE contract (cloud-labs ↔ lab_automation)

**Status:** V1 implemented (May 2026) — ``lab_automation.managers.optimize_runner.run_optimize``.

## Session model

- One `OPTIMIZE` command → `system_status = OPTIMIZING` for the whole run.
- cloud-labs owns refusal, live session, WebSocket telemetry, and final tunable commit.
- lab_automation owns hardware loops, vision metrics, and algorithm constants.

## Operator parameters (UI / console / `POST /api/command`)

| Field | NEWTON | COBYLA | Notes |
|-------|--------|--------|-------|
| `strategy` | ✓ | ✓ | `NEWTON` \| `COBYLA` |
| `sensor_component` | ✓ | ✓ | Catalog camera tag (e.g. `tag_22`) |
| `loss_metric` | ✓ | ✓ | Must match strategy allow-list |
| `axis` | ✓ | — | Table axis to nudge: `x` \| `y` |
| `tolerance_ratio` | ✓ | — | Convergence band as fraction of image width |
| `video_exposure` | ✓ | ✓ | Recorder stream exposure (seconds) |

Legacy alias: `exposure` → `video_exposure`.

## lab_automation-owned constants (not UI)

- `NEWTON_OPTIMIZE_HOVER_LIFT_MM` (default `5.0`)
- `NEWTON_INITIAL_MOVE_MM`, `NEWTON_MAX_STEPS`
- COBYLA `max_iter`, `rhobeg`, `rhoend`, settle time, `objective_threshold`
- `output_dir` / run dir — communicator internal only

## Loss metrics

| Strategy | Allowed | Measurement |
|----------|---------|-------------|
| NEWTON | `centroid_match` | Camera **pixel X** centroid vs target |
| COBYLA | `reference_match` | Two-beam subtraction distance |

Invalid `loss_metric` for the chosen strategy → refuse before run.

## Newton physics (V1)

1. Pick (mode 0).
2. Lift by `NEWTON_OPTIMIZE_HOVER_LIFT_MM` (mode 0).
3. Loop (mode 0 hover per step):
   - Move along table `axis` while gripper closed at hover Z.
   - **Settle** → grab live frame → compute pixel-X loss.
   - Emit **`step`**; pose publisher emits **`motion`** during move.
4. Final `place_from_hover` (mode 0).

Vision always reads **pixel X**; table move uses operator `axis`.

## COBYLA physics (V1)

- Mode 0 motor moves with backlash pattern, settle, live frame, reference subtraction.
- Telemetry: **`step`** only (discrete motor positions + loss).

## Telemetry: `motion` vs `step`

WebSocket `/api/components/{tag_id}/optimize/session` (and HTTP live-pose fallback):

### `motion` (high rate, ~25–50 Hz)

```json
{ "type": "motion", "pose": { "x", "y", "rotation", "z" }, "executing": true }
```

- Updates canvas / `teleopLivePose` only.
- Does **not** append to loss chart.

### `step` (once per algorithm iteration)

```json
{
  "type": "step",
  "iteration": 3,
  "loss": 12.4,
  "pose": { "x", "y", "rotation" },
  "motor_positions": { "1": 150.0 }
}
```

- Updates optimization preview chart and Step/Loss readout.
- COBYLA: primary telemetry type.

Backward compat: untyped tick with `iteration`/`loss` is treated as `step`.

## lab_automation API (target)

```python
def run_optimize(
    experiment,
    *,
    spec: dict,
    on_motion: Callable[[dict], None],
    on_step: Callable[..., None],
) -> dict:
    """Returns {score, final_pose?, final_motor_positions?}."""
```

Communicator maps `on_motion` / `on_step` to `_optimize_live_pose_update`.

## Future (out of V1)

- IBVS / continuous strategies → mode 1 servoj
- `axis_deviation` loss metric for NEWTON
