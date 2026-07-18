"""Lab state transitions: commits, refusals, snapshots, placement UI, runtime/control."""

from .control_manager import ControlManager
from .runtime_manager import MutationKind, RuntimeManager, default_runtime_state
from .projections import (
    apply_configuration_to_components,
    build_setup,
    extract_configuration,
    extract_observations,
)
from .snapshot import LabPose, normalize_loaded_state

__all__ = [
    "ControlManager",
    "LabPose",
    "MutationKind",
    "RuntimeManager",
    "apply_configuration_to_components",
    "build_setup",
    "default_runtime_state",
    "extract_configuration",
    "extract_observations",
    "normalize_loaded_state",
]
