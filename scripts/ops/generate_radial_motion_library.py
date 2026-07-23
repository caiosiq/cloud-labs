#!/usr/bin/env python3
"""Generate the MuJoCo radial pickup, carry, and placement library."""

from __future__ import annotations

import argparse
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


def _set_optional_env(name: str, value: object | None) -> None:
    if value is not None:
        os.environ[name] = str(value)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default="optical_housings")
    parser.add_argument("--min-radius-mm", type=float)
    parser.add_argument("--max-radius-mm", type=float)
    parser.add_argument("--step-mm", type=float)
    parser.add_argument("--carry-z-m", type=float)
    parser.add_argument("--height-margin-mm", type=float)
    args = parser.parse_args()

    os.environ["CLOUDLAB_SIM_PROFILE"] = args.profile
    _set_optional_env("CLOUDLAB_RADIAL_MIN_RADIUS_MM", args.min_radius_mm)
    _set_optional_env("CLOUDLAB_RADIAL_MAX_RADIUS_MM", args.max_radius_mm)
    _set_optional_env("CLOUDLAB_RADIAL_STEP_MM", args.step_mm)
    _set_optional_env("CLOUDLAB_RADIAL_CARRY_Z_M", args.carry_z_m)
    if args.height_margin_mm is not None:
        os.environ["CLOUDLAB_RADIAL_HEIGHT_ZONE_MARGIN_M"] = str(
            args.height_margin_mm / 1000.0
        )

    _ensure_paths()

    from simulation_edge.bootstrap import bootstrap_host
    from simulation_edge.host.runtime import (
        MUJOCO_PLANNER_RADIAL,
        MuJoCoRobotRuntime,
    )
    from simulation_edge.host.scene import build_scene_spec

    host, _ = bootstrap_host()
    scene = build_scene_spec(host.layout, host.catalog, host.current_state)
    host.scene = scene
    host._apply_scene_spawn_adjustments()
    scene = build_scene_spec(host.layout, host.catalog, host.current_state)

    runtime = MuJoCoRobotRuntime(
        scene,
        show_viewer=False,
        realtime=False,
        planner_backend=MUJOCO_PLANNER_RADIAL,
    )
    try:
        library = runtime.generate_radial_motion_library(write=True)
        low, high = library.radius_bounds_m
        summary = {
            "ok": True,
            "path": str(runtime._radial_library_path()),
            "profile_id": library.profile_id,
            "sample_count": len(library.samples),
            "unsafe_count": len(library.unsafe),
            "radius_range_mm": [low * 1000.0, high * 1000.0],
            "carry_z_m": library.carry_z_m,
            "grasp_z_m": library.grasp_z_m,
            "max_vertical_z_m": library.max_vertical_z_m,
            "vertical_pose_count": sum(
                len(sample.vertical_poses) for sample in library.samples
            ),
            "component_height_limit_m": library.component_height_limit_m,
            "height_zone_margin_m": library.height_zone_margin_m,
            "worst_clearance_mm": min(
                sample.min_clearance_m for sample in library.samples
            )
            * 1000.0,
            "max_tcp_error_mm": max(sample.tcp_error_m for sample in library.samples)
            * 1000.0,
        }
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0 if not library.unsafe else 1
    finally:
        runtime.close()


if __name__ == "__main__":
    raise SystemExit(main())
