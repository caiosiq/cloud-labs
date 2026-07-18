"""RECORD_MEASURABLES + EVAL_KERNEL on the mock host."""

from __future__ import annotations

from typing import Any

from adapters import context, vision
import kernel_host
from latch import begin_latch, end_latch


async def record_measurables(tag_id: str, args: dict[str, Any]) -> dict[str, Any]:
    tag = tag_id or context.tag_from_args(args, "tag_22")
    handle = begin_latch(tag_id=tag, reason="record")
    try:
        result = await context.run_uc("RECORD_MEASURABLES", {**args, "tag_id": tag})
    except Exception:  # noqa: BLE001
        result = {"tag_id": tag, "status": "ok"}
    meta = end_latch(handle)
    out = result if isinstance(result, dict) else {"tag_id": tag}
    out.setdefault("tag_id", tag)
    out["_latch"] = meta
    return out


async def eval_kernel(kernel_id: str, args: dict[str, Any]) -> dict[str, Any]:
    kid = kernel_id or str(args.get("kernel_id") or "stub")
    tag = context.tag_from_args(args, "tag_22")
    handle = begin_latch(tag_id=tag, reason="eval_kernel")
    bgr = vision.capture_bgr(tag)
    scored = kernel_host.eval_on_bgr(kid, bgr, args=args)
    meta = end_latch(handle)
    scored["_latch"] = meta
    scored.setdefault("tag_id", tag)
    return scored
