"""Shared objective metrics (pure math — no hardware I/O)."""

from .registry import METRIC_REGISTRY, MetricFn, get_metric, register_metric
from .weighted_sum import evaluate_weighted_sum

__all__ = [
    "METRIC_REGISTRY",
    "MetricFn",
    "evaluate_weighted_sum",
    "get_metric",
    "register_metric",
]
