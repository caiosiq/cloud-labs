"""Tunable plugins — import submodules to register handlers."""

from . import exposure_time_ms as exposure_time_ms  # noqa: F401
from . import nominal_motor_positions as nominal_motor_positions  # noqa: F401
from . import nominal_pose as nominal_pose  # noqa: F401
from . import output_power_mw as output_power_mw  # noqa: F401
from .registry import TUNABLE_REGISTRY, TunableSpec, get_tunable, register_tunable

__all__ = [
    "TUNABLE_REGISTRY",
    "TunableSpec",
    "get_tunable",
    "register_tunable",
    "exposure_time_ms",
    "nominal_pose",
    "nominal_motor_positions",
    "output_power_mw",
]
