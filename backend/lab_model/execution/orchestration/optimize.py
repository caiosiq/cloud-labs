"""OPTIMIZE primitive orchestration — ensemble only."""
from __future__ import annotations

from typing import Any, Dict

from .protocol import OptimizeHost


async def run_optimize_component(
    host: OptimizeHost,
    target_id: str,
    strategy_name: str,
    params: Dict[str, Any],
) -> None:
    """Dispatch OPTIMIZE to the ensemble path.

    ``strategy_name`` is retained for call-site compatibility (session label /
    ``"ensemble"``); legacy NEWTON/COBYLA strategy mode is removed.
    """
    params = dict(params or {})
    mode = str(params.get("mode") or "").strip().lower()
    if mode != "ensemble":
        print(
            f"{host.log_prefix} Refusing optimize {target_id}: "
            f"OPTIMIZE requires parameters.mode=ensemble "
            f"(got mode={mode!r}, strategy={strategy_name!r}). "
            f"Use Alignment session / SDK run_optimize / run_cobyla."
        )
        return

    from .optimize_ensemble import run_optimize_ensemble

    await run_optimize_ensemble(host, target_id, params)
