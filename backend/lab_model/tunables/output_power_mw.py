"""Tunable plugin: ``output_power_mw`` (laser sources)."""
from __future__ import annotations

from typing import Any

from lab_model.primitives.ids import PrimitiveId

from ._commit import commit_tunable_value
from .registry import register_tunable


@register_tunable(
    field_id="output_power_mw",
    widget="FloatRange",
    write_primitive=PrimitiveId.SET_LASER_OUTPUT,
)
async def apply(bridge: Any, tag_id: str, output_power_mw: float) -> None:
    power = float(output_power_mw)
    if power < 0:
        print(f"{bridge.log_prefix} set_output_power_mw: invalid {power}")
        return

    commit_tunable_value(bridge, tag_id, "output_power_mw", power)

    hook = getattr(bridge, "_hardware_set_laser_output_power_mw", None)
    if callable(hook):
        ok, msg = hook(tag_id, power)
        print(
            f"{bridge.log_prefix} set_output_power_mw {tag_id}: "
            f"{power:g} mW ok={ok} {msg}"
        )
    else:
        print(
            f"{bridge.log_prefix} set_output_power_mw {tag_id}: "
            f"{power:g} mW (intent only, no hardware hook)"
        )
