"""Plan primitive commands to reconcile one configuration to another."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence

from .batch_plan import (
    BatchPlanError,
    BatchPlanResult,
    plan_batch,
    plan_batch_or_raise,
    strip_plan_meta,
)


def plan_reconcile(
    current_cfg: Mapping[str, Any],
    target_cfg: Mapping[str, Any],
    *,
    staging_seats: Optional[Sequence[Mapping[str, Any]]] = None,
    free_storage_slots: Optional[int] = None,
    storage_capacity: Optional[int] = None,
    size_fn: Optional[Any] = None,
    pad_mm: Optional[float] = None,
    raise_if_not_ready: bool = True,
) -> List[Dict[str, Any]]:
    """Return command envelopes that move from ``current_cfg`` toward ``target_cfg``.

    Spatial order goes through the shared seat DAG (:func:`plan_batch`): Park /
    Unpark via layout staging seats, predecessor edges, fail-closed capacity.

    Each envelope matches ``POST /api/command`` shape (``action``, ``target_id``,
    ``parameters``) plus optional plan meta (``plan_step_id``, ``predecessors``,
    ``plan_role``) for Command Matrix batch enqueue.

    When ``raise_if_not_ready`` is true (default), blocking issues raise
    :class:`BatchPlanError` with a structured ``report``.
    """
    result = plan_batch(
        current_cfg,
        target_cfg,
        staging_seats=staging_seats,
        free_storage_slots=free_storage_slots,
        storage_capacity=storage_capacity,
        size_fn=size_fn,
        pad_mm=pad_mm,
    )
    if not result.ready:
        if raise_if_not_ready:
            raise BatchPlanError(result.report)
        return []
    return list(result.commands)


def plan_reconcile_detailed(
    current_cfg: Mapping[str, Any],
    target_cfg: Mapping[str, Any],
    *,
    staging_seats: Optional[Sequence[Mapping[str, Any]]] = None,
    free_storage_slots: Optional[int] = None,
    storage_capacity: Optional[int] = None,
    size_fn: Optional[Any] = None,
    pad_mm: Optional[float] = None,
) -> BatchPlanResult:
    """Same as :func:`plan_reconcile` but always returns the full result + report."""
    return plan_batch(
        current_cfg,
        target_cfg,
        staging_seats=staging_seats,
        free_storage_slots=free_storage_slots,
        storage_capacity=storage_capacity,
        size_fn=size_fn,
        pad_mm=pad_mm,
    )


__all__ = [
    "BatchPlanError",
    "BatchPlanResult",
    "plan_batch",
    "plan_batch_or_raise",
    "plan_reconcile",
    "plan_reconcile_detailed",
    "strip_plan_meta",
]
