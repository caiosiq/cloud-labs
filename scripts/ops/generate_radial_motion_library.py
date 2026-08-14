#!/usr/bin/env python3
"""Generate the MuJoCo radial pickup, carry, and placement library."""

from __future__ import annotations

import argparse
import json
import math
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
    parser.add_argument("--min-vertical-z-m", type=float)
    parser.add_argument("--height-margin-mm", type=float)
    parser.add_argument(
        "--output-file",
        type=Path,
        help=(
            "Write to this radial-library JSON instead of replacing the "
            "profile's default library."
        ),
    )
    parser.add_argument(
        "--seed-joints-deg",
        type=float,
        nargs=7,
        metavar=("J1", "J2", "J3", "J4", "J5", "J6", "J7"),
        help=(
            "Use this xArm joint posture as the IK seed/posture reference. "
            "The target TCP orientation and all normal safety checks remain unchanged."
        ),
    )
    parser.add_argument(
        "--branch-name",
        help="Optional descriptive name recorded in generation metadata.",
    )
    parser.add_argument(
        "--reference-home-joints-deg",
        type=float,
        nargs=7,
        metavar=("J1", "J2", "J3", "J4", "J5", "J6", "J7"),
        help=(
            "Optional physical home posture recorded as provenance. This does "
            "not alter the IK seed or generated task orientation."
        ),
    )
    args = parser.parse_args()

    os.environ["CLOUDLAB_SIM_PROFILE"] = args.profile
    _set_optional_env("CLOUDLAB_RADIAL_MIN_RADIUS_MM", args.min_radius_mm)
    _set_optional_env("CLOUDLAB_RADIAL_MAX_RADIUS_MM", args.max_radius_mm)
    _set_optional_env("CLOUDLAB_RADIAL_STEP_MM", args.step_mm)
    _set_optional_env("CLOUDLAB_RADIAL_CARRY_Z_M", args.carry_z_m)
    _set_optional_env(
        "CLOUDLAB_RADIAL_MIN_VERTICAL_Z_M",
        args.min_vertical_z_m,
    )
    if args.height_margin_mm is not None:
        os.environ["CLOUDLAB_RADIAL_HEIGHT_ZONE_MARGIN_M"] = str(
            args.height_margin_mm / 1000.0
        )
    if args.output_file is not None:
        os.environ["CLOUDLAB_RADIAL_LIBRARY_FILE"] = str(
            args.output_file.expanduser().resolve()
        )

    _ensure_paths()

    from simulation_edge.bootstrap import bootstrap_host
    from simulation_edge.host.runtime import (
        MUJOCO_PLANNER_CUSTOM_IK,
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
        # Generation must not bootstrap from the existing radial library: a
        # deliberate scene-geometry correction makes its old joints stale.
        planner_backend=MUJOCO_PLANNER_CUSTOM_IK,
    )
    try:
        if args.seed_joints_deg is not None:
            import numpy as np

            # Keep the existing radial TCP orientation; only change the
            # deterministic IK seed/posture family. This lets a caller select
            # the legacy shoulder/elbow branch without changing the task pose.
            runtime.home = np.asarray(
                [math.radians(value) for value in args.seed_joints_deg],
                dtype=float,
            )
        library = runtime.generate_radial_motion_library(write=True)
        output_path = runtime._radial_library_path()
        if (
            args.seed_joints_deg is not None
            or args.reference_home_joints_deg is not None
            or args.branch_name
        ):
            document = json.loads(output_path.read_text(encoding="utf-8"))
            document["generation"] = {
                "branch_name": args.branch_name,
                "ik_seed_xarm_deg": (
                    [float(value) for value in args.seed_joints_deg]
                    if args.seed_joints_deg is not None
                    else None
                ),
                "reference_home_xarm_deg": (
                    [float(value) for value in args.reference_home_joints_deg]
                    if args.reference_home_joints_deg is not None
                    else None
                ),
                "tcp_orientation_source": "existing radial reference",
            }
            output_path.write_text(
                json.dumps(document, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        low, high = library.radius_bounds_m
        all_joint_rows = [
            pose.joints
            for sample in library.samples
            for pose in sample.vertical_poses
        ]
        joint_min_deg = None
        joint_max_deg = None
        if all_joint_rows:
            import numpy as np

            all_joints = np.asarray(all_joint_rows, dtype=float)
            joint_min_deg = np.degrees(all_joints).min(axis=0).tolist()
            joint_max_deg = np.degrees(all_joints).max(axis=0).tolist()
        summary = {
            "ok": True,
            "path": str(output_path),
            "profile_id": library.profile_id,
            "branch_name": args.branch_name,
            "ik_seed_xarm_deg": args.seed_joints_deg,
            "reference_home_xarm_deg": args.reference_home_joints_deg,
            "sample_count": len(library.samples),
            "unsafe_count": len(library.unsafe),
            "radius_range_mm": [low * 1000.0, high * 1000.0],
            "carry_z_m": library.carry_z_m,
            "grasp_z_m": library.grasp_z_m,
            "max_vertical_z_m": library.max_vertical_z_m,
            "min_vertical_z_m": min(
                pose.z_m
                for sample in library.samples
                for pose in sample.vertical_poses
            ),
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
            "joint_min_deg": joint_min_deg,
            "joint_max_deg": joint_max_deg,
        }
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0 if not library.unsafe else 1
    finally:
        runtime.close()


if __name__ == "__main__":
    raise SystemExit(main())
