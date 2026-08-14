"""Block COBYLA stepper (predefined engine code — not a shipped kernel)."""
from __future__ import annotations

from typing import Any, Callable, Dict, Mapping, Sequence

import numpy as np
from scipy.optimize import minimize

from .normalize import NormalizedSearchSpace


def run_block_cobyla(
    block: Any,
    variable_ids: Sequence[str],
    space: NormalizedSearchSpace,
    current_physical: Mapping[str, float],
    objective_fn: Callable[[Mapping[str, float]], float],
    *,
    max_evals: int,
) -> Dict[str, float]:
    """Run COBYLA on a subset of variables in normalized [0,1]^k space."""
    ids = list(variable_ids)
    if not ids:
        return dict(current_physical)

    x0_u = np.array(
        [space.normalize_scalar(vid, float(current_physical[vid])) for vid in ids]
    )
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
        "rhobeg": float(getattr(block, "rhobeg_u", 0.05)),
        "catol": float(getattr(block, "rhoend_u", 0.002)),
    }
    minimize(_wrapped, x0_u, method="COBYLA", bounds=bounds, options=options)
    if eval_count == 0:
        _wrapped(x0_u)
    return best_physical


__all__ = ["run_block_cobyla"]
