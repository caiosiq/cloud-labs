"""Cartesian motion and inventory primitives for placed or stored components.

These functions implement the motion half of the Edge Contract: moving a
tag on the bench, in-air pick/hover/place, and storage-grid operations.
Wire each body to the existing OpticalExperiment ``*_cloudlab`` helpers (or
a future motion backend) without changing the function names Twin and the
SDK already call through ``POST /execute``.

Unless noted, ``args`` is the execute ``args`` object and every function
returns a JSON-serializable ``dict`` that will become the execute
``result`` (include ``tag_id`` and any updated pose or presence fields the
coordinator should merge into lab state).

simulation_edge implements table and storage moves through ``SimulationHost``;
in-air pick/hover helpers remain ``NotImplementedError`` until ported.
"""

from __future__ import annotations

from typing import Any

from adapters import context


async def move_component(args: dict[str, Any]) -> dict[str, Any]:
    """Move a placed component to an absolute bench pose (MOVE_COMPONENT).

    Parameters
    ----------
    args:
        Must include the target tag as ``tag_id`` or ``target_id``. Pose is
        usually ``target_x``, ``target_y``, and ``rotation`` in lab
        millimetres / degrees (same convention as Twin nominal_pose). Optional
        speed or frame hints may appear as lab-defined keys.

    Returns
    -------
    dict
        At least ``{"tag_id": str}`` and the pose that was commanded or
        achieved, for example ``{"tag_id": "tag_20", "pose": {"x": ..., "y": ...,
        "rotation": ...}}``.
    """
    tag = context.tag_from_args(args)
    lab = context.get_lab()
    x = float(args.get("x") if args.get("x") is not None else args.get("target_x") or 0.0)
    y = float(args.get("y") if args.get("y") is not None else args.get("target_y") or 0.0)
    rotation = float(
        args.get("rotation")
        if args.get("rotation") is not None
        else args.get("yaw")
        if args.get("yaw") is not None
        else 0.0
    )
    return await lab.move_component(tag, x=x, y=y, rotation=rotation)


async def pick_component(args: dict[str, Any]) -> dict[str, Any]:
    """Lift a placed component into the held/in-air state (PICK_COMPONENT).

    Parameters
    ----------
    args:
        ``tag_id`` / ``target_id`` of the component to pick. Optional approach
        offsets may be supplied by the lab.

    Returns
    -------
    dict
        Confirmation including ``tag_id`` and presence/holding state after the
        pick (for example ``{"tag_id": "...", "holding": true}``).
    """
    raise NotImplementedError("Phase 6: pick_component_cloudlab")


async def hover_component(args: dict[str, Any]) -> dict[str, Any]:
    """Move a held component to a hover pose above the bench (HOVER).

    Parameters
    ----------
    args:
        ``tag_id`` / ``target_id`` plus the hover pose fields the lab uses
        (often the same x/y/rotation keys as MOVE_COMPONENT).

    Returns
    -------
    dict
        ``tag_id`` and the hover pose that was reached.
    """
    raise NotImplementedError("Phase 6: hover_component_cloudlab")


async def place_from_hover(args: dict[str, Any]) -> dict[str, Any]:
    """Set a held component down from hover onto the bench (PLACE_FROM_HOVER).

    Parameters
    ----------
    args:
        ``tag_id`` / ``target_id`` and the final place pose.

    Returns
    -------
    dict
        ``tag_id``, resulting presence (placed), and final pose.
    """
    raise NotImplementedError("Phase 6: place_from_hover_cloudlab")


async def confirm_holding_tag(args: dict[str, Any]) -> dict[str, Any]:
    """Confirm which tag the arm believes it is holding (CONFIRM_HOLDING_TAG).

    Parameters
    ----------
    args:
        Usually ``tag_id`` expected to be in the gripper; labs may also pass
        vision confirmation flags.

    Returns
    -------
    dict
        ``{"tag_id": str, "holding": bool}`` (and optional confidence).
    """
    raise NotImplementedError("Phase 6: confirm holding tag")


async def store_component(args: dict[str, Any]) -> dict[str, Any]:
    """Move a component from the bench into a storage slot (STORE_COMPONENT).

    Parameters
    ----------
    args:
        ``tag_id`` / ``target_id``; optional explicit slot coordinates if the
        lab does not assign slots automatically.

    Returns
    -------
    dict
        ``tag_id``, storage slot id/indices, and presence ``"STORED"``.
    """
    tag = context.tag_from_args(args)
    return await context.get_lab().store_component(tag)


async def place_from_storage(args: dict[str, Any]) -> dict[str, Any]:
    """Bring a stored component onto the bench (PLACE_FROM_STORAGE).

    Parameters
    ----------
    args:
        ``tag_id`` / ``target_id`` and the destination pose
        (``target_x`` / ``target_y`` / ``rotation``).

    Returns
    -------
    dict
        ``tag_id``, presence ``"PLACED"``, and final pose.
    """
    tag = context.tag_from_args(args)
    x = float(args.get("x") if args.get("x") is not None else args.get("target_x") or 0.0)
    y = float(args.get("y") if args.get("y") is not None else args.get("target_y") or 0.0)
    rotation = float(
        args.get("rotation")
        if args.get("rotation") is not None
        else args.get("yaw")
        if args.get("yaw") is not None
        else 0.0
    )
    return await context.get_lab().place_from_storage(
        tag,
        x=x,
        y=y,
        rotation=rotation,
    )


async def affirm_placed_at_current(args: dict[str, Any]) -> dict[str, Any]:
    """Affirm that a component is correctly placed at its current pose.

    Parameters
    ----------
    args:
        ``tag_id`` / ``target_id`` of the component being affirmed.

    Returns
    -------
    dict
        ``tag_id`` and any bookkeeping fields the lab uses for affirmations.
    """
    raise NotImplementedError("Phase 6: affirm placed")


async def repack_storage_slot(args: dict[str, Any]) -> dict[str, Any]:
    """Repack or tidy a storage slot (REPACK_STORAGE).

    Parameters
    ----------
    args:
        Identifies the slot and/or ``tag_id`` to repack.

    Returns
    -------
    dict
        Slot identity and outcome status.
    """
    tag = context.tag_from_args(args)
    return await context.get_lab().repack_storage_slot(tag)


async def recenter_stored_in_inventory(args: dict[str, Any]) -> dict[str, Any]:
    """Recenter a stored part inside its inventory cell (RECENTER_IN_STORAGE).

    Parameters
    ----------
    args:
        ``tag_id`` / ``target_id`` of the stored component.

    Returns
    -------
    dict
        ``tag_id`` and updated storage pose or slot metadata.
    """
    tag = context.tag_from_args(args)
    return await context.get_lab().recenter_stored_in_inventory(tag)


async def remove_component(args: dict[str, Any]) -> dict[str, Any]:
    """Remove a component from the runtime bench model (REMOVE).

    Parameters
    ----------
    args:
        ``tag_id`` / ``target_id`` to remove. Physical park behaviour is
        lab-defined; the contract requires a clear success/failure result.

    Returns
    -------
    dict
        ``{"tag_id": str, "removed": true}`` (or equivalent).
    """
    raise NotImplementedError("Phase 6: remove component")
