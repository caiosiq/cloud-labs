"""Route Edge Contract ``primitive`` names to simulation adapters."""

from __future__ import annotations

import inspect
from typing import Any, Awaitable, Callable, Union

from adapters import live_feed, motion, observe, teleop, vision
from adapters.runtime_ready import ensure_runtime_ready
from kernel_host import eval_on_bgr
from latch import begin_latch, end_latch, now_epoch_ms

Handler = Callable[[dict[str, Any]], Union[Any, Awaitable[Any]]]

PRIMITIVE_HANDLERS: dict[str, Handler] = {
    "START_LIVE_FEED": lambda a: live_feed.arm_live_feed(
        str(a.get("channel") or a.get("measurable_id") or ""),
        profile=a.get("profile"),
        exposure_time_ms=(
            float(a["exposure_time_ms"])
            if a.get("exposure_time_ms") is not None
            else (
                float(a["live_exposure_time_ms"])
                if a.get("live_exposure_time_ms") is not None
                else None
            )
        ),
    ),
    "END_LIVE_FEED": lambda a: live_feed.disarm_live_feed(
        str(a.get("channel") or a.get("measurable_id") or ""),
    ),
    "SET_LIVE_EXPOSURE": lambda a: live_feed.set_live_exposure(a),
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
    "STORE_COMPONENT": lambda a: motion.store_component(a),
    "PLACE_FROM_STORAGE": lambda a: motion.place_from_storage(a),
    "REPACK_STORAGE": lambda a: motion.repack_storage_slot(a),
    "RECENTER_IN_STORAGE": lambda a: motion.recenter_stored_in_inventory(a),
    "LOCALIZE_COMPONENTS": lambda a: vision.localize_components(a),
    "RECORD_TUNABLES": lambda a: vision.record_tunables(a),
    "SYNC_RUNTIME": lambda a: vision.sync_runtime(a),
}


async def dispatch_primitive(primitive: str, args: dict[str, Any] | None = None) -> Any:
    args = args or {}
    ensure_runtime_ready(primitive)
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
