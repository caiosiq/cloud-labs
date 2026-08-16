"""Motion + inventory primitives → mock host UC dispatch."""

from __future__ import annotations

from typing import Any

from adapters import context


async def move_component(args: dict[str, Any]) -> dict[str, Any]:
    return await context.run_uc("MOVE_COMPONENT", args)


async def pick_component(args: dict[str, Any]) -> dict[str, Any]:
    return await context.run_uc("PICK_COMPONENT", args)


async def hover_component(args: dict[str, Any]) -> dict[str, Any]:
    return await context.run_uc("HOVER", args)


async def place_from_hover(args: dict[str, Any]) -> dict[str, Any]:
    return await context.run_uc("PLACE_FROM_HOVER", args)


async def confirm_holding_tag(args: dict[str, Any]) -> dict[str, Any]:
    return await context.run_uc("CONFIRM_HOLDING_TAG", args)


async def store_component(args: dict[str, Any]) -> dict[str, Any]:
    return await context.run_uc("STORE_COMPONENT", args)


async def place_from_storage(args: dict[str, Any]) -> dict[str, Any]:
    return await context.run_uc("PLACE_FROM_STORAGE", args)


async def affirm_placed_at_current(args: dict[str, Any]) -> dict[str, Any]:
    return await context.run_uc("AFFIRM_PLACED_AT_CURRENT", args)


async def repack_storage_slot(args: dict[str, Any]) -> dict[str, Any]:
    return await context.run_uc("REPACK_STORAGE", args)


async def recenter_stored_in_inventory(args: dict[str, Any]) -> dict[str, Any]:
    return await context.run_uc("RECENTER_IN_STORAGE", args)


async def remove_component(args: dict[str, Any]) -> dict[str, Any]:
    return await context.run_uc("REMOVE", args)
