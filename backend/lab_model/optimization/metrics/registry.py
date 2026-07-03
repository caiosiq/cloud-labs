"""Objective metric registry — shared pure math (bench edge + mock)."""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional, TypeVar

from lab_model.optimization.spec import ObjectiveTermSpec

F = TypeVar("F", bound=Callable[..., float])

MetricFn = Callable[[Dict[str, Any], ObjectiveTermSpec], float]

METRIC_REGISTRY: Dict[str, MetricFn] = {}


def register_metric(*, metric_id: str) -> Callable[[F], F]:
    """Decorator for pure metric functions: (measurement_dict, term) -> scalar."""

    def deco(fn: F) -> F:
        if metric_id in METRIC_REGISTRY:
            raise ValueError(f"Duplicate metric registration: {metric_id!r}")
        METRIC_REGISTRY[metric_id] = fn
        return fn

    return deco


def get_metric(metric_id: str) -> Optional[MetricFn]:
    return METRIC_REGISTRY.get(metric_id)


__all__ = ["METRIC_REGISTRY", "MetricFn", "get_metric", "register_metric"]
