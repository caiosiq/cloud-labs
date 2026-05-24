"""Measurable field registry — register plugins with :func:`register_measurable`."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Optional, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


@dataclass(frozen=True)
class MeasurableSpec:
    field_id: str
    widget: str
    observe: Callable[..., Awaitable[Optional[Any]]]


MEASURABLE_REGISTRY: Dict[str, MeasurableSpec] = {}


def register_measurable(
    *,
    field_id: str,
    widget: str,
) -> Callable[[F], F]:
    """Decorator: ``async def observe(bridge, tag_id, catalog_meta) -> value | None``."""

    def deco(fn: F) -> F:
        if field_id in MEASURABLE_REGISTRY:
            raise ValueError(f"Duplicate measurable registration: {field_id!r}")
        MEASURABLE_REGISTRY[field_id] = MeasurableSpec(
            field_id=field_id,
            widget=widget,
            observe=fn,
        )
        return fn

    return deco


def get_measurable(field_id: str) -> Optional[MeasurableSpec]:
    return MEASURABLE_REGISTRY.get(field_id)
