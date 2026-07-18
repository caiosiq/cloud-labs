"""Measurable plugins — import submodules to register observers.

Pose and motor angles are **not** measurables: lab observe recalculates
the corresponding tunables (``reported_pose`` / ``nominal_motor_positions``).
"""

from . import camera_image as camera_image  # noqa: F401
from . import last_optimization_score as last_optimization_score  # noqa: F401
from . import output_power_readback_mw as output_power_readback_mw  # noqa: F401
from .materialize import legacy_wire_view, materialize_measurable, read_measurable_from_state
from .record import observe_for_tag
from .registry import (
    MEASURABLE_REGISTRY,
    MeasurableSpec,
    TensorSchema,
    get_measurable,
    is_tensor_envelope,
    register_measurable,
)
from .resolve_data import resolve_tensor_data, resolve_tensor_with_state_path
from .tensor import LazyRef, MeasurableTensor

__all__ = [
    "MEASURABLE_REGISTRY",
    "LazyRef",
    "MeasurableSpec",
    "MeasurableTensor",
    "TensorSchema",
    "get_measurable",
    "is_tensor_envelope",
    "legacy_wire_view",
    "materialize_measurable",
    "read_measurable_from_state",
    "register_measurable",
    "observe_for_tag",
    "resolve_tensor_data",
    "resolve_tensor_with_state_path",
    "camera_image",
    "last_optimization_score",
    "output_power_readback_mw",
]
