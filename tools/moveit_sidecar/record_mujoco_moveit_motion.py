#!/usr/bin/env python3
"""Record a MuJoCo MoveIt component move as an offscreen BMP montage.

This is intentionally dependency-light: MuJoCo provides RGB frames, and this
script writes BMP files directly so the visual debug loop works inside the
project venv without Pillow/imageio/OpenCV.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import struct
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import mujoco
import numpy as np


CLOUD_LABS_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = CLOUD_LABS_ROOT / "backend"
WORKSPACE_ROOT = CLOUD_LABS_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def _prepare_lab_view(source: Path | None) -> Path:
    if source is None:
        source = BACKEND_ROOT / "lab_communicator" / "mock" / "lab_view"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = CLOUD_LABS_ROOT / ".tmp" / f"mujoco_moveit_record_lab_view_{stamp}"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target)
    return target


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_bmp(path: Path, rgb: np.ndarray) -> None:
    if rgb.dtype != np.uint8 or rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError("BMP writer expects uint8 RGB image")
    height, width, _ = rgb.shape
    row_stride = ((width * 3 + 3) // 4) * 4
    image_size = row_stride * height
    file_size = 14 + 40 + image_size
    padding = b"\x00" * (row_stride - width * 3)
    with path.open("wb") as f:
        f.write(b"BM")
        f.write(struct.pack("<IHHI", file_size, 0, 0, 54))
        f.write(
            struct.pack(
                "<IIIHHIIIIII",
                40,
                width,
                height,
                1,
                24,
                0,
                image_size,
                2835,
                2835,
                0,
                0,
            )
        )
        for row in rgb[::-1]:
            f.write(row[:, ::-1].tobytes())
            f.write(padding)


def _montage(frames: list[np.ndarray], *, columns: int = 5, gap: int = 6) -> np.ndarray:
    if not frames:
        raise ValueError("no frames captured")
    height, width, _ = frames[0].shape
    rows = int(np.ceil(len(frames) / columns))
    canvas = np.full(
        (
            rows * height + (rows - 1) * gap,
            columns * width + (columns - 1) * gap,
            3,
        ),
        245,
        dtype=np.uint8,
    )
    for index, frame in enumerate(frames):
        row = index // columns
        col = index % columns
        y0 = row * (height + gap)
        x0 = col * (width + gap)
        canvas[y0 : y0 + height, x0 : x0 + width] = frame
    return canvas


def _build_scene(profile_id: str, lab_view_source: Path | None):
    lab_view = _prepare_lab_view(lab_view_source)
    os.environ["LAB_VIEW_PATH"] = str(lab_view)
    os.environ["CLOUDLAB_SIM_PROFILE"] = profile_id
    os.environ["CLOUDLAB_MOVEIT_URL"] = "http://127.0.0.1:8765"
    os.environ["CLOUDLAB_MUJOCO_VIEWER"] = "0"
    os.environ["CLOUDLAB_MUJOCO_REALTIME"] = "0"
    os.environ["ROBOTIC_TWIN_WORKSPACE"] = str(WORKSPACE_ROOT)

    from lab_communicator.mujoco.communicator import _simulator_catalog_row
    from lab_communicator.mujoco.scene import build_scene_spec
    from lab_communicator.shared.lab_view_config import (
        bootstrap_lab_view,
        get_lab_view_paths,
        load_layout_document,
    )
    from lab_model.catalog.bundle import merged_catalog_rows

    bootstrap_lab_view(str(CLOUD_LABS_ROOT))
    paths = get_lab_view_paths()
    state = _read_json(Path(paths.lab_state_json))
    layout = load_layout_document()
    rows = [_simulator_catalog_row(row) for row in merged_catalog_rows()]
    return build_scene_spec(layout, rows, state, profile_id=profile_id), lab_view


def record_motion(
    *,
    tag_id: str,
    target_x_mm: float,
    target_y_mm: float,
    target_rotation_deg: float,
    profile_id: str,
    lab_view_source: Path | None,
    output_path: Path,
    frame_interval_s: float,
) -> dict[str, Any]:
    from lab_communicator.mujoco.runtime import (
        MUJOCO_PLANNER_MOVEIT,
        MuJoCoRobotRuntime,
    )

    scene, lab_view = _build_scene(profile_id, lab_view_source)
    runtime = MuJoCoRobotRuntime(
        scene,
        show_viewer=False,
        realtime=False,
        planner_backend=MUJOCO_PLANNER_MOVEIT,
    )
    renderer = mujoco.Renderer(runtime.model, height=240, width=360)
    camera = mujoco.MjvCamera()
    mujoco.mjv_defaultFreeCamera(runtime.model, camera)
    bounds = scene.lab_bounds_mm
    camera.lookat[:] = (
        (float(bounds["x_min"]) + float(bounds["x_max"])) / 2000.0,
        (float(bounds["y_min"]) + float(bounds["y_max"])) / 2000.0,
        0.24,
    )
    camera.distance = 1.35
    camera.azimuth = -125.0
    camera.elevation = -30.0
    frames: list[np.ndarray] = []
    frame_times: list[float] = []
    last_frame_time = -1.0e9
    original_step = runtime._step

    def capture() -> None:
        nonlocal last_frame_time
        if runtime.data.time - last_frame_time < frame_interval_s and frames:
            return
        mujoco.mj_forward(runtime.model, runtime.data)
        renderer.update_scene(runtime.data, camera=camera)
        frames.append(renderer.render().copy())
        frame_times.append(float(runtime.data.time))
        last_frame_time = float(runtime.data.time)

    def recording_step() -> None:
        original_step()
        capture()

    runtime._step = recording_step  # type: ignore[method-assign]
    capture()
    result: dict[str, Any] | None = None
    error: str | None = None
    try:
        move_result = runtime.pick_and_place(
            tag_id,
            target_x_mm=target_x_mm,
            target_y_mm=target_y_mm,
            target_rotation_deg=target_rotation_deg,
        )
        capture()
        result = move_result.as_dict()
    except Exception as exc:  # noqa: BLE001
        error = str(exc)
        capture()
    finally:
        renderer.close()
        runtime.close()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    montage = _montage(frames)
    _write_bmp(output_path, montage)
    metadata = {
        "ok": error is None,
        "error": error,
        "result": result,
        "tag_id": tag_id,
        "target": {
            "x_mm": target_x_mm,
            "y_mm": target_y_mm,
            "rotation_deg": target_rotation_deg,
        },
        "profile_id": profile_id,
        "lab_view_path": str(lab_view),
        "output_path": str(output_path),
        "frame_count": len(frames),
        "frame_times_s": frame_times,
        "diagnostic_log_path": runtime.diagnostic_log_path(),
        "stage_trace": list(runtime.stage_trace),
    }
    metadata_path = output_path.with_suffix(".json")
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    if error:
        raise RuntimeError(
            f"recorded failed motion to {output_path}; metadata={metadata_path}; error={error}"
        )
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", default="tag_9")
    parser.add_argument("--x-mm", type=float, default=163.4)
    parser.add_argument("--y-mm", type=float, default=-105.1)
    parser.add_argument("--rotation-deg", type=float, default=0.0)
    parser.add_argument("--profile", default="optical_housings")
    parser.add_argument("--lab-view", type=Path, default=None)
    parser.add_argument(
        "--out",
        type=Path,
        default=CLOUD_LABS_ROOT / ".tmp" / "mujoco_moveit_motion_montage.bmp",
    )
    parser.add_argument("--frame-interval-s", type=float, default=0.18)
    args = parser.parse_args()

    metadata = record_motion(
        tag_id=args.tag,
        target_x_mm=args.x_mm,
        target_y_mm=args.y_mm,
        target_rotation_deg=args.rotation_deg,
        profile_id=args.profile,
        lab_view_source=args.lab_view,
        output_path=args.out,
        frame_interval_s=args.frame_interval_s,
    )
    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    main()
