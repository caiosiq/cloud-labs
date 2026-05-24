"""OPTIMIZE primitive orchestration."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from lab_model.domain.holding import SYSTEM_STATUS_IDLE, SYSTEM_STATUS_OPTIMIZING
from lab_model.state.commits import commit_optimization_complete, null_measurables_for_targets
from lab_model.state.state_machine import refuse_if_stored, refuse_if_teleop_active

from .protocol import OptimizeHost


async def run_optimize_component(
    host: OptimizeHost,
    target_id: str,
    strategy_name: str,
    params: Dict[str, Any],
) -> None:
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
