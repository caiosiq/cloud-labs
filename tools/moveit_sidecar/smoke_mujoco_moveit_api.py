#!/usr/bin/env python3
"""Smoke-test the FastAPI UI route into the MuJoCo MoveIt runtime."""

from __future__ import annotations

import json
import os
import shutil
import sys
import asyncio
import argparse
from datetime import datetime
from pathlib import Path

from fastapi import BackgroundTasks


CLOUD_LABS_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = CLOUD_LABS_ROOT / "backend"
WORKSPACE_ROOT = CLOUD_LABS_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def _prepare_lab_view_copy() -> Path:
    source = BACKEND_ROOT / "lab_communicator" / "mock" / "lab_view"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = CLOUD_LABS_ROOT / ".tmp" / f"mujoco_moveit_api_lab_view_{stamp}"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target)
    return target


async def _run_smoke(
    *,
    tag_id: str,
    target_x_mm: float,
    target_y_mm: float,
    target_rotation_deg: float,
) -> dict:
    lab_view_path = _prepare_lab_view_copy()
    os.environ["LAB_VIEW_PATH"] = str(lab_view_path)
    os.environ["CLOUDLAB_SIM_PROFILE"] = "optical_housings"
    os.environ["CLOUDLAB_MOVEIT_URL"] = "http://127.0.0.1:8765"
    os.environ["CLOUDLAB_MUJOCO_VIEWER"] = "0"
    os.environ["CLOUDLAB_MUJOCO_REALTIME"] = "0"
    os.environ["ROBOTIC_TWIN_WORKSPACE"] = str(WORKSPACE_ROOT)

    import main as backend_main  # noqa: PLC0415

    command = {
        "action": "MOVE_COMPONENT",
        "target_id": tag_id,
        "parameters": {
            "target_x": float(target_x_mm),
            "target_y": float(target_y_mm),
            "rotation": float(target_rotation_deg),
        },
    }

    before = await backend_main.get_runtime_mode()
    switch = await backend_main.set_runtime_mode(
        backend_main.RuntimeModeBody(mode="mujoco_moveit")
    )
    background_tasks = BackgroundTasks()
    response = await backend_main.receive_command(command, background_tasks)
    await background_tasks()

    state_json = backend_main.lab.get_lab_state()
    mode_json = await backend_main.get_runtime_mode()

    tag_state = state_json["components"][tag_id]["statecontrol"]["tunables"][
        "nominal_pose"
    ]
    simulator = mode_json.get("simulator") or {}
    last_error = mode_json.get("last_simulator_error") or simulator.get("last_error")
    pose_error_mm = (
        (float(tag_state["x"]) - float(command["parameters"]["target_x"])) ** 2
        + (float(tag_state["y"]) - float(command["parameters"]["target_y"])) ** 2
    ) ** 0.5
    yaw_error_deg = abs(
        (
            float(tag_state["rotation"])
            - float(command["parameters"]["rotation"])
            + 180.0
        )
        % 360.0
        - 180.0
    )
    ok = last_error is None and pose_error_mm <= 8.0 and yaw_error_deg <= 2.0
    result = {
        "ok": ok,
        "lab_view_path": str(lab_view_path),
        "before": before,
        "switch": switch,
        "command_response": response,
        "mode_after": mode_json,
        "tag_id": tag_id,
        "tag_nominal_pose": tag_state,
        "pose_error_mm": pose_error_mm,
        "yaw_error_deg": yaw_error_deg,
        "last_error": last_error,
        "simulator_log_path": simulator.get("log_path"),
    }

    if backend_main.runtime_manager is not None:
        backend_main.runtime_manager.shutdown_lab_processes()

    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", default="tag_9")
    parser.add_argument("--x-mm", type=float, default=163.4)
    parser.add_argument("--y-mm", type=float, default=-105.1)
    parser.add_argument("--rotation-deg", type=float, default=0.0)
    args = parser.parse_args()

    result = asyncio.run(
        _run_smoke(
            tag_id=args.tag,
            target_x_mm=args.x_mm,
            target_y_mm=args.y_mm,
            target_rotation_deg=args.rotation_deg,
        )
    )
    print(json.dumps(result, indent=2))
    if not result["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
