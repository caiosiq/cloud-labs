"""Ensemble optimization session — block COBYLA macro loop."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Mapping, Optional

from lab_model.optimization.backend import EnsembleEvaluationBackend
from lab_model.optimization.normalize import NormalizedSearchSpace
from lab_model.optimization.solvers.block_cobyla import run_block_cobyla
from lab_model.optimization.spec import OptimizeEnsembleParameters, VariableRef


@dataclass
class EnsembleOptimizationResult:
    session_id: str
    best_loss: float
    final_values: Dict[str, float]
    evals: int = 0
    trace: list[Dict[str, Any]] = field(default_factory=list)


def run_ensemble_optimization(
    spec: OptimizeEnsembleParameters,
    x0: Mapping[str, float],
    *,
    backend: EnsembleEvaluationBackend,
    session_id: str = "",
    progress_callback: Optional[Callable[..., None]] = None,
) -> EnsembleOptimizationResult:
    """
    Inner macro loop: sequential block COBYLA on normalized subspaces.

    ``backend`` owns bench I/O and measurement capture; metrics live in
    ``lab_model.optimization.metrics``.
    """
    variables_by_id = {v.id: v for v in spec.variables}
    space = NormalizedSearchSpace(spec.variables, x0)
    current = {vid: float(x0[vid]) for vid in space.variable_ids}
    best = dict(current)
    best_loss = float("inf")
    evals = 0
    trace: list[Dict[str, Any]] = []

    def _objective_for_block(block_id: str, router: Any) -> Callable[[Mapping[str, float]], float]:
        def _fn(physical: Mapping[str, float]) -> float:
            nonlocal evals, best_loss, best
            backend.apply_through_router(router, physical, block_id=block_id)
            loss, terms = backend.evaluate_loss(
                physical,
                spec.objective,
                router=router,
            )
            evals += 1
            u = space.normalize_dict(physical)
            record = {
                "eval": evals,
                "loss": loss,
                "terms": terms,
                "u": u,
                "block_id": block_id,
                "values": {vid: float(physical[vid]) for vid in space.variable_ids},
            }
            trace.append(record)
            if loss < best_loss:
                best_loss = loss
                best = dict(physical)
            if progress_callback is not None:
                progress_callback(step=evals, best_loss=best_loss, **record)
            if evals >= spec.solver.max_total_evals:
                return loss
            return loss

        return _fn

    for block in spec.solver.blocks:
        block_vars = [variables_by_id[vid] for vid in block.variable_ids]
        invasive = any(v.physical_type == "invasive_discrete" for v in block_vars)
        router = backend.router_for_block(block, block.variable_ids)
        if invasive:
            router.enter_invasive_block(block.variable_ids)
        else:
            router.enter_continuous_block(block.variable_ids)

        obj_fn = _objective_for_block(block.id, router)
        remaining = max(1, spec.solver.max_total_evals - evals)

        for _pass in range(block.passes):
            if evals >= spec.solver.max_total_evals:
                break
            per_block = min(block.max_evals, remaining)
            current = run_block_cobyla(
                block,
                block.variable_ids,
                space,
                current,
                obj_fn,
                max_evals=per_block,
            )
            remaining = max(1, spec.solver.max_total_evals - evals)

        router.exit_block(block.id)

    sid = session_id or "ensemble"
    return EnsembleOptimizationResult(
        session_id=sid,
        best_loss=best_loss,
        final_values=best,
        evals=evals,
        trace=trace,
    )


__all__ = ["EnsembleOptimizationResult", "run_ensemble_optimization"]
