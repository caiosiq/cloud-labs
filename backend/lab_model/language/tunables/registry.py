"""Tunable field registry — register plugins with :func:`register_tunable`."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, TypeVar

from lab_model.language.primitives.ids import PrimitiveId

F = TypeVar("F", bound=Callable[..., Any])


@dataclass(frozen=True)
class TunableSpec:
    field_id: str
    widget: str
    write_primitive: PrimitiveId
    apply: Callable[..., Any]


TUNABLE_REGISTRY: Dict[str, TunableSpec] = {}


def register_tunable(
    *,
    field_id: str,
    widget: str,
    write_primitive: PrimitiveId,
) -> Callable[[F], F]:
    """Decorator: register a tunable plugin and its ``apply(bridge, tag_id, ...)`` handler."""

    def deco(fn: F) -> F:
        if field_id in TUNABLE_REGISTRY:
            raise ValueError(f"Duplicate tunable registration: {field_id!r}")
        TUNABLE_REGISTRY[field_id] = TunableSpec(
            field_id=field_id,
            widget=widget,
            write_primitive=write_primitive,
            apply=fn,
        )
        return fn

    return deco


def get_tunable(field_id: str) -> Optional[TunableSpec]:
    return TUNABLE_REGISTRY.get(field_id)
