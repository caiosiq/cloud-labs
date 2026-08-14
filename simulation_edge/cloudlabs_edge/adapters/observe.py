"""RECORD_MEASURABLES + EVAL_KERNEL on the simulation edge."""

from __future__ import annotations

from typing import Any, Dict, Optional

from adapters import context, vision
import kernel_host
from latch import begin_latch, end_latch

# Analysis BGR from last RECORD so EVAL_KERNEL scores the saved frame.
_last_bgr: Dict[str, Any] = {}


def get_recorded_bgr(tag_id: str) -> Optional[Any]:
    return _last_bgr.get(tag_id)


async def record_measurables(tag_id: str, args: dict[str, Any]) -> dict[str, Any]:
    tag = tag_id or context.tag_from_args(args, "tag_22")
    handle = begin_latch(tag_id=tag, reason="record")
    try:
        bgr = vision.capture_bgr(tag)
        _last_bgr[tag] = bgr
    except Exception:  # noqa: BLE001
        bgr = None
    jpeg = vision.capture_jpeg(tag)
    meta = end_latch(handle)
    return {
        "tag_id": tag,
        "status": "ok",
        "measurable_id": f"{tag}.camera_image",
        "bytes": len(jpeg),
        "_latch": meta,
    }


async def eval_kernel(kernel_id: str, args: dict[str, Any]) -> dict[str, Any]:
    kid = kernel_id or str(args.get("kernel_id") or "stub")
    tag = context.tag_from_args(args, "tag_22")
    force_live = bool(args.get("recapture") or args.get("fresh_capture"))
    handle = begin_latch(tag_id=tag, reason="eval_kernel")
    bgr = None if force_live else _last_bgr.get(tag)
    from_latched = bgr is not None
    if bgr is None:
        bgr = vision.capture_bgr(tag)
        _last_bgr[tag] = bgr
    scored = kernel_host.eval_on_bgr(kid, bgr, args=args)
    meta = end_latch(handle)
    scored["_latch"] = meta
    scored.setdefault("tag_id", tag)
    scored["from_latched_record"] = from_latched
    return scored
