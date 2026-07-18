"""Exposure / laser tunable writes → mock host UC dispatch."""

from __future__ import annotations

from typing import Any

from adapters import context


async def set_exposure_time_ms(args: dict[str, Any]) -> dict[str, Any]:
    return await context.run_uc("SET_EXPOSURE", args)


async def set_output_power_mw(args: dict[str, Any]) -> dict[str, Any]:
    return await context.run_uc("SET_LASER_OUTPUT", args)
