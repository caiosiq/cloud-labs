"""Motor primitives → mock host UC dispatch."""

from __future__ import annotations

from typing import Any

from adapters import context


async def move_motor(args: dict[str, Any]) -> dict[str, Any]:
    return await context.run_uc("MOVE_MOTOR", args)


async def set_motor_setpoint(args: dict[str, Any]) -> dict[str, Any]:
    return await context.run_uc("SET_MOTOR_SETPOINT", args)


async def motor_set_zero(args: dict[str, Any]) -> dict[str, Any]:
    return await context.run_uc("MOTOR_SET_ZERO", args)


async def motor_send_home(args: dict[str, Any]) -> dict[str, Any]:
    return await context.run_uc("MOTOR_SEND_HOME", args)
