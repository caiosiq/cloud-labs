"""Measurable plugins — import submodules to register observers."""

from . import camera_image as camera_image  # noqa: F401
from . import last_optimization_score as last_optimization_score  # noqa: F401
from . import motor_rotations as motor_rotations  # noqa: F401
from . import output_power_readback_mw as output_power_readback_mw  # noqa: F401
from . import pose as pose  # noqa: F401
from .record import observe_for_tag
from .registry import MEASURABLE_REGISTRY, MeasurableSpec, get_measurable, register_measurable

__all__ = [
    "MEASURABLE_REGISTRY",
    "MeasurableSpec",
    "get_measurable",
    "register_measurable",
    "observe_for_tag",
    "camera_image",
    "last_optimization_score",
    "motor_rotations",
    "pose",
]
