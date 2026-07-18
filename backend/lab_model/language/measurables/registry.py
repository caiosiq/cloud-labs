"""Measurable field registry — each plugin owns observe + tensor schema."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple, TypeVar

from .tensor import Domain

F = TypeVar("F", bound=Callable[..., Any])


@dataclass(frozen=True)
class TensorSchema:
    """Static analysis-tensor contract for one measurable field."""

    dtype: str
    domain: Domain
    axes: Dict[str, str] = field(default_factory=dict)
    units: Dict[str, str] = field(default_factory=dict)
    #: Declared shape when known a priori (``()`` scalar; images often empty until resolve).
    shape: Tuple[int, ...] = ()
    #: Wire/analysis hint: ``scalar`` | ``spatial_bgr`` | ``vector`` | ``lazy_image``.
    layout: str = "scalar"


@dataclass(frozen=True)
class MeasurableSpec:
    field_id: str
    widget: str
    observe: Callable[..., Awaitable[Optional[Any]]]
    tensor: TensorSchema


MEASURABLE_REGISTRY: Dict[str, MeasurableSpec] = {}


def register_measurable(
    *,
    field_id: str,
    widget: str,
    dtype: str,
    domain: Domain,
    axes: Optional[Dict[str, str]] = None,
    units: Optional[Dict[str, str]] = None,
    shape: Tuple[int, ...] = (),
    layout: str = "scalar",
) -> Callable[[F], F]:
    """Decorator: ``async def observe(bridge, tag_id, catalog_meta) -> value | MeasurableTensor | None``.

    Tensor schema is required — the language is tensor-native.
    """

    schema = TensorSchema(
        dtype=dtype,
        domain=domain,
        axes=dict(axes or {}),
        units=dict(units or {}),
        shape=shape,
        layout=layout,
    )

    def deco(fn: F) -> F:
        if field_id in MEASURABLE_REGISTRY:
            raise ValueError(f"Duplicate measurable registration: {field_id!r}")
        MEASURABLE_REGISTRY[field_id] = MeasurableSpec(
            field_id=field_id,
            widget=widget,
            observe=fn,
            tensor=schema,
        )
        return fn

    return deco


def get_measurable(field_id: str) -> Optional[MeasurableSpec]:
    return MEASURABLE_REGISTRY.get(field_id)


def is_tensor_envelope(value: Any) -> bool:
    """True when ``value`` looks like :meth:`MeasurableTensor.to_api_dict` output."""
    return (
        isinstance(value, dict)
        and "field" in value
        and "dtype" in value
        and "domain" in value
        and "data" in value
    )
