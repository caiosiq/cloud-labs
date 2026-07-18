"""Tunable writes — soft bookkeeping on SimulationHost poses / no-ops."""

from __future__ import annotations

from typing import Any

from adapters import context


async def set_exposure_time_ms(args: dict[str, Any]) -> dict[str, Any]:
    tag = context.tag_from_args(args, "tag_22")
    ms = float(args.get("exposure_ms") or args.get("value") or 0.0)
    return {"tag_id": tag, "exposure_ms": ms, "backend": "soft"}


async def set_output_power_mw(args: dict[str, Any]) -> dict[str, Any]:
    tag = context.tag_from_args(args, "tag_22")
    mw = float(args.get("power_mw") or args.get("value") or 0.0)
    return {"tag_id": tag, "power_mw": mw, "backend": "soft"}
