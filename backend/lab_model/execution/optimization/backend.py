"""Ensemble evaluation backend protocol (hardware / mock implements this)."""
from __future__ import annotations

from typing import Any, Dict, Mapping, Protocol, Sequence

from lab_model.execution.optimization.spec import ObjectiveSpec, SolverBlockSpec


class EnsembleEvaluationBackend(Protocol):
    """
    Bench-side bridge for one ensemble optimization session.

    Capture and I/O stay here; loss math uses ``lab_model.execution.optimization.metrics``.
    """

    def router_for_block(
        self,
        block: SolverBlockSpec,
        variable_ids: Sequence[str],
    ) -> Any: ...

    def apply_through_router(
        self,
        router: Any,
        physical: Mapping[str, float],
        *,
        block_id: str,
    ) -> None: ...

    def evaluate_loss(
        self,
        physical: Mapping[str, float],
        objective: ObjectiveSpec,
        *,
        router: Any,
    ) -> tuple[float, Dict[str, float]]: ...


__all__ = ["EnsembleEvaluationBackend"]
