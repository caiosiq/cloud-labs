# TeleOp operator runbook

Operator guide for per-component TeleOp on the real bench (cloud-labs + `lab_automation`).

## Before you start

1. Confirm the lab status is **IDLE** (or **HOLDING** with the same tag you will teleop, for in-air work).
2. Ensure no other component has an active TeleOp lease (only one session at a time).
3. For **pose3d** (held-part) TeleOp: run **PICK** first and verify the gripper is closed.

## Starting TeleOp

1. Select the table optic in the UI.
2. Click **Start TeleOp** (`START_TELEOP`).
3. Wait until the component shows **ready** — the backend enters `LiveControlSession` on the robot.
4. Use **goto** / jog controls; live pose polls at the rate configured in the catalog (`LivePosePoll`).

### Modes

| Mode | When | Controls |
|------|------|----------|
| **rz** | Part on table | Rotation only |
| **pose3d** | Part in gripper | X, Y, Z, rotation |

## During TeleOp

- **Stale lease watchdog:** If the UI disconnects, the sweeper clears the lease after `teleop_ttl_ms` (see `lab_manifest.json`). Motion stops when the hardware session exits.
- **Hardware mutex:** Only one robot live-control session runs at a time. A second `START_TELEOP` while hardware is busy is refused.
- **Gripper slip (pose3d only):** If the gripper opens while teleoping a held part, the session **estops** automatically: hardware live control stops, the lease is cleared, and `telemetry.teleop.last_error` records the reason.

## Ending TeleOp

1. Click **End TeleOp** (`END_TELEOP`) when finished.
2. The final live pose is written to session/meas pose as configured.
3. For on-table parts, confirm placement if needed (`AFFIRM_PLACED_AT_CURRENT`).

## Failure recovery

| Symptom | Action |
|---------|--------|
| TeleOp stuck on “starting” | Check backend logs for prepare failure; call **End TeleOp**, fix holding/pick state, retry. |
| `last_error` after slip | Re-pick the part if dropped; confirm gripper closed; restart TeleOp. |
| Stale lease / ghost session | Wait for TTL sweep or call **End TeleOp**; refresh lab state. |
| Robot still moving after UI disconnect | Sweeper + hardware stop should halt motion; if not, use the physical e-stop and contact the bench owner. |
| HOLDING_UNCONFIRMED on boot | Gripper was closed at startup without a known tag — use **Confirm holding tag** before other commands. |

## Camera preview during TeleOp

Beam camera (`tag_22`) uses the **teleop** preview profile (fast JPEG poll on `/telemetry/preview`), not the slow CAP capture path. If preview is blank, check the recorder service and `table_cam_preview.json` teleop block.

## Related docs

- `docs/REAL_BENCH_ROADMAP.md` — Phase 6–10 TeleOp and hardening
- `docs/LAB_AUTOMATION_AND_TELEOP_ANALYSIS.md` — architecture notes
