"""READY gate: refuse motion/teleop until SYNC_RUNTIME has succeeded."""

from __future__ import annotations

from adapters import context

# Primitives that require lab-state ``runtime_sync.status == ready``.
# Observe / live-feed / RECORD / SYNC itself stay allowed while syncing.
REQUIRES_RUNTIME_READY = frozenset(
    {
        "MOVE_COMPONENT",
        "MOVE_MOTOR",
        "SET_MOTOR_SETPOINT",
        "MOTOR_SET_ZERO",
        "MOTOR_SEND_HOME",
        "PICK_COMPONENT",
        "HOVER",
        "PLACE_FROM_HOVER",
        "STORE_COMPONENT",
        "PLACE_FROM_STORAGE",
        "AFFIRM_PLACED_AT_CURRENT",
        "REPACK_STORAGE",
        "RECENTER_IN_STORAGE",
        "REMOVE",
        "START_TELEOP",
        "TELEOP_JOG",
        "TELEOP_GOTO",
        "OPTIMIZE",
        "SET_EXPOSURE",
        "SET_LASER_OUTPUT",
    }
)


def ensure_runtime_ready(primitive: str) -> None:
    """Raise if ``primitive`` needs READY and SYNC has not completed."""
    if primitive not in REQUIRES_RUNTIME_READY:
        return
    lab = context.get_lab()
    ready = getattr(lab, "is_runtime_ready", None)
    if callable(ready) and ready():
        return
    status = getattr(lab, "runtime_sync_status", lambda: "pending")()
    print(
        f"[lab_init] refuse primitive={primitive!r} "
        f"runtime_sync={status!r} (lab not initialized)"
    )
    raise RuntimeError(
        f"refused: runtime_sync status={status!r}; "
        "SYNC_RUNTIME must succeed before motion / teleop"
    )
