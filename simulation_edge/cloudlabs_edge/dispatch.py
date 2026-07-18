"""Route Edge Contract ``primitive`` names to simulation adapters."""

from __future__ import annotations

import inspect
from typing import Any, Awaitable, Callable, Union

from adapters import live_feed, motion, observe, teleop
from kernel_host import eval_on_bgr
from latch import begin_latch, end_latch, now_epoch_ms

Handler = Callable[[dict[str, Any]], Union[Any, Awaitable[Any]]]

PRIMITIVE_HANDLERS: dict[str, Handler] = {
    "START_LIVE_FEED": lambda a: live_feed.arm_live_feed(
        str(a.get("channel") or a.get("measurable_id") or ""),
        profile=a.get("profile"),
    ),
    "END_LIVE_FEED": lambda a: live_feed.disarm_live_feed(
        str(a.get("channel") or a.get("measurable_id") or ""),
    ),
    "START_TELEOP": lambda a: teleop.start_teleop(
        str(a.get("tag_id") or a.get("target_id") or "")
    ),
    "END_TELEOP": lambda a: teleop.end_teleop(
        str(a.get("tag_id") or a.get("target_id") or "")
    ),
    "TELEOP_JOG": lambda a: teleop.teleop_jog(a),
    "TELEOP_GOTO": lambda a: teleop.teleop_goto(a),
    "RECORD_MEASURABLES": lambda a: observe.record_measurables(
        str(a.get("tag_id") or a.get("target_id") or ""), a
    ),
    "EVAL_KERNEL": lambda a: observe.eval_kernel(str(a.get("kernel_id") or ""), a),
    "MOVE_COMPONENT": lambda a: motion.move_component(a),
}


async def dispatch_primitive(primitive: str, args: dict[str, Any] | None = None) -> Any:
    args = args or {}
    handler = PRIMITIVE_HANDLERS.get(primitive)
    if handler is None:
        raise KeyError(f"no adapter mapping for primitive {primitive!r}")
    result = handler(args)
    if inspect.isawaitable(result):
        return await result
    return result


__all__ = [
    "PRIMITIVE_HANDLERS",
    "dispatch_primitive",
    "begin_latch",
    "end_latch",
    "now_epoch_ms",
    "eval_on_bgr",
]
