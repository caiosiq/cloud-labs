#!/usr/bin/env python3
"""Interactive bench cheat sheet — real-backend first.

Same ``cloudlabs`` verbs as Twin. Defaults to ``real.default``.

Requires::

    pip install -e ./packages/cloudlabs
    # coordinator up (e.g. scripts/ops/run_cloud_labs_backend.ps1)
    # real edge attached / reachable

Run::

    python scripts/bench/cheat_sheet.py
    python scripts/bench/cheat_sheet.py --backend real.default --mirror tag_20 --camera tag_22
    python scripts/bench/cheat_sheet.py --backend mock.default
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# Allow running from repo without an editable install.
_REPO = Path(__file__).resolve().parents[2]
_SRC = _REPO / "packages" / "cloudlabs" / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from cloudlabs import configure_logging, connect

_LOG = logging.getLogger("bench.cheat_sheet")

DEFAULT_BACKEND = "real.default"
DEFAULT_MIRROR = "tag_20"
DEFAULT_CAMERA = "tag_22"
DEFAULT_KERNEL = "builtin.roi_centroid"


def _prompt(msg: str, default: Optional[str] = None) -> str:
    suffix = f" [{default}]" if default is not None else ""
    raw = input(f"{msg}{suffix}: ").strip()
    if not raw and default is not None:
        return default
    return raw


def _prompt_float(msg: str, default: Optional[float] = None) -> float:
    while True:
        raw = _prompt(msg, None if default is None else str(default))
        try:
            return float(raw)
        except ValueError:
            print("  need a number")


def _prompt_int(msg: str, default: int) -> int:
    while True:
        raw = _prompt(msg, str(default))
        try:
            return int(raw)
        except ValueError:
            print("  need an integer")


def _pp(obj: Any) -> None:
    print(json.dumps(obj, indent=2, default=str))


def _menu() -> None:
    print(
        """
======== cloudlabs bench cheat sheet ========
  1  list components
  2  lab state (presence / poses)
  3  describe component
  4  move pose (x / y / rotation)
  5  set motor angle
  6  capture camera_image (save .npy/.png + plot)
  7  list kernels
  8  probe kernel (EVAL_KERNEL)
  9  start live feed
 10  end live feed
 11  start teleop
 12  teleop goto (pose)
 13  end teleop
 14  sync_runtime / wait ready
 15  record nominal poses
 16  run_cobyla (simple motor + kernel match)
 17  raw /api/command (advanced)
  q  quit / release lease
