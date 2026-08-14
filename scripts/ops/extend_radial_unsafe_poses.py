#!/usr/bin/env python3
"""Add diagnostic-only outer-radius poses to a radial library.

The generated records remain under ``unsafe`` and are never consumed as
executable radial samples. Production safety validation is not weakened.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path
from typing import Any


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _ensure_paths() -> None:
    root = _repo_root()
    for rel in ("backend", "simulation_edge/src", "packages/cloudlabs_edge_dev/src"):
        path = str(root / rel)
        if path not in sys.path:
            sys.path.insert(0, path)


def _default_library() -> Path:
    return (
        _repo_root()
        / "simulation_edge"
        / "radial_motion_libraries"
        / "optical_housings_noninverted.json"
    )


def _extension_radii_mm(
    document: dict[str, Any], max_radius_mm: float
) -> list[float]:
    step_mm = float(document["step_m"]) * 1000.0
    known = {
        round(float(item["radius_m"]) * 1000.0, 6)
        for key in ("samples", "unsafe")
        for item in document.get(key, ())
        if isinstance(item, dict) and item.get("radius_m") is not None
    }
    if not known:
        raise ValueError("library contains no radii")
    radius_mm = max(known) + step_mm
    while radius_mm < max_radius_mm - 1e-6:
        known.add(round(radius_mm, 6))
        radius_mm += step_mm
    known.add(round(max_radius_mm, 6))
    safe = {
        round(float(item["radius_m"]) * 1000.0, 6)
        for item in document.get("samples", ())
        if isinstance(item, dict)
    }
    return sorted(radius for radius in known if radius not in safe)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Record non-executable outer-radius poses under JSON unsafe."
    )
    parser.add_argument("--library", type=Path, default=_default_library())
    parser.add_argument("--max-radius-mm", type=float, default=550.0)
    args = parser.parse_args()

    path = args.library.expanduser().resolve()
    document = json.loads(path.read_text(encoding="utf-8"))
    samples = document.get("samples") or []
    if not samples:
        parser.error("library has no safe samples to use as posture seeds")
    if args.max_radius_mm < float(document["max_radius_m"]) * 1000.0 - 1e-6:
        parser.error("new maximum must not shrink the library's current maximum")

    z_levels = sorted(
        {
            float(pose["z_m"])
            for sample in samples
            for pose in sample.get("vertical_poses", ())
        },
        reverse=True,
    )
    carry_z_m = float(document["carry_z_m"])
    if not z_levels:
        parser.error("library has no vertical pose levels")
    radii_mm = _extension_radii_mm(document, float(args.max_radius_mm))

    os.environ["CLOUDLABS_SKIP_RUNTIME_SYNC"] = "1"
    os.environ["CLOUDLAB_SIM_PROFILE"] = str(document["profile_id"])
    os.environ["CLOUDLAB_RADIAL_LIBRARY_FILE"] = str(path)
    _ensure_paths()

    import mujoco
    import numpy as np

    from simulation_edge.bootstrap import bootstrap_host
    from simulation_edge.host.ik import DampedLeastSquaresIK
    from simulation_edge.host.runtime import ARM_DOF, MUJOCO_PLANNER_RADIAL, MuJoCoRobotRuntime
    from simulation_edge.host.scene import build_scene_spec

    host, _ = bootstrap_host()
    scene = build_scene_spec(host.layout, host.catalog, host.current_state)
    runtime = MuJoCoRobotRuntime(
        scene,
        show_viewer=False,
        realtime=False,
        planner_backend=MUJOCO_PLANNER_RADIAL,
    )
    target_rotation = runtime._target_rotation(0.0)

    safe_by_radius = sorted(samples, key=lambda item: float(item["radius_m"]))
    anchor = safe_by_radius[-1]
    anchor_radius_m = float(anchor["radius_m"])
    anchor_poses = {
        round(float(pose["z_m"]), 9): pose
        for pose in anchor.get("vertical_poses", ())
    }
    unsafe_existing = {
        round(float(item["radius_m"]) * 1000.0, 6): dict(item)
        for item in document.get("unsafe", ())
        if isinstance(item, dict) and item.get("radius_m") is not None
    }
    records: dict[float, dict[str, Any]] = {}

    try:
        # Solve each Z row outward from the last safe sample. This preserves the
        # library's noninverted shoulder/elbow branch without treating the poses
        # as safe or preflighting paths through them.
        for z_m in z_levels:
            anchor_pose = anchor_poses.get(round(z_m, 9))
            if anchor_pose is None:
                raise ValueError(f"safe anchor lacks z={z_m:.6f} m")
            seed = np.asarray(anchor_pose["joints"], dtype=float).copy()
            current_radius_m = anchor_radius_m
            for radius_mm in radii_mm:
                radius_m = radius_mm / 1000.0
                distance = radius_m - current_radius_m
                count = max(1, int(math.ceil(abs(distance) / 0.005)))
                for index in range(1, count + 1):
                    step_radius = current_radius_m + distance * index / count
                    target = np.array((step_radius, 0.0, z_m), dtype=float)
                    solved = runtime.ik.solve(
                        runtime.model,
                        target,
                        target_rotation,
                        seed,
                        seed,
                    )
                    solved = runtime._nearest_equivalent_joints(solved, seed)
                    DampedLeastSquaresIK._clip_joint_limits(runtime.model, solved)
                    seed = np.asarray(solved, dtype=float).copy()
                current_radius_m = radius_m

                runtime.data.qpos[:ARM_DOF] = seed
                runtime.data.ctrl[:ARM_DOF] = seed
                mujoco.mj_forward(runtime.model, runtime.data)
                tcp = runtime.data.site("link_tcp").xpos.copy()
                target = np.array((radius_m, 0.0, z_m), dtype=float)
                frame = runtime._radial_frame_boundary_report(runtime.data)
                height = runtime._radial_height_zone_report(runtime.data)
                pose = {
                    "frame_clearance_m": float(frame.min_clearance_m),
                    "frame_ok": bool(frame.ok),
                    "height_clearance_m": float(height.min_clearance_m),
                    "height_ok": bool(height.ok),
                    "joints": seed.tolist(),
                    "tcp_error_m": float(np.linalg.norm(tcp - target)),
                    "z_m": float(z_m),
                }
                records.setdefault(radius_mm, {"vertical_poses": []})[
                    "vertical_poses"
                ].append(pose)

        carry_key = min(z_levels, key=lambda value: abs(value - carry_z_m))
        for radius_mm, generated in records.items():
            poses = sorted(
                generated["vertical_poses"],
                key=lambda pose: float(pose["z_m"]),
                reverse=True,
            )
            carry_pose = min(
                poses, key=lambda pose: abs(float(pose["z_m"]) - carry_key)
            )
            existing = unsafe_existing.get(radius_mm, {})
            existing.update(
                {
                    "diagnostic_only": True,
                    "joints": carry_pose["joints"],
                    "max_tcp_error_m": max(
                        float(pose["tcp_error_m"]) for pose in poses
                    ),
                    "min_frame_clearance_m": min(
                        float(pose["frame_clearance_m"]) for pose in poses
                    ),
                    "radius_m": radius_mm / 1000.0,
                    "theta_deg": 0.0,
                    "vertical_poses": poses,
                }
            )
            legacy_error = existing.get("error")
            if legacy_error and not existing.get("legacy_generation_error"):
                existing["legacy_generation_error"] = legacy_error
            existing["error"] = (
                "diagnostic-only kinematic radius; runtime analytical "
                "(radius, theta) validation required"
            )
            unsafe_existing[radius_mm] = existing

        document["max_radius_m"] = float(args.max_radius_mm) / 1000.0
        document["unsafe"] = [
            unsafe_existing[radius]
            for radius in sorted(unsafe_existing, reverse=True)
        ]
        path.write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    finally:
        runtime.close()

    print(
        json.dumps(
            {
                "library": str(path),
                "max_radius_mm": float(args.max_radius_mm),
                "unsafe_radius_count": len(unsafe_existing),
                "vertical_levels_per_radius": len(z_levels),
                "executable_sample_max_radius_mm": max(
                    float(item["radius_m"]) for item in samples
                )
                * 1000.0,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
