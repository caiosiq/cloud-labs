"""OPTIMIZE strategy eligibility — placeable targets, motor-gated COBYLA."""
from __future__ import annotations

from typing import Any, Dict, Optional

from lab_model.catalog.schema import (
    catalog_has_motors,
    catalog_is_optimize_placeable,
    catalog_optimize_strategies,
)
from lab_model.state.state_machine import RefusalResult, ok, refuse


def refuse_if_optimize_strategy_not_allowed(
    catalog_row: Optional[Dict[str, Any]],
    target_id: str,
    strategy_name: str,
) -> RefusalResult:
    """Refuse OPTIMIZE when the target or strategy is outside catalog policy."""
    if not catalog_is_optimize_placeable(catalog_row):
        return refuse(
            f"OPTIMIZE is not supported for {target_id!r} "
            "(only breadboard-placeable table components)."
        )

    strat = (strategy_name or "").upper()
    allowed = catalog_optimize_strategies(catalog_row)
    if strat not in allowed:
        if strat == "COBYLA" and not catalog_has_motors(catalog_row):
            return refuse(
                f"COBYLA is only available for motorised components; "
                f"{target_id!r} has no motors."
            )
        return refuse(
            f"Strategy {strategy_name!r} is not allowed for {target_id!r}."
        )
    return ok()


def refuse_if_loss_metric_not_allowed(
    catalog_row: Optional[Dict[str, Any]],
    strategy_name: str,
    params: Dict[str, Any],
) -> RefusalResult:
    """Refuse when ``loss_metric`` is not in the catalog allow-list for this strategy."""
    invalid = params.get("_loss_metric_invalid")
    if not invalid:
        return ok()
    allowed = params.get("_loss_metrics_allowed") or []
    return refuse(
        f"Loss metric {invalid!r} is not allowed for {strategy_name!r} "
        f"(allowed: {', '.join(allowed) or 'none'})."
    )
