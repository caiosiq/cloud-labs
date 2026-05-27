"""Real OPTIMIZE edge loop — delegates to ``lab_automation.managers.optimize_runner``."""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

if TYPE_CHECKING:
    from lab_communicator.real.communicator import RealLabCommunicator

LivePoseCallback = Callable[..., None]


def build_lab_automation_optimize_spec(
    communicator: "RealLabCommunicator",
    target_id: str,
    strategy_name: str,
    params: Dict[str, Any],
    run_dir: str,
) -> Dict[str, Any]:
    """Assemble the kwargs ``lab_automation.run_optimize`` expects."""
    meta = communicator.catalog_map.get(target_id) or {}
    strat = (strategy_name or "").upper()
    sensor = params.get("sensor_component")
    spec: Dict[str, Any] = {
        "target_id": target_id,
        "strategy": strat,
        "sensor_component": sensor,
        "loss_metric": params.get("loss_metric"),
        "video_exposure": params.get("video_exposure"),
        "run_dir": run_dir,
        "motor_controller": meta.get("motor_controller"),
        "motor_ids": params.get("motor_ids") or meta.get("motor_ids") or [],
    }
    if strat == "NEWTON":
        spec["axis"] = params.get("axis")
        spec["tolerance_ratio"] = params.get("tolerance_ratio")
    if strat == "COBYLA":
        spec["reference_image_path"] = _pinned_reference_path(communicator)
    return spec


def _pinned_reference_path(communicator: "RealLabCommunicator") -> Optional[str]:
    from lab_model.orchestration.cobyla_reference import cobyla_reference_path

    with communicator._state_lock:
        state = communicator.current_state
    return cobyla_reference_path(state, None)


def _robot_pose_to_lab(
    communicator: "RealLabCommunicator", target_id: str, pose: Dict[str, Any]
) -> Dict[str, float]:
    return communicator._ui_pose_for_placement_tick(
        target_id,
        pose.get("x"),
        pose.get("y"),
    )


def _wrap_callbacks(
    communicator: "RealLabCommunicator",
    target_id: str,
    live_pose_callback: LivePoseCallback,
):
    def on_motion(pose_robot: Dict[str, float]) -> None:
        lab_pose = _robot_pose_to_lab(communicator, target_id, pose_robot)
        live_pose_callback(pose=lab_pose)

    def on_step(**kwargs: Any) -> None:
        pose = kwargs.get("pose") or {}
        lab_pose = _robot_pose_to_lab(communicator, target_id, pose)
        live_pose_callback(
            pose=lab_pose,
            motor_positions=kwargs.get("motor_positions"),
            iteration=kwargs.get("iteration"),
            loss=kwargs.get("loss"),
        )

    return on_motion, on_step


async def run_optimize_edge(
    communicator: "RealLabCommunicator",
    *,
    target_id: str,
    strategy_name: str,
    params: Dict[str, Any],
    live_pose_callback: LivePoseCallback,
    run_dir: str,
) -> Optional[Dict[str, Any]]:
    """Run one optimization on the real backend via ``lab_automation.run_optimize``."""
    spec = build_lab_automation_optimize_spec(
        communicator, target_id, strategy_name, params, run_dir
    )
    strat = spec["strategy"]

    if strat == "COBYLA":
        _require_cobyla_reference(communicator, params)
        if not spec.get("motor_ids"):
            raise ValueError(
                f"COBYLA requires motor_ids for {target_id!r} "
                "(catalog motor_ids or explicit param)."
            )
        if not spec.get("motor_controller"):
            raise ValueError(
                f"COBYLA requires motor_controller in catalog for {target_id!r}."
            )

    experiment = getattr(communicator, "experiment", None)
    if experiment is None:
        raise RuntimeError("RealLabCommunicator.experiment is not initialized")

    print(
        f"[REAL LAB] optimize {strat} target={target_id} "
        f"sensor={spec.get('sensor_component')!r} "
        f"loss_metric={spec.get('loss_metric')!r} "
        f"run_dir={run_dir}"
    )

    from lab_automation.managers.optimize_runner import run_optimize

    on_motion, on_step = _wrap_callbacks(communicator, target_id, live_pose_callback)

    result = await asyncio.to_thread(
        run_optimize,
        experiment,
        spec=spec,
        on_motion=on_motion,
        on_step=on_step,
    )

    if isinstance(result, dict) and isinstance(result.get("final_pose"), dict):
        result = dict(result)
        result["final_pose"] = _robot_pose_to_lab(
            communicator, target_id, result["final_pose"]
        )
    return result


def _require_cobyla_reference(
    communicator: "RealLabCommunicator", params: Dict[str, Any]
) -> None:
    from lab_communicator.real.optimization import load_cobyla_reference_bgr_from_state

    sensor = params.get("sensor_component")
    ref = load_cobyla_reference_bgr_from_state(
        communicator, sensor_tag_id=str(sensor).strip() if sensor else None
    )
    if ref is None:
        raise ValueError(
            "COBYLA requires a pinned lab optimization reference. "
            "Record a camera image and run set_cobyla_reference first."
        )
