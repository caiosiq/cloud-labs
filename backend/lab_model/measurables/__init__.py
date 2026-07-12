"""Measurable plugins — import submodules to register observers."""

from . import camera_image as camera_image  # noqa: F401
from . import last_optimization_score as last_optimization_score  # noqa: F401
from . import motor_rotations as motor_rotations  # noqa: F401
from . import output_power_readback_mw as output_power_readback_mw  # noqa: F401
from . import pose as pose  # noqa: F401
from .materialize import materialize_measurable, read_measurable_from_state
from .record import observe_for_tag
from .registry import MEASURABLE_REGISTRY, MeasurableSpec, get_measurable, register_measurable
from .resolve_data import resolve_tensor_data, resolve_tensor_with_state_path
from .tensor import LazyRef, MeasurableTensor

__all__ = [
    "MEASURABLE_REGISTRY",
    "LazyRef",
    "MeasurableSpec",
    "MeasurableTensor",
    "get_measurable",
    "materialize_measurable",
    "read_measurable_from_state",
    "register_measurable",
    "observe_for_tag",
    "resolve_tensor_data",
    "resolve_tensor_with_state_path",
    "camera_image",
    "last_optimization_score",
    "motor_rotations",
    "pose",
]
