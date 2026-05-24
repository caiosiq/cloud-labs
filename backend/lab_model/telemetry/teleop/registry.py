"""Registry for telemetry.teleop live-control field plugins."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict


@dataclass(frozen=True)
class TeleopControlSpec:
    field_id: str
    widget: str


TELEOP_REGISTRY: Dict[str, TeleopControlSpec] = {}


def register_teleop_control(*, field_id: str, widget: str):
    def _decorator(fn):
        TELEOP_REGISTRY[field_id] = TeleopControlSpec(field_id=field_id, widget=widget)
        return fn

    return _decorator
