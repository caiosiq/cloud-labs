"""
Lab domain model: component tunables/measurables, storage-quadrant geometry, motor-angle persistence.

This package is intentionally separate from :mod:`lab_communicator`, which contains only the
mock/real **LabCommunicator** adapters and hardware-facing glue.

See ``README.md`` in this directory for tunables vs measurables.
"""

from . import component_model
from . import holding
from . import motor_rotation_store
from . import storage_region

__all__ = ["component_model", "holding", "motor_rotation_store", "storage_region"]
