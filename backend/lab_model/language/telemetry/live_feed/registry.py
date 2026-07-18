"""Registry for telemetry.live_feed channel plugins."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict


@dataclass(frozen=True)
class LiveFeedSpec:
    field_id: str
    widget: str


LIVE_FEED_REGISTRY: Dict[str, LiveFeedSpec] = {}


def register_live_feed(*, field_id: str, widget: str):
    def _decorator(fn):
        LIVE_FEED_REGISTRY[field_id] = LiveFeedSpec(field_id=field_id, widget=widget)
        return fn

    return _decorator
