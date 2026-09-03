"""Lab language surface for the SDK — prefer ``lab_model`` when on PYTHONPATH.

HTTP wrappers stay hand-written (names, ergonomics), but action strings and
measurable field contracts should come from the same language package the
coordinator uses. When ``lab_model`` is unavailable (pip-only install), fall
back to string constants that must stay aligned with
``lab_model.language.primitives.ids.PrimitiveId``.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, Optional

try:
    from lab_model.language.primitives import PrimitiveId as PrimitiveId
    from lab_model.language.measurables import (  # noqa: F401
        MEASURABLE_REGISTRY as MEASURABLE_REGISTRY,
    )

    _FROM_LAB_MODEL = True
except ImportError:
    try:
        from enum import StrEnum
    except ImportError:  # Python < 3.11
        class StrEnum(str, Enum):
            def __str__(self) -> str:
                return str(self.value)

            def __format__(self, format_spec: str) -> str:
                return str(self.value).__format__(format_spec)

    class PrimitiveId(StrEnum):
        """Mirror of ``lab_model.language.primitives.ids.PrimitiveId`` (fallback)."""

        GET_TUNABLES = "GET_TUNABLES"
        GET_MEASURABLES = "GET_MEASURABLES"
        GET_PARAMETERS = "GET_PARAMETERS"
        RECORD_MEASURABLES = "RECORD_MEASURABLES"
        EVAL_KERNEL = "EVAL_KERNEL"
        MOVE_COMPONENT = "MOVE_COMPONENT"
        MOVE_MOTOR = "MOVE_MOTOR"
        SET_MOTOR_SETPOINT = "SET_MOTOR_SETPOINT"
        MOTOR_SEND_HOME = "MOTOR_SEND_HOME"
        MOTOR_SET_ZERO = "MOTOR_SET_ZERO"
        SET_EXPOSURE = "SET_EXPOSURE"
        SET_LIVE_EXPOSURE = "SET_LIVE_EXPOSURE"
        SET_LASER_OUTPUT = "SET_LASER_OUTPUT"
        APPLY_TUNABLES_PATCH = "APPLY_TUNABLES_PATCH"
        OPTIMIZE = "OPTIMIZE"
        STORE_COMPONENT = "STORE_COMPONENT"
        PLACE_FROM_STORAGE = "PLACE_FROM_STORAGE"
        PICK_COMPONENT = "PICK_COMPONENT"
        PLACE_FROM_HOVER = "PLACE_FROM_HOVER"
        START_TELEOP = "START_TELEOP"
        END_TELEOP = "END_TELEOP"
        TELEOP_JOG = "TELEOP_JOG"
        TELEOP_GOTO = "TELEOP_GOTO"
        START_LIVE_FEED = "START_LIVE_FEED"
        END_LIVE_FEED = "END_LIVE_FEED"
        LOCALIZE_COMPONENTS = "LOCALIZE_COMPONENTS"
        RECORD_TUNABLES = "RECORD_TUNABLES"
        SYNC_RUNTIME = "SYNC_RUNTIME"

    MEASURABLE_REGISTRY: Dict[str, Any] = {}
    _FROM_LAB_MODEL = False


def primitive_action(pid: PrimitiveId) -> str:
    """Wire ``action`` string for POST /api/command."""
    return pid.value if isinstance(pid, Enum) else str(pid)


def measurable_field_schema(field_id: str) -> Optional[Dict[str, Any]]:
    """Return registered tensor schema for ``field_id`` when lab_model is loaded."""
    spec = MEASURABLE_REGISTRY.get(field_id) if MEASURABLE_REGISTRY else None
    if spec is None:
        return None
    t = getattr(spec, "tensor", None)
    if t is None:
        return {"widget": getattr(spec, "widget", None)}
    return {
        "widget": spec.widget,
        "tensor": {
            "dtype": t.dtype,
            "domain": t.domain,
            "axes": dict(t.axes),
            "units": dict(t.units),
            "shape": list(t.shape),
            "layout": t.layout,
        },
    }


__all__ = [
    "MEASURABLE_REGISTRY",
    "PrimitiveId",
    "measurable_field_schema",
    "primitive_action",
    "_FROM_LAB_MODEL",
]
