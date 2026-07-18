"""
Lab platform core: UC language, execution plane, coordinator plane.

Import subpackages explicitly (e.g. ``lab_model.language.primitives``).

Layout::

    language/       domain, primitives, tunables, measurables, telemetry
    execution/      orchestration, edge, optimization
    coordinator/    backends, catalog, jobs, state

See ``README.md`` and ``ARCHITECTURE.md``.
"""

from .language.domain import component, holding, motor_rotation_store, storage_region

__all__ = [
    "component",
    "holding",
    "motor_rotation_store",
    "storage_region",
]
