# Primitive UI contract (v1)

## Rule

**Tunables / measurables / telemetry panels are read-only.** Every state change uses a **named primitive** with an explicit parameter object (no `parameters: {}` stubs).

## Tunable field → primitive

| Tunable field | Primitive | Parameters |
|---------------|-----------|------------|
| `exposure_time_ms` | `SET_EXPOSURE` | `{ exposure_time_ms }` |
| `nominal_motor_positions` | `SET_MOTOR_SETPOINT` | `{ motor_id, angle_deg }` |
| `nominal_pose` (table) | `MOVE_COMPONENT` | `{ target_x, target_y, rotation }` |
| `nominal_pose` (held) | `HOVER` / `PLACE_FROM_HOVER` | pose + `z` for hover |
| `storage` / presence | `STORE_COMPONENT`, `PLACE_FROM_STORAGE`, … | per primitive |

Relative motor jog (does not update nominal): `MOVE_MOTOR` with `{ motor_id, distance }`.

## Macro: `APPLY_TUNABLES_PATCH`

Recipe convenience only. Fixed order in `backend/lab_primitives/macros/apply_tunables_patch.py`:

1. Hyperparameters (`exposure_time_ms`, …)
2. Motor setpoints (`nominal_motor_positions`)
3. Placement intent (`nominal_pose`, `presence`, `storage`) — **no motion**
4. Motion primitives are **not** invoked from this macro

## Frontend layout

```
Component popup
├── TUNABLES / MEASURABLES / TELEMETRY (read-only widgets)
└── PRIMITIVES (one region per catalog allow-list entry)
```

Implementations: `frontend/js/primitives/` (UI catalog), `backend/lab_primitives/` (dispatch).

## Measurables

Each catalog `capabilities.measurables` entry names a field and a **widget** (`ImageViewer`, `MotorRotationsReadout`, `NumberBadge`, …). `RECORD_MEASURABLES` fills those fields; the read-only MEASURABLES panel renders them. Mock fills every declared measurable for the tag (synthetic camera PNG, tracked motor angles, etc.).
