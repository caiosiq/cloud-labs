#!/usr/bin/env python3
"""Smoke-test the Windows MuJoCo process client against the WSL MoveIt sidecar."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


CLOUD_LABS_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = CLOUD_LABS_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from lab_communicator.mujoco.client import MuJoCoProcessClient  # noqa: E402
from lab_communicator.mujoco.runtime import MUJOCO_PLANNER_MOVEIT  # noqa: E402
from lab_communicator.mujoco.scene import build_scene_spec  # noqa: E402
from lab_model.domain.storage_region import configure_from_layout_document  # noqa: E402


LAYOUT = {
    "lab_bounds_mm": {
        "x_min": -500,
        "x_max": 500,
        "y_min": -500,
        "y_max": 500,
    },
    "danger_zone": {"radius_mm": 90, "padding_mm": 5},
    "storage": {
        "rule": "negative_xy",
        "grid_nx": 4,
        "grid_ny": 4,
        "extent_from_origin_mm": {"width_mm": 300, "height_mm": 380},
    },
}


def _catalog_row(tag_id: str, component_type: str = "OPTICAL_FILTER") -> dict:
    return {
        "tag_id": tag_id,
        "type": component_type,
        "size": {"width": 62, "height": 62},
        "height_mm": 60,
        "capabilities": {
            "statecontrol": {
                "tunables": {"nominal_pose": {"widget": "TablePose"}},
                "measurables": {},
            },
            "telemetry": {},
            "primitives": ["MOVE_COMPONENT"],
        },
    }


def _component(x: float, y: float, rotation: float = 0.0) -> dict:
    return {
        "type": "OPTICAL_FILTER",
        "statecontrol": {
            "tunables": {
                "presence": "breadboard",
                "nominal_pose": {"x": x, "y": y, "rotation": rotation},
                "storage": {"in_storage": False, "slot": None},
                "placement": {"mode": "MANUAL"},
            },
            "measurables": {
                "pose": {"x": x, "y": y, "rotation": rotation},
            },
        },
        "telemetry": {
            "teleop": {"active": False, "ready": False},
            "live_feed": {},
        },
    }


def _scene(profile_id: str):
    configure_from_layout_document(LAYOUT)
    state = {
        "system_status": "IDLE",
        "components": {
            "tag_pick": _component(300, -180),
            "tag_other": _component(300, 100),
        },
        "holding": {},
    }
    rows = [
        _catalog_row("tag_pick"),
        _catalog_row("tag_other", "OPTICAL_MIRROR"),
    ]
    return build_scene_spec(LAYOUT, rows, state, profile_id=profile_id)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default="optical_housings")
    parser.add_argument("--tag", default="tag_pick")
    parser.add_argument("--x-mm", type=float, default=320.0)
    parser.add_argument("--y-mm", type=float, default=-60.0)
    parser.add_argument("--rotation-deg", type=float, default=15.0)
    args = parser.parse_args()

    client = MuJoCoProcessClient(
        _scene(args.profile),
        show_viewer=False,
        realtime=False,
        planner_backend=MUJOCO_PLANNER_MOVEIT,
    )
    try:
        client.start(timeout_s=30)
        result = client.move_component(
            args.tag,
            target_x_mm=args.x_mm,
            target_y_mm=args.y_mm,
            target_rotation_deg=args.rotation_deg,
            timeout_s=180,
        )
        print(
            json.dumps(
                {
                    "ok": True,
                    "result": result,
                    "status": client.status(),
                },
                indent=2,
            )
        )
    finally:
        client.stop()


if __name__ == "__main__":
    main()
