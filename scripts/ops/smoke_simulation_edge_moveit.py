#!/usr/bin/env python3
"""Smoke-test simulation_edge MuJoCo + MoveIt against the WSL sidecar."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _ensure_paths() -> None:
    root = _repo_root()
    for rel in (
        "backend",
        "simulation_edge/src",
        "packages/cloudlabs_edge_dev/src",
    ):
        path = str(root / rel)
        if path not in sys.path:
            sys.path.insert(0, path)


async def _run(args: argparse.Namespace) -> dict:
    os.environ.setdefault("SIMULATION_EDGE_MUJOCO", "1")
    os.environ.setdefault("SIMULATION_EDGE_PLANNER", "moveit")
    os.environ.setdefault("CLOUDLAB_MOVEIT_URL", "http://127.0.0.1:8765")
    os.environ.setdefault("CLOUDLAB_MUJOCO_VIEWER", "0")
    os.environ.setdefault("CLOUDLAB_MUJOCO_REALTIME", "0")
    os.environ.setdefault("CLOUDLAB_SIM_PROFILE", args.profile)
    os.environ.setdefault("CLOUDLAB_MOVEIT_MAX_VELOCITY_SCALE", "0.35")
    os.environ.setdefault("CLOUDLAB_MOVEIT_MAX_ACCELERATION_SCALE", "0.35")
    os.environ.setdefault("CLOUDLAB_MOVEIT_TRAJECTORY_TIME_SCALE", "0.5")
    os.environ.setdefault("CLOUDLAB_MOVEIT_PREPICK_FEASIBILITY_SEEDS", "10")
    os.environ.setdefault("CLOUDLAB_MOVEIT_PREPICK_FEASIBILITY_TIMEOUT_S", "150")

    _ensure_paths()
    from simulation_edge.bootstrap import bootstrap_host

    host, _ = bootstrap_host()
    try:
        before = host.simulator_status()
        result = await host.move_component(
            args.tag,
            x=args.x_mm,
            y=args.y_mm,
            rotation=args.rotation_deg,
        )
        state = host.get_lab_state()
        pose = (
            (state.get("components") or {})
            .get(args.tag, {})
            .get("capabilities", {})
            .get("statecontrol", {})
            .get("tunables", {})
            .get("nominal_pose")
        )
        return {
            "ok": state.get("last_runtime_error") is None,
            "before": before,
            "move_result": result,
            "simulator": state.get("simulator"),
            "last_runtime_error": state.get("last_runtime_error"),
            "tag_id": args.tag,
            "tag_nominal_pose": pose,
        }
    finally:
        host.shutdown_lab_processes()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default="optical_housings")
    parser.add_argument("--tag", default="tag_21")
    parser.add_argument("--x-mm", type=float, default=420.0)
    parser.add_argument("--y-mm", type=float, default=260.0)
    parser.add_argument("--rotation-deg", type=float, default=-90.0)
    args = parser.parse_args()

    payload = asyncio.run(_run(args))
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