=============================================
""".rstrip()
    )


def cmd_list_components(lab: Any) -> None:
    rows = lab.list_components(refresh=True)
    if not rows:
        print("(no components)")
        return
    for row in rows:
        tid = row.get("id") or row.get("tag_id") or "?"
        print(f"  {tid:12}  type={row.get('type')!r}")


def cmd_lab_state(lab: Any) -> None:
    state = lab.get_lab_state()
    print(f"system_status={state.get('system_status')!r}")
    comps = state.get("components") or {}
    for tag in sorted(comps):
        comp = comps[tag] or {}
        tun = ((comp.get("statecontrol") or {}).get("tunables") or {})
        pose = tun.get("nominal_pose")
        print(
            f"  {tag:12}  presence={tun.get('presence')!r}  "
            f"pose={pose}  motors={tun.get('nominal_motor_positions')}"
        )


def cmd_describe(lab: Any, default_tag: str) -> None:
    tag = _prompt("tag", default_tag)
    _pp(lab.describe_component(tag, refresh=True))


def cmd_move(lab: Any, default_tag: str) -> None:
    tag = _prompt("tag", default_tag)
    print("Enter axes to change (blank = leave unchanged).")
    xs = _prompt("x", "")
    ys = _prompt("y", "")
    rs = _prompt("rotation", "")
    kwargs: Dict[str, float] = {}
    if xs:
        kwargs["x"] = float(xs)
    if ys:
        kwargs["y"] = float(ys)
    if rs:
        kwargs["rotation"] = float(rs)
    if not kwargs:
        print("  nothing to move")
        return
    lab.components[tag].move(**kwargs).wait_until_idle()
    print("  moved + idle")


def cmd_motor(lab: Any, default_tag: str) -> None:
    tag = _prompt("tag", default_tag)
    motor_id = _prompt_int("motor_id", 1)
    angle = _prompt_float("angle_deg", 0.0)
    lab.components[tag].motor(motor_id, angle).wait_until_idle()
    print("  motor set + idle")


def cmd_capture(lab: Any, default_cam: str) -> None:
    tag = _prompt("camera tag", default_cam)
    field = _prompt("field", "camera_image")
    show = _prompt("show matplotlib window? (y/n)", "y").lower() not in ("n", "no", "0")
    capture_and_show(lab, tag, field=field, show=show)


def capture_and_show(
    lab: Any,
    tag: str,
    *,
    field: str = "camera_image",
    show: bool = True,
    out_dir: Optional[Path] = None,
) -> Path:
    """RECORD + resolve tensor, save ``.npy`` (+ ``.png``), optionally plot.

    Camera tensors are HxWx3 uint8 **BGR** (OpenCV layout). The PNG / plot
    convert to RGB for human viewing.
    """
    import numpy as np

    print(f"  capturing + resolving {tag}.{field} …")
    tensor = lab.measurable(tag, field).resolve(record=True)
    arr = np.asarray(tensor.data)
    print(f"  tensor dtype={tensor.dtype} shape={tuple(arr.shape)} domain={tensor.domain}")

    dest = out_dir or (_REPO / "scripts" / "bench" / "captures")
    dest.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = dest / f"{tag}_{field}_{stamp}"
    npy_path = Path(str(stem) + ".npy")
    png_path = Path(str(stem) + ".png")
    meta_path = Path(str(stem) + ".json")

    np.save(npy_path, arr)
    meta = {
        "tag_id": tag,
        "field": field,
        "dtype": str(tensor.dtype),
        "shape": list(arr.shape),
        "domain": str(tensor.domain),
        "layout": "bgr_hwc_uint8" if arr.ndim == 3 and arr.shape[-1] == 3 else "array",
        "npy": str(npy_path),
        "png": str(png_path),
    }
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"  saved {npy_path}")
    print(f"  saved {meta_path}")

    rgb = _bgr_to_rgb(arr)
    try:
        import matplotlib.pyplot as plt
    except ImportError as exc:
        print(f"  matplotlib not installed ({exc}); skip plot / png")
        print("  pip install matplotlib")
        return npy_path

    try:
        plt.imsave(png_path, rgb)
        print(f"  saved {png_path}")
    except Exception as exc:  # noqa: BLE001
        print(f"  png save failed: {exc}")

    if show:
        fig, ax = plt.subplots(figsize=(8, 6))
        if rgb.ndim == 2:
            ax.imshow(rgb, cmap="gray")
        else:
            ax.imshow(rgb)
        ax.set_title(f"{tag}.{field}  {arr.shape}")
        ax.axis("off")
        fig.tight_layout()
        print("  close the plot window to return to the menu")
        plt.show()
        plt.close(fig)

    return npy_path


def _bgr_to_rgb(arr: Any) -> Any:
    import numpy as np

    a = np.asarray(arr)
    if a.ndim == 3 and a.shape[-1] == 3:
        return a[:, :, ::-1].copy()
    return a


def cmd_list_kernels(lab: Any) -> None:
    rows = lab.list_kernels()
    if not rows:
        print("(no kernels — is edge catalog up?)")
        return
    for row in rows:
        kid = row.get("id") or row.get("kernel_id")
        present = row.get("artifact_present")
        print(f"  {kid:40}  artifact_present={present}")


def cmd_probe(lab: Any, default_cam: str, default_kernel: str) -> None:
    tag = _prompt("camera tag", default_cam)
    field = _prompt("field", "camera_image")
    kid = _prompt("kernel_id", default_kernel)
    out = lab.probe_kernel(tag, field, kernel_id=kid)
    print(f"  result={out!r}")


def cmd_live_start(lab: Any, default_cam: str) -> None:
    tag = _prompt("camera tag", default_cam)
    _pp(lab.start_live_feed(tag, channel="stream"))


def cmd_live_end(lab: Any, default_cam: str) -> None:
    tag = _prompt("camera tag", default_cam)
    _pp(lab.end_live_feed(tag, channel="all"))


def cmd_teleop_start(lab: Any, default_tag: str) -> None:
    tag = _prompt("tag", default_tag)
    _pp(lab.start_teleop(tag))


def cmd_teleop_goto(lab: Any, default_tag: str) -> None:
    tag = _prompt("tag", default_tag)
    x = _prompt_float("target x", 0.0)
    y = _prompt_float("target y", 0.0)
    r = _prompt_float("target rotation", 0.0)
    _pp(
        lab.teleop_goto(
            tag,
            target_pose={"x": x, "y": y, "rotation": r},
        )
    )


def cmd_teleop_end(lab: Any, default_tag: str) -> None:
    tag = _prompt("tag", default_tag)
    _pp(lab.end_teleop(tag))


def cmd_sync(lab: Any) -> None:
    try:
        _pp(lab.sync_runtime())
    except Exception as exc:  # noqa: BLE001
        print(f"  sync_runtime: {exc}")
    lab.wait_until_lab_ready()
    print("  lab ready")


def cmd_record_poses(lab: Any, default_tag: str) -> None:
    raw = _prompt("tags (comma-separated)", default_tag)
    tags = [t.strip() for t in raw.split(",") if t.strip()]
    _pp(lab.record_nominal_poses(tags))


def cmd_cobyla(
    lab: Any,
    default_mirror: str,
    default_cam: str,
    default_kernel: str,
) -> None:
    mirror = _prompt("mirror tag (variable)", default_mirror)
    motor_id = _prompt_int("motor_id", 1)
    lo = _prompt_float("bounds min (delta deg)", -2.0)
    hi = _prompt_float("bounds max (delta deg)", 2.0)
    cam = _prompt("camera tag", default_cam)
    kid = _prompt("kernel_id", default_kernel)
    max_evals = _prompt_int("max_evals", 25)
    # Centroid match: ask for target pixel; empty → skip typed target and use
    # scalar match sugar only when kernel is scalar.
    tx = _prompt("target cx (blank = 0.0 for scalar match)", "0.0")
    ty = _prompt("target cy (blank to use scalar target only)", "")
    path = f"tunables.nominal_motor_positions.{motor_id}"
    var = lab.variable(mirror, path, bounds=(lo, hi), delta=True)
    if ty.strip():
        match = lab.kernel_match(
            cam,
            "camera_image",
            kernel_id=kid,
            target=[float(tx), float(ty)],
            feature_index=[0, 1],
        )
    else:
        match = lab.kernel_match(
            cam,
            "camera_image",
            kernel_id=kid,
            target=float(tx),
        )
    print("  submitting run_cobyla (releases imperative lease while job runs)…")
    result = lab.run_cobyla(
        variables=[var],
        match_kernel=match,
        max_evals=max_evals,
    )
    _pp(result)
    # Re-acquire so the menu can keep going.
    try:
        lab.acquire_lease()
        print(f"  re-acquired lease {lab.lease_id}")
    except Exception as exc:  # noqa: BLE001
        print(f"  could not re-acquire lease: {exc}")


def cmd_raw(lab: Any, default_tag: str) -> None:
    print(
        "Advanced: POST /api/command. Examples of actions Twin can do that\n"
        "lack dedicated SDK helpers yet: STORE_COMPONENT, PLACE_FROM_STORAGE,\n"
        "PICK_COMPONENT, HOVER, PLACE_FROM_HOVER, SET_EXPOSURE, …"
    )
    action = _prompt("action", "SET_EXPOSURE")
    tag = _prompt("target_id", default_tag)
    params_raw = _prompt('parameters JSON (e.g. {"exposure_time_ms": 50})', "{}")
    try:
        params = json.loads(params_raw) if params_raw else {}
    except json.JSONDecodeError as exc:
        print(f"  bad JSON: {exc}")
        return
    if not isinstance(params, dict):
        print("  parameters must be a JSON object")
        return
    body = {"action": action, "target_id": tag, "parameters": params}
    _pp(lab._post_command(body))  # noqa: SLF001 — intentional escape hatch


def run_menu(
    lab: Any,
    *,
    mirror: str,
    camera: str,
    kernel: str,
) -> int:
    print(f"lease_id={lab.lease_id}  backend={lab.backend_id}  holder={lab.holder}")
    print(f"defaults: mirror={mirror}  camera={camera}  kernel={kernel}")
    while True:
        _menu()
        choice = input("choice> ").strip().lower()
        try:
            if choice in ("q", "quit", "exit"):
                return 0
            if choice == "1":
                cmd_list_components(lab)
            elif choice == "2":
                cmd_lab_state(lab)
            elif choice == "3":
                cmd_describe(lab, mirror)
            elif choice == "4":
                cmd_move(lab, mirror)
            elif choice == "5":
                cmd_motor(lab, mirror)
            elif choice == "6":
                cmd_capture(lab, camera)
            elif choice == "7":
                cmd_list_kernels(lab)
            elif choice == "8":
                cmd_probe(lab, camera, kernel)
            elif choice == "9":
                cmd_live_start(lab, camera)
            elif choice == "10":
                cmd_live_end(lab, camera)
            elif choice == "11":
                cmd_teleop_start(lab, mirror)
            elif choice == "12":
                cmd_teleop_goto(lab, mirror)
            elif choice == "13":
                cmd_teleop_end(lab, mirror)
            elif choice == "14":
                cmd_sync(lab)
            elif choice == "15":
                cmd_record_poses(lab, mirror)
            elif choice == "16":
                cmd_cobyla(lab, mirror, camera, kernel)
            elif choice == "17":
                cmd_raw(lab, mirror)
            else:
                print("  unknown choice")
        except KeyboardInterrupt:
            print("\n  (interrupted — back to menu)")
        except Exception as exc:  # noqa: BLE001
            _LOG.exception("action failed")
            print(f"  ERROR: {exc}")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Interactive cloudlabs cheat sheet (defaults to real.default)",
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--backend",
        default=DEFAULT_BACKEND,
        help=f"backend_id (default: {DEFAULT_BACKEND})",
    )
    parser.add_argument("--mirror", default=DEFAULT_MIRROR, help="default motion tag")
    parser.add_argument("--camera", default=DEFAULT_CAMERA, help="default camera tag")
    parser.add_argument(
        "--kernel",
        default=DEFAULT_KERNEL,
        help="default kernel_id for probe / cobyla",
    )
    parser.add_argument(
        "--once",
        choices=[
            "list",
            "state",
            "kernels",
            "capture",
            "probe",
        ],
        default=None,
        help="run one non-interactive action then exit",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="HTTP timeout seconds (camera RECORD can exceed 30s)",
    )
    args = parser.parse_args(argv)

    configure_logging()
    base_url = args.base_url.rstrip("/")
    backend_id = (args.backend or DEFAULT_BACKEND).strip()
    _LOG.info("connecting backend=%s base_url=%s", backend_id, base_url)

    with connect(
        backend_id,
        base_url=base_url,
        verbose=True,
        timeout_s=float(args.timeout),
    ) as lab:
        if args.once == "list":
            cmd_list_components(lab)
            return 0
        if args.once == "state":
            cmd_lab_state(lab)
            return 0
        if args.once == "kernels":
            cmd_list_kernels(lab)
            return 0
        if args.once == "capture":
            capture_and_show(lab, args.camera, field="camera_image", show=True)
            return 0
        if args.once == "probe":
            print(lab.probe_kernel(args.camera, "camera_image", kernel_id=args.kernel))
            return 0
        return run_menu(
            lab,
            mirror=args.mirror,
            camera=args.camera,
            kernel=args.kernel,
        )


if __name__ == "__main__":
    sys.exit(main())
