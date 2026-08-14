"""Ensemble optimization session — block COBYLA macro loop."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Mapping, Optional

from lab_model.execution.optimization.backend import EnsembleEvaluationBackend
from lab_model.execution.optimization.normalize import NormalizedSearchSpace
from lab_model.execution.optimization.solvers.block_cobyla import run_block_cobyla
from lab_model.execution.optimization.spec import OptimizeEnsembleParameters, VariableRef


@dataclass
class EnsembleOptimizationResult:
    session_id: str
    best_loss: float
    final_values: Dict[str, float]
    evals: int = 0
    aborted: bool = False
    early_stopped: bool = False
    early_stop_reason: Optional[str] = None
    trace: list[Dict[str, Any]] = field(default_factory=list)


def run_ensemble_optimization(
    spec: OptimizeEnsembleParameters,
    x0: Mapping[str, float],
    *,
    backend: EnsembleEvaluationBackend,
    session_id: str = "",
    progress_callback: Optional[Callable[..., None]] = None,
    should_abort: Optional[Callable[[], bool]] = None,
    should_accept: Optional[Callable[[], bool]] = None,
) -> EnsembleOptimizationResult:
    """
    Inner macro loop: sequential block COBYLA on normalized subspaces.

    ``backend`` owns bench I/O and measurement capture; metrics live in
    ``lab_model.execution.optimization.metrics``.
    """
    variables_by_id = {v.id: v for v in spec.variables}
    space = NormalizedSearchSpace(spec.variables, x0)
    x0_vals = {vid: float(x0[vid]) for vid in space.variable_ids}
    current = dict(x0_vals)
    best = dict(current)
    best_loss = float("inf")
    evals = 0
    aborted = False
    early_stopped = False
    early_stop_reason: Optional[str] = None
    trace: list[Dict[str, Any]] = []

    class _OptimizationAborted(Exception):
        pass

    class _EarlyStop(Exception):
        def __init__(self, reason: str = "operator_accept") -> None:
            super().__init__(reason)
            self.reason = reason

    def _check_abort() -> None:
        if should_abort is not None and should_abort():
            raise _OptimizationAborted("cancelled")

    def _check_accept() -> None:
        if should_accept is not None and should_accept():
            raise _EarlyStop("operator_accept")

    def _objective_for_block(block_id: str, router: Any) -> Callable[[Mapping[str, float]], float]:
        def _fn(physical: Mapping[str, float]) -> float:
            nonlocal evals, best_loss, best, current
            _check_abort()
            _check_accept()
            backend.apply_through_router(router, physical, block_id=block_id)
            current = {vid: float(physical[vid]) for vid in space.variable_ids}
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
                "values": dict(current),
            }
            trace.append(record)
            if loss < best_loss:
                best_loss = loss
                best = dict(current)
            if progress_callback is not None:
                progress_callback(step=evals, best_loss=best_loss, **record)
            _check_accept()
            return loss

        return _fn

    try:
        for block in spec.solver.blocks:
            _check_abort()
            _check_accept()
            block_vars = [variables_by_id[vid] for vid in block.variable_ids]
            invasive = any(v.physical_type == "invasive_discrete" for v in block_vars)
            router = backend.router_for_block(block, block.variable_ids)
            if invasive:
                router.enter_invasive_block(block.variable_ids)
            else:
                router.enter_continuous_block(block.variable_ids)

            try:
                obj_fn = _objective_for_block(block.id, router)
                remaining = max(1, spec.solver.max_total_evals - evals)

                for _pass in range(block.passes):
                    _check_abort()
                    _check_accept()
                    if early_stopped or evals >= spec.solver.max_total_evals:
                        break
                    per_block = min(block.max_evals, remaining)
                    try:
                        current = run_block_cobyla(
                            block,
                            block.variable_ids,
                            space,
                            current,
                            obj_fn,
                            max_evals=per_block,
                        )
                    except _EarlyStop as stop_exc:
                        early_stopped = True
                        early_stop_reason = getattr(stop_exc, "reason", None) or "operator_accept"
                        if best_loss < float("inf"):
                            current = dict(best)
                        break
                    remaining = max(1, spec.solver.max_total_evals - evals)
            finally:
                router.exit_block(block.id)
            if aborted or early_stopped or evals >= spec.solver.max_total_evals:
                break
    except _OptimizationAborted:
        aborted = True
    except _EarlyStop as stop_exc:
        early_stopped = True
        early_stop_reason = getattr(stop_exc, "reason", None) or "operator_accept"
        if best_loss < float("inf"):
            current = dict(best)

    keep_best = bool(getattr(spec.solver, "keep_best", True))
    rollback = bool(getattr(spec.solver, "rollback_on_fail", False))
    if aborted and rollback:
        final = dict(x0_vals)
    elif keep_best:
        final = dict(best)
    else:
        final = dict(current)

    sid = session_id or "ensemble"
    return EnsembleOptimizationResult(
        session_id=sid,
        best_loss=best_loss,
        final_values=final,
        evals=evals,
        aborted=aborted,
        early_stopped=early_stopped,
        early_stop_reason=early_stop_reason,
        trace=trace,
    )


__all__ = ["EnsembleOptimizationResult", "run_ensemble_optimization"]
