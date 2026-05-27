"""OPTIMIZE primitive orchestration (autonomous TeleOp at the edge)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Dict, Optional

from lab_model.domain.component import get_tunables
from lab_model.domain.holding import SYSTEM_STATUS_IDLE, SYSTEM_STATUS_OPTIMIZING
from lab_model.state.commits import commit_optimization_complete, null_measurables_for_targets
from lab_model.state.state_machine import refuse_if_stored, refuse_if_teleop_active

from .cobyla_reference import refuse_if_cobyla_without_reference
from .optimize_params import normalize_optimize_params
from .optimize_policy import (
    refuse_if_loss_metric_not_allowed,
    refuse_if_optimize_strategy_not_allowed,
)
from .protocol import OptimizeHost

LivePoseCallback = Callable[..., None]


async def run_optimize_component(
    host: OptimizeHost,
    target_id: str,
    strategy_name: str,
    params: Dict[str, Any],
) -> None:
    catalog_row = host._catalog_meta_for_tag(target_id)
    if not catalog_row:
        print(f"{host.log_prefix} Refusing optimize: {target_id} not in catalog.")
        return

    params = normalize_optimize_params(catalog_row, target_id, strategy_name, params)
    strategy_name = params["strategy"]
    sensor = params.get("sensor_component")

    print(
        f"{host.log_prefix} Optimize {target_id} strategy={strategy_name!r} "
        f"sensor={sensor!r} loss_metric={params.get('loss_metric')!r}"
    )

    refusal = refuse_if_optimize_strategy_not_allowed(
        catalog_row, target_id, strategy_name
    )
    if refusal:
        print(f"{host.log_prefix} Refusing optimize: {refusal.reason}")
        return

    refusal = refuse_if_loss_metric_not_allowed(catalog_row, strategy_name, params)
    if refusal:
        print(f"{host.log_prefix} Refusing optimize: {refusal.reason}")
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

    catalog_map = getattr(host, "catalog_map", None)
    refusal = refuse_if_cobyla_without_reference(
        snapshot,
        strategy_name,
        params,
        catalog_map=catalog_map,
    )
    if refusal:
        print(f"{host.log_prefix} Refusing optimize: {refusal.reason}")
        return

    null_targets = [target_id]
    if isinstance(sensor, str) and sensor.strip():
        null_targets.append(sensor.strip())

    with host._state_lock:
        null_measurables_for_targets(host.current_state, null_targets)

    run_dir_basename: Optional[str] = None
    try:
        run_dir_basename = host._primitive_prepare_optimization_run(
            target_id, strategy_name
        )
    except Exception as e:  # noqa: BLE001
        print(f"{host.log_prefix} Optimization prepare failed: {e}")
        return

    with host._state_lock:
        host.current_state["system_status"] = SYSTEM_STATUS_OPTIMIZING
        host.current_state["optimization_target_id"] = target_id
        host.current_state["optimization_run_dir"] = run_dir_basename
        host.current_state["optimization_step"] = 0
        host.current_state["last_updated"] = datetime.now().isoformat()
    host._persist_state()

    initial_pose: Dict[str, Any] = {}
    initial_motor_positions: Dict[str, float] = {}
    with host._state_lock:
        entry = (host.current_state.get("components") or {}).get(target_id)
        if isinstance(entry, dict):
            tun = get_tunables(entry)
            initial_pose = dict(tun.get("nominal_pose") or {})
            nmp = tun.get("nominal_motor_positions") or {}
            if isinstance(nmp, dict):
                for k, v in nmp.items():
                    try:
                        initial_motor_positions[str(k)] = float(v)
                    except (TypeError, ValueError):
                        continue

    host._begin_optimize_live_session(
        target_id,
        initial_pose,
        initial_motor_positions=initial_motor_positions or None,
    )

    def live_pose_callback(
        *,
        pose: Optional[Dict[str, float]] = None,
        motor_positions: Optional[Dict[str, Any]] = None,
        motor_deltas: Optional[Dict[str, Any]] = None,
        iteration: Optional[int] = None,
        loss: Optional[float] = None,
    ) -> None:
        host._optimize_live_pose_update(
            target_id,
            pose or {},
            motor_positions=motor_positions,
            motor_deltas=motor_deltas,
            iteration=iteration,
            loss=loss,
        )

    result: Optional[Dict[str, Any]] = None
    try:
        result = await host._primitive_optimize_component(
            target_id=target_id,
            strategy_name=strategy_name,
            params=params,
            live_pose_callback=live_pose_callback,
        )
    except Exception as e:  # noqa: BLE001
        print(f"{host.log_prefix} Optimization failed: {e}")
        result = None
    finally:
        host._end_optimize_live_session(target_id)
        try:
            host._primitive_finalize_optimization_run()
        except Exception as fin_e:  # noqa: BLE001
            print(f"{host.log_prefix} Optimization finalize failed: {fin_e}")
        with host._state_lock:
            host.current_state["system_status"] = SYSTEM_STATUS_IDLE
            host.current_state["optimization_target_id"] = None
            host.current_state["optimization_run_dir"] = None
            host.current_state["optimization_step"] = None
            host.current_state["last_updated"] = datetime.now().isoformat()
        host._persist_state()

    if isinstance(result, dict):
        score = float(result.get("score", 1.0))
        final_pose = result.get("final_pose")
        final_motors = result.get("final_motor_positions")
        with host._state_lock:
            commit_optimization_complete(
                host.current_state,
                target_id,
                score=score,
                final_pose=final_pose if isinstance(final_pose, dict) else None,
                final_motor_positions=(
                    final_motors if isinstance(final_motors, dict) else None
                ),
            )
            host.current_state["last_updated"] = datetime.now().isoformat()
        host._persist_state()
        print(
            f"{host.log_prefix} Optimization complete for {target_id} "
            f"({strategy_name}) score={score:.4f}"
        )
