"""Block COBYLA optimizer in normalized subspace."""
from __future__ import annotations

from typing import Callable, Dict, Mapping, Sequence

import numpy as np
from scipy.optimize import minimize

from lab_model.optimization.normalize import NormalizedSearchSpace
from lab_model.optimization.spec import SolverBlockSpec


def run_block_cobyla(
    block: SolverBlockSpec,
    variable_ids: Sequence[str],
    space: NormalizedSearchSpace,
    current_physical: Mapping[str, float],
    objective_fn: Callable[[Mapping[str, float]], float],
    *,
    max_evals: int,
) -> Dict[str, float]:
    """
    Run COBYLA on a subset of variables in normalized [0,1]^k space.

    Variables not in ``variable_ids`` stay at ``current_physical`` values.
    """
    ids = list(variable_ids)
    if not ids:
        return dict(current_physical)

    x0_u = np.array([space.normalize_scalar(vid, float(current_physical[vid])) for vid in ids])
    eval_count = 0
    best_loss = float("inf")
    best_physical = dict(current_physical)

    def _wrapped(u_vec: np.ndarray) -> float:
        nonlocal eval_count, best_loss, best_physical
        eval_count += 1
        trial = dict(current_physical)
        for i, vid in enumerate(ids):
            trial[vid] = space.denormalize_scalar(vid, float(u_vec[i]))
        loss = float(objective_fn(trial))
        if loss < best_loss:
            best_loss = loss
            best_physical = trial
        return loss

    bounds = [(0.0, 1.0)] * len(ids)
    options = {
        "maxiter": max(1, int(max_evals)),
        "rhobeg": float(block.rhobeg_u),
        "catol": float(block.rhoend_u),
    }
    result = minimize(
        _wrapped,
        x0_u,
        method="COBYLA",
        bounds=bounds,
        options=options,
    )
    _ = result  # best tracked in objective_fn via best_physical
    if eval_count == 0:
        _wrapped(x0_u)
    return best_physical


__all__ = ["run_block_cobyla"]
