"""
Lab platform core: domain, primitives, catalog, state, tunable/measurable plugins.

Import subpackages explicitly (e.g. ``lab_model.primitives``) to avoid import cycles
with :mod:`lab_communicator.base`.

See ``README.md`` and ``ARCHITECTURE.md``.
"""

from .domain import component, holding, motor_rotation_store, storage_region

__all__ = [
    "component",
    "holding",
    "motor_rotation_store",
    "storage_region",
]
