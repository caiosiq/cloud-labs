#!/usr/bin/env python3
"""Interactively inspect radial poses without enforcing safety gates.

This is a visualization/diagnostic tool only. It solves and displays individual
MuJoCo poses; it never commands a physical robot and never executes a path.
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
    for rel in (
        "backend",
        "simulation_edge/src",
        "packages/cloudlabs_edge_dev/src",
    ):
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


def _inspection_radii_mm(document: dict[str, Any]) -> list[float]:
    radii = {
        round(float(sample["radius_m"]) * 1000.0, 6)
        for sample in document.get("samples", [])
        if isinstance(sample, dict) and sample.get("radius_m") is not None
    }
    radii.update(
        round(float(sample["radius_m"]) * 1000.0, 6)
        for sample in document.get("unsafe", [])
        if isinstance(sample, dict) and sample.get("radius_m") is not None
    )
    return sorted(radii)


def _robot_contacts(runtime: Any) -> list[str]:
    robot_geom_ids = set(runtime._robot_geom_ids())
    rows: list[str] = []
    for index in range(int(runtime.data.ncon)):
        contact = runtime.data.contact[index]
        geom1, geom2 = int(contact.geom1), int(contact.geom2)
        if geom1 not in robot_geom_ids and geom2 not in robot_geom_ids:
            continue
        body1 = runtime.model.body(int(runtime.model.geom_bodyid[geom1])).name
        body2 = runtime.model.body(int(runtime.model.geom_bodyid[geom2])).name
        rows.append(
            f"{runtime.model.geom(geom1).name} ({body1}) <-> "
            f"{runtime.model.geom(geom2).name} ({body2}), "
            f"distance={float(contact.dist) * 1000.0:.2f} mm"
        )
    return rows


def _place_held_component(runtime: Any, tag_id: str, yaw_deg: float) -> None:
    import mujoco
    import numpy as np

    spec = runtime.scene.components.get(tag_id)
    if spec is None:
        return
    tcp = runtime.data.site("link_tcp").xpos.copy()
    body_position = tcp - np.array((0.0, 0.0, spec.grasp_site_local_z_m))
    joint_id = runtime.model.joint(spec.joint_name).id
    address = int(runtime.model.jnt_qposadr[joint_id])
    yaw_rad = math.radians(float(yaw_deg))
    runtime.data.qpos[address : address + 3] = body_position
    runtime.data.qpos[address + 3 : address + 7] = (
        math.cos(yaw_rad / 2.0),
        0.0,
        0.0,
        math.sin(yaw_rad / 2.0),
    )
    mujoco.mj_forward(runtime.model, runtime.data)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Open MuJoCo and inspect radial arm poses at a fixed "
            "TCP world-Z height. Unsafe poses are displayed, not executed."
        )
    )
    parser.add_argument("--library", type=Path, default=_default_library())
    parser.add_argument("--carry-z-mm", type=float, default=550.0)
    parser.add_argument("--start-radius-mm", type=float)
    parser.add_argument("--start-theta-deg", type=float, default=0.0)
    parser.add_argument(
        "--held-tag",
        default="tag_18",
        help="Component displayed at the TCP; use 'none' for arm only.",
    )
    parser.add_argument("--no-viewer", action="store_true")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Display/print one pose and exit (requires --start-radius-mm).",
    )
    args = parser.parse_args()

    library_path = args.library.expanduser().resolve()
    if not library_path.is_file():
        parser.error(f"library not found: {library_path}")
    if args.once and args.start_radius_mm is None:
        parser.error("--once requires --start-radius-mm")

    document = json.loads(library_path.read_text(encoding="utf-8"))
    radii_mm = _inspection_radii_mm(document)
    if not radii_mm:
        parser.error("library contains no safe or unsafe radii")

    os.environ["CLOUDLABS_SKIP_RUNTIME_SYNC"] = "1"
    os.environ["CLOUDLAB_SIM_PROFILE"] = str(
        document.get("profile_id") or "optical_housings"
    )
    os.environ["CLOUDLAB_RADIAL_LIBRARY_FILE"] = str(library_path)
    _ensure_paths()

    import mujoco
    import numpy as np

    from simulation_edge.bootstrap import bootstrap_host
    from simulation_edge.host.ik import DampedLeastSquaresIK
    from simulation_edge.host.runtime import (
        ARM_DOF,
        MUJOCO_PLANNER_RADIAL,
        MuJoCoRobotRuntime,
        _rotation_distance_deg,
    )
    from simulation_edge.host.scene import build_scene_spec

    host, _ = bootstrap_host()
    scene = build_scene_spec(host.layout, host.catalog, host.current_state)
    runtime = MuJoCoRobotRuntime(
        scene,
        show_viewer=not args.no_viewer,
        realtime=False,
        planner_backend=MUJOCO_PLANNER_RADIAL,
    )
    library = runtime._load_radial_motion_library()
    carry_z_m = float(args.carry_z_mm) / 1000.0
    # Keep the component at world yaw zero while theta rotates the radial arm
    # posture around the robot base. This matches the production radial planner.
    target_rotation = runtime._target_rotation(0.0)

    def joints_for_pose(radius_mm: float, theta_deg: float) -> np.ndarray:
        radius_m = float(radius_mm) / 1000.0
        anchor = min(
            library.samples,
            key=lambda sample: abs(float(sample.radius_m) - radius_m),
        )
        anchor_pose = min(
            anchor.vertical_poses,
            key=lambda pose: abs(float(pose.z_m) - carry_z_m),
        )
        seed = np.asarray(anchor_pose.joints, dtype=float).copy()
        current_radius = float(anchor.radius_m)
        distance = radius_m - current_radius
        step_count = max(1, int(math.ceil(abs(distance) / 0.005)))
        for step_index in range(1, step_count + 1):
            step_radius = current_radius + distance * step_index / step_count
            target_position = np.array((step_radius, 0.0, carry_z_m))
            solved = runtime.ik.solve(
                runtime.model,
                target_position,
                target_rotation,
                seed,
                seed,
            )
            solved = runtime._nearest_equivalent_joints(solved, seed)
            DampedLeastSquaresIK._clip_joint_limits(runtime.model, solved)
            seed = np.asarray(solved, dtype=float).copy()
        theta_rad = math.radians(float(theta_deg))
        seed[0] += theta_rad
        seed[6] += theta_rad
        DampedLeastSquaresIK._clip_joint_limits(runtime.model, seed)
        return seed

    held_tag = str(args.held_tag or "").strip()
    if held_tag.lower() in {"none", "off", "false", "0"}:
        held_tag = ""
    if held_tag and held_tag not in runtime.scene.components:
        available = ", ".join(sorted(runtime.scene.components))
        runtime.close()
        parser.error(f"unknown held tag {held_tag!r}; available: {available}")

    def display(radius_mm: float, theta_deg: float) -> bool:
        try:
            joints = joints_for_pose(radius_mm, theta_deg)
        except Exception as exc:  # noqa: BLE001
            print(
                f"\nRADIUS {radius_mm:.1f} mm, THETA {theta_deg:.1f} deg: "
                f"IK FAILED: {exc}"
            )
            return False

        runtime.data.qpos[:ARM_DOF] = joints
        runtime.data.ctrl[:ARM_DOF] = joints
        runtime.current_joint_target = joints.copy()
        mujoco.mj_forward(runtime.model, runtime.data)
        if held_tag:
            _place_held_component(runtime, held_tag, yaw_deg=0.0)

        tcp_position = runtime.data.site("link_tcp").xpos.copy()
        tcp_rotation = runtime.data.site("link_tcp").xmat.reshape(3, 3).copy()
        theta_rad = math.radians(float(theta_deg))
        target_position = np.array(
            (
                radius_mm / 1000.0 * math.cos(theta_rad),
                radius_mm / 1000.0 * math.sin(theta_rad),
                carry_z_m,
            )
        )
        tcp_error_mm = float(np.linalg.norm(tcp_position - target_position)) * 1000.0
        rotation_error_deg = _rotation_distance_deg(tcp_rotation, target_rotation)
        frame = runtime._radial_frame_boundary_report(runtime.data)
        height = runtime._radial_height_zone_report(runtime.data)
        contacts = _robot_contacts(runtime)

        print("\n" + "=" * 76)
        print(
            f"theta={theta_deg:.1f} deg | requested radius={radius_mm:.1f} mm | "
            f"TCP world Z={args.carry_z_mm:.1f} mm"
        )
        print(
            "TCP actual [mm]: "
            + np.array2string(tcp_position * 1000.0, precision=2)
            + f" | position error={tcp_error_mm:.3f} mm | "
            + f"rotation error={rotation_error_deg:.4f} deg"
        )
        print(
            "Joints [deg]: "
            + np.array2string(np.degrees(joints), precision=2)
        )
        print(
            f"Frame envelope: {'PASS' if frame.ok else 'FAIL'} | "
            f"clearance={frame.min_clearance_m * 1000.0:.1f} mm | "
            f"body={frame.body} | side={frame.side} | "
            f"point_m={None if frame.point_m is None else np.round(frame.point_m, 4)}"
        )
        print(
            f"Component-height rule: {'PASS' if height.ok else 'FAIL'} | "
            f"clearance={height.min_clearance_m * 1000.0:.1f} mm | "
            f"body={height.body}"
        )
        if contacts:
            print("Actual MuJoCo robot contacts:")
            for row in contacts[:20]:
                print(f"  - {row}")
        else:
            print("Actual MuJoCo robot contacts: none")
        if not args.no_viewer and runtime.viewer_running():
            runtime._viewer_entered.sync()
        return True

    start_radius = (
        float(args.start_radius_mm)
        if args.start_radius_mm is not None
        else radii_mm[0]
    )
    index = min(
        range(len(radii_mm)),
        key=lambda item: abs(radii_mm[item] - start_radius),
    )
    theta_deg = float(args.start_theta_deg)

    print("\nSIMULATION-ONLY UNSAFE-POSE INSPECTOR")
    print(f"Library: {library_path}")
    print(f"Available grid: {radii_mm[0]:.1f} to {radii_mm[-1]:.1f} mm")
    print("This bypasses production safety refusal for static visualization only.")
    current_radius = start_radius
    display(current_radius, theta_deg)
    if args.once:
        runtime.close()
        return 0

    try:
        while runtime.viewer_running():
            command = input(
                "\nEnter=next radius | p=previous | radius | "
                "radius theta | t theta | q=quit > "
            ).strip()
            if command.lower() in {"q", "quit", "exit"}:
                break
            if command.lower() in {"p", "prev", "previous"}:
                index = max(0, index - 1)
                current_radius = radii_mm[index]
                display(current_radius, theta_deg)
                continue
            if not command:
                index = min(len(radii_mm) - 1, index + 1)
                current_radius = radii_mm[index]
                display(current_radius, theta_deg)
                continue
            fields = command.replace(",", " ").split()
            try:
                if len(fields) == 2 and fields[0].lower() in {"t", "theta"}:
                    theta_deg = float(fields[1])
                    display(current_radius, theta_deg)
                    continue
                if len(fields) == 2:
                    requested = float(fields[0])
                    theta_deg = float(fields[1])
                elif len(fields) == 1:
                    requested = float(fields[0])
                else:
                    raise ValueError
            except ValueError:
                print(
                    "Enter radius, 'radius theta', 't theta', Enter, p, or q."
                )
                continue
            index = min(
                range(len(radii_mm)),
                key=lambda item: abs(radii_mm[item] - requested),
            )
            current_radius = requested
            display(current_radius, theta_deg)
    finally:
        runtime.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
