"""Per-motor angle primitives for wifi steppers and similar drivers.

These functions change ``nominal_motor_positions`` (or the lab equivalent)
for a tagged component. Angles are in degrees unless your lab documents
otherwise; keep the same units Twin already shows. Return dicts that echo
``tag_id``, ``motor_id``, and the angle that was commanded or read back.

simulation_edge v1 does not implement motors yet — keep this surface so the
contract shape stays documented.
"""

from __future__ import annotations

from typing import Any


async def move_motor(args: dict[str, Any]) -> dict[str, Any]:
    """Command a relative or absolute motor move (MOVE_MOTOR).

    Parameters
    ----------
    args:
        ``tag_id`` / ``target_id``, integer ``motor_id``, and a distance or
        angle field (commonly ``distance`` or ``angle_deg`` — match the
        existing wifi_stepper API).

    Returns
    -------
    dict
        ``{"tag_id": str, "motor_id": int, "angle_deg": float}`` after the move.
    """
    raise NotImplementedError("Phase 6: wifi_stepper move_motor")


async def set_motor_setpoint(args: dict[str, Any]) -> dict[str, Any]:
    """Set an absolute motor angle setpoint (SET_MOTOR_SETPOINT).

    Parameters
    ----------
    args:
        ``tag_id`` / ``target_id``, ``motor_id``, and absolute ``angle_deg``.

    Returns
    -------
    dict
        The tag, motor id, and absolute angle now in effect.
    """
    raise NotImplementedError("Phase 6: set motor setpoint")


async def motor_set_zero(args: dict[str, Any]) -> dict[str, Any]:
    """Define the current mechanical position as zero (MOTOR_SET_ZERO).

    Parameters
    ----------
    args:
        ``tag_id`` / ``target_id`` and ``motor_id``.

    Returns
    -------
    dict
        Confirmation with ``tag_id``, ``motor_id``, and ``angle_deg`` of ``0``
        (or the lab's zero convention).
    """
    raise NotImplementedError("Phase 6: motor set zero")


async def motor_send_home(args: dict[str, Any]) -> dict[str, Any]:
    """Send a motor to its home angle (MOTOR_SEND_HOME).

    On the coordinator this is often a macro that expands to MOVE_MOTOR. The
    edge may still expose a direct implementation for local callers.

    Parameters
    ----------
    args:
        ``tag_id`` / ``target_id`` and ``motor_id``.

    Returns
    -------
    dict
        ``tag_id``, ``motor_id``, and the home angle reached.
    """
    raise NotImplementedError("Phase 6: motor send home (or rely on coordinator macro)")
