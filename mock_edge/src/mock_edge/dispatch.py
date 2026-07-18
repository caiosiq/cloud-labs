"""Route Edge Contract ``primitive`` names to filled mock adapters.

Same map as ``cloudlabs-edge init`` ``dispatch.py``. Handlers may be async
because the teaching host's UC surface is async; :func:`dispatch_primitive`
awaits awaitables.
"""

from __future__ import annotations

import inspect
from typing import Any, Awaitable, Callable, Union

from mock_edge.adapters import live_feed, motion, motors, observe, optimize, teleop, tunables
from mock_edge.kernel_host import eval_on_bgr
from mock_edge.latch import begin_latch, end_latch, now_epoch_ms

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
    "PICK_COMPONENT": lambda a: motion.pick_component(a),
    "HOVER": lambda a: motion.hover_component(a),
    "PLACE_FROM_HOVER": lambda a: motion.place_from_hover(a),
    "SCAN_ROTATE_IN_PLACE": lambda a: motion.scan_rotate_in_place(a),
    "CONFIRM_HOLDING_TAG": lambda a: motion.confirm_holding_tag(a),
    "STORE_COMPONENT": lambda a: motion.store_component(a),
    "PLACE_FROM_STORAGE": lambda a: motion.place_from_storage(a),
    "AFFIRM_PLACED_AT_CURRENT": lambda a: motion.affirm_placed_at_current(a),
    "REPACK_STORAGE": lambda a: motion.repack_storage_slot(a),
    "RECENTER_IN_STORAGE": lambda a: motion.recenter_stored_in_inventory(a),
    "REMOVE": lambda a: motion.remove_component(a),
    "MOVE_MOTOR": lambda a: motors.move_motor(a),
    "SET_MOTOR_SETPOINT": lambda a: motors.set_motor_setpoint(a),
    "MOTOR_SET_ZERO": lambda a: motors.motor_set_zero(a),
    "MOTOR_SEND_HOME": lambda a: motors.motor_send_home(a),
    "SET_EXPOSURE": lambda a: tunables.set_exposure_time_ms(a),
    "SET_LASER_OUTPUT": lambda a: tunables.set_output_power_mw(a),
    "OPTIMIZE": lambda a: optimize.optimize_component(a),
}


async def dispatch_primitive(primitive: str, args: dict[str, Any] | None = None) -> Any:
    """Invoke the adapter registered for ``primitive`` (await if needed)."""
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
