"""Legacy single-tag strategy → ensemble spec (compile-time helper)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .spec import (
    ObjectiveSpec,
    ObjectiveSourceSpec,
    ObjectiveTermSpec,
    OptimizeEnsembleParameters,
    SolverBlockSpec,
    SolverSpec,
    VariableRef,
)


def compile_legacy_strategy(
    *,
    target_id: str,
    strategy: str,
    motor_ids: Optional[List[int]] = None,
) -> Dict[str, Any]:
    """
    Build an ensemble-shaped dict equivalent to legacy NEWTON/COBYLA on one tag.

    Used for migration/tests and optional Step E redirect
    (``CLOUDLABS_LEGACY_OPTIMIZE_REDIRECT=1`` on mock). Not the default on real.
    """
    strategy_u = (strategy or "NEWTON").upper()
    mids = motor_ids if motor_ids else [1, 3]
    variables: List[VariableRef] = []
    for mid in mids:
        variables.append(
            VariableRef(
                id=f"v_{target_id}_m{mid}",
                tag_id=target_id,
                path=f"tunables.nominal_motor_positions.{mid}",
                physical_type="continuous",
                unit="deg",
                bounds={"min": -3.0, "max": 3.0},
                delta=True,
            )
        )

    objective = ObjectiveSpec(
        terms=[
            ObjectiveTermSpec(
                id="legacy_score",
                weight=1.0,
                source=ObjectiveSourceSpec(
                    tag_id=target_id,
                    kind="measurable_scalar",
                    path="measurables.last_optimization_score",
                ),
                metric="one_minus_normalized",
            )
        ]
    )
    solver = SolverSpec(
        blocks=[
            SolverBlockSpec(
                id=f"block_{target_id}",
                variable_ids=[v.id for v in variables],
                max_evals=30,
            )
        ]
    )
    spec = OptimizeEnsembleParameters(
        mode="ensemble",
        session_label=f"legacy {strategy_u}",
        variables=variables,
        objective=objective,
        solver=solver,
    )
    return spec.model_dump()


__all__ = ["compile_legacy_strategy"]
