"""Teleop lease + pose samples via mock host UC primitives."""

from __future__ import annotations

from typing import Any

from adapters import context
from latch import now_epoch_ms


async def start_teleop(tag_id: str) -> dict[str, Any]:
    tag = tag_id or "tag_22"
    context.teleop_active.add(tag)
    try:
        await context.run_uc("START_TELEOP", {"tag_id": tag})
    except Exception:  # noqa: BLE001
        pass
    return {"tag_id": tag, "active": True, "ws_path": "/ws/teleop"}


async def end_teleop(tag_id: str) -> dict[str, Any]:
    tag = tag_id or "tag_22"
    context.teleop_active.discard(tag)
    try:
        await context.run_uc("END_TELEOP", {"tag_id": tag})
    except Exception:  # noqa: BLE001
        pass
    return {"tag_id": tag, "active": False}


async def teleop_jog(args: dict[str, Any]) -> dict[str, Any]:
    tag = context.tag_from_args(args, "tag_22")
    try:
        result = await context.run_uc("TELEOP_JOG", {**args, "tag_id": tag})
        if isinstance(result, dict) and result:
            return result
    except Exception:  # noqa: BLE001
        pass
    axis = str(args.get("axis") or "x")
    val = float(args.get("val") or args.get("delta") or 0.0)
    pose = {"x": 0.0, "y": 0.0, "rotation": 0.0}
    if axis in pose:
        pose[axis] = val
    return {"tag_id": tag, "pose": pose}


async def teleop_goto(args: dict[str, Any]) -> dict[str, Any]:
    tag = context.tag_from_args(args, "tag_22")
    try:
        result = await context.run_uc("TELEOP_GOTO", {**args, "tag_id": tag})
        if isinstance(result, dict) and result:
            return result
    except Exception:  # noqa: BLE001
        pass
    return {"tag_id": tag, "status": "ok"}


def read_pose_sample(tag_id: str) -> dict[str, Any]:
    """Flat Tier A pose_sample; prefers host tunables when available."""
    tag = tag_id or "tag_22"
    x = y = rotation = 0.0
    lab = context.get_lab()
    try:
        if hasattr(lab, "return_tunables_for_tag"):
            tun = lab.return_tunables_for_tag(tag) or {}
            pose = tun.get("nominal_pose") or {}
            x = float(pose.get("x") or 0.0)
            y = float(pose.get("y") or 0.0)
            rotation = float(pose.get("rotation") or 0.0)
    except Exception:  # noqa: BLE001
        pass
    return {
        "kind": "pose_sample",
        "tag_id": tag,
        "x": x,
        "y": y,
        "rotation": rotation,
        "epoch_ms": now_epoch_ms(),
    }
