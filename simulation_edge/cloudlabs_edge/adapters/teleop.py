"""Teleop lease + pose samples via SimulationHost soft poses."""

from __future__ import annotations

from typing import Any

from adapters import context
from latch import now_epoch_ms


async def start_teleop(tag_id: str) -> dict[str, Any]:
    tag = tag_id or "tag_22"
    context.teleop_active.add(tag)
    return {"tag_id": tag, "active": True, "ws_path": "/ws/teleop"}


async def end_teleop(tag_id: str) -> dict[str, Any]:
    tag = tag_id or "tag_22"
    context.teleop_active.discard(tag)
    return {"tag_id": tag, "active": False}


async def teleop_jog(args: dict[str, Any]) -> dict[str, Any]:
    tag = context.tag_from_args(args, "tag_22")
    axis = str(args.get("axis") or "x")
    val = float(args.get("val") or args.get("delta") or 0.0)
    lab = context.get_lab()
    pose = dict((lab.return_tunables_for_tag(tag) or {}).get("nominal_pose") or {})
    x = float(pose.get("x") or 0.0)
    y = float(pose.get("y") or 0.0)
    rotation = float(pose.get("rotation") or 0.0)
    if axis == "x":
        x = val
    elif axis == "y":
        y = val
    elif axis in ("rotation", "yaw"):
        rotation = val
    await lab.move_component(tag, x=x, y=y, rotation=rotation)
    return {"tag_id": tag, "pose": {"x": x, "y": y, "rotation": rotation}}


async def teleop_goto(args: dict[str, Any]) -> dict[str, Any]:
    tag = context.tag_from_args(args, "tag_22")
    x = float(args.get("x") or 0.0)
    y = float(args.get("y") or 0.0)
    rotation = float(args.get("rotation") or args.get("yaw") or 0.0)
    await context.get_lab().move_component(tag, x=x, y=y, rotation=rotation)
    return {"tag_id": tag, "status": "ok", "x": x, "y": y, "rotation": rotation}


def read_pose_sample(tag_id: str) -> dict[str, Any]:
    tag = tag_id or "tag_22"
    tun = context.get_lab().return_tunables_for_tag(tag) or {}
    pose = tun.get("nominal_pose") or {}
    return {
        "kind": "pose_sample",
        "tag_id": tag,
        "x": float(pose.get("x") or 0.0),
        "y": float(pose.get("y") or 0.0),
        "rotation": float(pose.get("rotation") or 0.0),
        "epoch_ms": now_epoch_ms(),
    }
