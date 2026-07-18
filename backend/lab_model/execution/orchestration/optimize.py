"""OPTIMIZE primitive orchestration."""
from __future__ import annotations

import logging
import os
import warnings
from datetime import datetime
from typing import Any, Dict, Optional

from lab_model.language.domain.holding import SYSTEM_STATUS_IDLE, SYSTEM_STATUS_OPTIMIZING
from lab_model.coordinator.state.commits import commit_optimization_complete, null_measurables_for_targets
from lab_model.coordinator.state.state_machine import refuse_if_stored, refuse_if_teleop_active

from .protocol import OptimizeHost

_LOG = logging.getLogger(__name__)

_LEGACY_DEPRECATION = (
    "OPTIMIZE mode=legacy_strategy is deprecated (Step E). Prefer parameters.mode=ensemble "
    "or SDK run_optimize/run_cobyla. See docs/FUTURE_STEPS_DISTRIBUTED_AND_SDK.md Step E."
)


def _legacy_redirect_enabled(host: OptimizeHost) -> bool:
    """Optional mock-safe redirect of legacy NEWTON/COBYLA → ensemble IR.

    Enabled only when ``CLOUDLABS_LEGACY_OPTIMIZE_REDIRECT=1``. Never redirects
    real benches — operators still rely on MJPEG / Newton place-UI telemetry.
    """
    flag = (os.environ.get("CLOUDLABS_LEGACY_OPTIMIZE_REDIRECT") or "").strip().lower()
    if flag not in ("1", "true", "yes"):
        return False
    lab_mode = str(getattr(host, "lab_mode", "") or "").upper()
    if not lab_mode:
        # Fall back to communicator class name heuristics.
        name = type(host).__name__.lower()
        if "real" in name:
            return False
    elif lab_mode == "REAL":
        return False
    return True


def _try_redirect_legacy_to_ensemble(
    host: OptimizeHost,
    target_id: str,
    strategy_name: str,
    params: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Compile legacy strategy params into ensemble IR, or return None to keep legacy."""
    from lab_model.execution.optimization.presets import compile_legacy_strategy

    strategy_u = (strategy_name or "NEWTON").upper()
    if strategy_u not in ("NEWTON", "COBYLA"):
        return None
    motor_ids = params.get("motor_ids")
    mids: Optional[list] = None
    if isinstance(motor_ids, list) and motor_ids:
        mids = [int(x) for x in motor_ids]
    compiled = compile_legacy_strategy(
        target_id=target_id,
        strategy=strategy_u,
        motor_ids=mids,
    )
    # Preserve settle / exposure knobs when present.
    for key in ("settle_ms", "exposure", "kernels"):
        if key in params and params[key] is not None:
            compiled[key] = params[key]
    return compiled


async def run_optimize_component(
    host: OptimizeHost,
    target_id: str,
    strategy_name: str,
    params: Dict[str, Any],
) -> None:
    params = params or {}
    if params.get("mode") == "ensemble":
        from .optimize_ensemble import run_optimize_ensemble

        await run_optimize_ensemble(host, target_id, params)
        return

    warnings.warn(_LEGACY_DEPRECATION, DeprecationWarning, stacklevel=2)
    _LOG.warning(
        "optimize_path=legacy target=%s strategy=%s — %s",
        target_id,
        strategy_name,
        _LEGACY_DEPRECATION,
    )

    if _legacy_redirect_enabled(host):
        compiled = _try_redirect_legacy_to_ensemble(
            host, target_id, strategy_name, params
        )
        if compiled is not None:
            _LOG.info(
                "optimize_path=legacy_redirect target=%s strategy=%s → ensemble",
                target_id,
                strategy_name,
            )
            from .optimize_ensemble import run_optimize_ensemble

            await run_optimize_ensemble(host, target_id, compiled)
            return

    print(
        f"{host.log_prefix} Optimize {target_id} with {strategy_name} "
        f"(params keys={sorted((params or {}).keys())})"
    )

    if not host._catalog_meta_for_tag(target_id):
        print(
            f"{host.log_prefix} Refusing optimize: {target_id} not in catalog."
        )
        return

    with host._state_lock:
        snapshot = host.current_state
    refusal = refuse_if_stored(snapshot, target_id, primitive_name="optimize")
    if refusal:
        print(f"{host.log_prefix} Refusing optimize: {refusal.reason}")
        return
    refusal = refuse_if_teleop_active(snapshot, target_id, primitive_name="optimize")
    if refusal:
        print(f"{host.log_prefix} Refusing optimize: {refusal.reason}")
        return

    run_dir_basename = host._primitive_prepare_optimization_run(target_id, strategy_name)

    with host._state_lock:
        null_measurables_for_targets(host.current_state, [target_id])
        host.current_state["system_status"] = SYSTEM_STATUS_OPTIMIZING
        host.current_state["optimization_step"] = 0
        host.current_state["optimization_run_dir"] = run_dir_basename
        host.current_state["optimization_target_id"] = target_id
        host.current_state["last_updated"] = datetime.now().isoformat()
    host._persist_state()

    def progress_callback(*, step: Optional[int] = None) -> None:
        with host._state_lock:
            if step is not None:
                host.current_state["optimization_step"] = int(step)
            host.current_state["last_updated"] = datetime.now().isoformat()
        host._persist_state()

    result: Optional[Dict[str, Any]] = None
    try:
        result = await host._primitive_optimize_component(
            target_id=target_id,
            strategy_name=strategy_name,
            params=params or {},
            progress_callback=progress_callback,
        )
    except Exception as e:  # noqa: BLE001
        print(f"{host.log_prefix} Optimization failed: {e}")
        result = None

    if isinstance(result, dict):
        score = float(result.get("score", 1.0))
        final_pose = result.get("final_pose")
        with host._state_lock:
            commit_optimization_complete(
                host.current_state,
                target_id,
                strategy_name=strategy_name,
                score=score,
                final_pose=final_pose if isinstance(final_pose, dict) else None,
            )
            host.current_state["last_updated"] = datetime.now().isoformat()
        host._persist_state()

    try:
        host._primitive_finalize_optimization_run()
    finally:
        with host._state_lock:
            host.current_state["system_status"] = SYSTEM_STATUS_IDLE
            host.current_state["optimization_step"] = 0
            host.current_state["optimization_run_dir"] = None
            host.current_state["optimization_target_id"] = None
            host.current_state["last_updated"] = datetime.now().isoformat()
        host._persist_state()
        print(
            f"{host.log_prefix} Optimization complete for {target_id} "
            f"({strategy_name})"
        )
