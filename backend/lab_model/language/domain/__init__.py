"""
Domain layer — pure lab semantics (no hardware, no HTTP).

- :mod:`component` — per-tag tunables/measurables shapes and accessors
- :mod:`holding` — top-level ``holding`` block and system status constants
- :mod:`storage_region` — Q3 inventory grid from ``layout.json``
- :mod:`motor_rotation_store` — cumulative motor angles JSON persistence
"""

from . import component
from . import holding
from . import motor_rotation_store
from . import storage_region

__all__ = [
    "component",
    "holding",
    "motor_rotation_store",
    "storage_region",
]
