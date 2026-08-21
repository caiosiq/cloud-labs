#!/usr/bin/env python3
"""Interactive camera measurables + kernels smoke (default ``tag_22``, real).

Tests the sense path Twin uses for OPTIMIZE:

  RECORD / resolve camera_image → save + plot → list kernels → EVAL_KERNEL

Requires::

    # coordinator up; real edge reachable; matplotlib for plots
    python scripts/bench/camera_kernels.py
    python scripts/bench/camera_kernels.py --camera tag_22 --once smoke
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import quote

_REPO = Path(__file__).resolve().parents[2]
_SRC = _REPO / "packages" / "cloudlabs" / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from cloudlabs import configure_logging, connect

_LOG = logging.getLogger("bench.camera_kernels")

DEFAULT_BACKEND = "real.default"
DEFAULT_CAMERA = "tag_22"
DEFAULT_KERNEL = "builtin.roi_centroid"
DEFAULT_FIELD = "camera_image"

# Common edge kernels to try in the smoke / batch probe.
PROBE_KERNELS = (
    "builtin.roi_centroid",
    "builtin.beam_power",
    "builtin.gaussian_beam_fit",
    "builtin.beam_shift",
    "builtin.beam_com",
    "demo.image_mean_score",
    "demo.roi_mean_score",
)


def _prompt(msg: str, default: Optional[str] = None) -> str:
    suffix = f" [{default}]" if default is not None else ""
    raw = input(f"{msg}{suffix}: ").strip()
    if not raw and default is not None:
        return default
    return raw


def _pp(obj: Any) -> None:
    print(json.dumps(obj, indent=2, default=str))


def _bgr_to_rgb(arr: Any) -> Any:
    import numpy as np

    a = np.asarray(arr)
    if a.ndim == 3 and a.shape[-1] == 3:
        return a[:, :, ::-1].copy()
    return a


def _captures_dir() -> Path:
    dest = _REPO / "scripts" / "bench" / "captures"
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def capture_and_show(
    lab: Any,
    tag: str,
    *,
    field: str = DEFAULT_FIELD,
    show: bool = True,
    overlay_xy: Optional[Sequence[float]] = None,
    title_extra: str = "",
) -> Path:
    """RECORD + resolve tensor, save ``.npy`` / ``.png`` / ``.json``, plot."""
    import numpy as np

    print(f"  capturing + resolving {tag}.{field} …")
    # record=true on the coordinator; JPEG stays lazy on the wire and is
    # decoded on this client (see MeasurableHandle.resolve).
    tensor = lab.measurable(tag, field).resolve(record=True)
    arr = np.asarray(tensor.data)
    print(f"  tensor dtype={tensor.dtype} shape={tuple(arr.shape)} domain={tensor.domain}")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = _captures_dir() / f"{tag}_{field}_{stamp}"
    npy_path = Path(str(stem) + ".npy")
    png_path = Path(str(stem) + ".png")
    meta_path = Path(str(stem) + ".json")

    np.save(npy_path, arr)
    meta: Dict[str, Any] = {
        "tag_id": tag,
        "field": field,
        "dtype": str(tensor.dtype),
        "shape": list(arr.shape),
        "domain": str(tensor.domain),
        "layout": "bgr_hwc_uint8" if arr.ndim == 3 and arr.shape[-1] == 3 else "array",
        "npy": str(npy_path),
        "png": str(png_path),
    }
    if overlay_xy is not None and len(overlay_xy) >= 2:
        meta["overlay_xy"] = [float(overlay_xy[0]), float(overlay_xy[1])]
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
        if overlay_xy is not None and len(overlay_xy) >= 2:
            ax.plot(
                float(overlay_xy[0]),
                float(overlay_xy[1]),
                "r+",
                markersize=14,
                markeredgewidth=2,
                label="kernel xy",
            )
            ax.legend(loc="upper right")
        title = f"{tag}.{field}  {arr.shape}"
        if title_extra:
            title = f"{title}  |  {title_extra}"
        ax.set_title(title)
        ax.axis("off")
        fig.tight_layout()
        print("  close the plot window to continue")
        plt.show()
        plt.close(fig)

    return npy_path


def cmd_status(lab: Any, tag: str) -> None:
    state = lab.get_lab_state()
    print(f"system_status={state.get('system_status')!r}")
    comps = state.get("components") or {}
    entry = comps.get(tag) if isinstance(comps, dict) else None
    if not isinstance(entry, dict):
        print(f"  {tag} not in lab_state.components")
        return
    tun = ((entry.get("statecontrol") or {}).get("tunables") or {})
    meas = ((entry.get("statecontrol") or {}).get("measurables") or {})
    tel = entry.get("telemetry") or {}
    lf = (tel.get("live_feed") or {}).get("stream") or {}
    print(f"  {tag}")
    print(f"    presence={tun.get('presence')!r}")
    print(f"    measurable keys={sorted(meas.keys())}")
    print(f"    live_feed.stream live={lf.get('live')} connected={lf.get('connected')}")
    try:
        desc = lab.describe_component(tag, refresh=True)
        caps = desc.get("capabilities") or desc.get("primitives") or {}
        print(f"    describe keys={sorted(desc.keys()) if isinstance(desc, dict) else type(desc)}")
        if isinstance(desc, dict) and desc.get("measurables"):
            print(f"    declared measurables={[m.get('field') for m in desc['measurables']]}")
        _ = caps
    except Exception as exc:  # noqa: BLE001
        print(f"    describe_component: {exc}")


def cmd_list_kernels(lab: Any) -> None:
    rows = lab.list_kernels()
    if not rows:
        print("(no kernels — is edge catalog up / backend selected?)")
        return
    for row in rows:
        kid = row.get("id") or row.get("kernel_id")
        present = row.get("artifact_present")
        print(f"  {str(kid):40}  artifact_present={present}")


def cmd_capture(lab: Any, tag: str) -> None:
    field = _prompt("field", DEFAULT_FIELD)
    show = _prompt("show matplotlib window? (y/n)", "y").lower() not in ("n", "no", "0")
    capture_and_show(lab, tag, field=field, show=show)


def cmd_probe(lab: Any, tag: str, default_kernel: str) -> None:
    field = _prompt("field", DEFAULT_FIELD)
    kid = _prompt("kernel_id", default_kernel)
    print(f"  probe_kernel {tag}.{field} kernel={kid} …")
    out = lab.probe_kernel(tag, field, kernel_id=kid)
    print(f"  result={out!r}")
    if _prompt("capture+plot with overlay (if xy features)? (y/n)", "y").lower() in (
        "n",
        "no",
        "0",
    ):
        return
    overlay = None
    if isinstance(out, (list, tuple)) and len(out) >= 2:
        try:
            overlay = [float(out[0]), float(out[1])]
        except (TypeError, ValueError):
            overlay = None
    capture_and_show(
        lab,
        tag,
        field=field,
        show=True,
        overlay_xy=overlay,
        title_extra=f"{kid} → {out!r}",
    )


def cmd_probe_batch(lab: Any, tag: str) -> None:
    field = _prompt("field", DEFAULT_FIELD)
    available = {
        str(r.get("id") or r.get("kernel_id"))
        for r in (lab.list_kernels() or [])
        if (r.get("id") or r.get("kernel_id"))
    }
    for kid in PROBE_KERNELS:
        if available and kid not in available:
            print(f"  skip {kid} (not in edge catalog)")
            continue
        try:
            out = lab.probe_kernel(tag, field, kernel_id=kid)
            print(f"  {kid:40}  → {out!r}")
        except Exception as exc:  # noqa: BLE001
            print(f"  {kid:40}  FAIL: {exc}")


def _preview_jpeg_url(lab: Any, tag: str) -> str:
    """Coordinator JPEG preview URL (browser-safe ``?backend_id=`` query)."""
    base = str(getattr(lab, "base_url", None) or "http://127.0.0.1:8000").rstrip("/")
    bid = quote(str(getattr(lab, "backend_id", "") or ""), safe="")
    tid = quote(str(tag).strip(), safe="")
    return f"{base}/api/components/{tid}/telemetry/preview?backend_id={bid}"


def _write_live_preview_html(lab: Any, tag: str, *, interval_ms: int = 250) -> Path:
    """Auto-refresh HTML page that polls the coordinator preview JPEG."""
    jpeg_url = _preview_jpeg_url(lab, tag)
    dest = _captures_dir() / f"live_preview_{tag}.html"
    # Cache-bust so the browser actually reloads each tick.
    body = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <title>Live feed — {tag}</title>
  <style>
    html, body {{ margin: 0; background: #111; color: #ddd; font: 14px/1.4 system-ui, sans-serif; }}
    header {{ padding: 10px 14px; background: #1a1a1a; border-bottom: 1px solid #333; }}
    code {{ color: #9cf; }}
    #wrap {{ padding: 12px; text-align: center; }}
    img {{ max-width: 100%; height: auto; background: #000; }}
    #status {{ margin-top: 8px; color: #888; }}
  </style>
</head>
<body>
  <header>
    Live feed preview for <strong>{tag}</strong>
    · backend <code>{getattr(lab, "backend_id", "")}</code>
    · polls every {interval_ms} ms
  </header>
  <div id="wrap">
    <img id="frame" alt="waiting for JPEG…"/>
    <div id="status">connecting…</div>
  </div>
  <script>
    const base = {jpeg_url!r};
    const img = document.getElementById('frame');
    const status = document.getElementById('status');
    let ok = 0, fail = 0;
    function tick() {{
      const url = base + (base.includes('?') ? '&' : '?') + '_=' + Date.now();
      const probe = new Image();
      probe.onload = () => {{
        img.src = url;
        ok += 1;
        status.textContent = 'frames ok=' + ok + ' fail=' + fail + ' · ' + new Date().toLocaleTimeString();
      }};
      probe.onerror = () => {{
        fail += 1;
        status.textContent = 'no JPEG (204/error) — is live feed on? fail=' + fail + ' ok=' + ok;
      }};
      probe.src = url;
    }}
    tick();
    setInterval(tick, {int(interval_ms)});
  </script>
</body>
</html>
"""
    dest.write_text(body, encoding="utf-8")
    return dest


def _browser_candidates() -> List[Tuple[str, List[str]]]:
    """Prefer Microsoft Edge, then Chrome, then PATH shims."""
    found: List[Tuple[str, List[str]]] = []
    seen = set()

    def add(name: str, *paths: str) -> None:
        for p in paths:
            if not p or p in seen:
                continue
            if os.path.isfile(p) or shutil.which(p):
                exe = shutil.which(p) or p
                seen.add(p)
                seen.add(exe)
                found.append((name, [exe]))

    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    local = os.environ.get("LOCALAPPDATA", "")
    add(
        "Microsoft Edge",
        shutil.which("msedge") or "",
        os.path.join(pf, "Microsoft", "Edge", "Application", "msedge.exe"),
        os.path.join(pf86, "Microsoft", "Edge", "Application", "msedge.exe"),
        os.path.join(local, "Microsoft", "Edge", "Application", "msedge.exe") if local else "",
    )
    add(
        "Google Chrome",
        shutil.which("chrome") or "",
        os.path.join(pf, "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(pf86, "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(local, "Google", "Chrome", "Application", "chrome.exe") if local else "",
    )
    return found


def open_live_preview_browser(lab: Any, tag: str) -> Path:
    """Write auto-refresh preview HTML and open it in Edge, Chrome, or default browser."""
    html_path = _write_live_preview_html(lab, tag)
    uri = html_path.resolve().as_uri()
    jpeg = _preview_jpeg_url(lab, tag)
    print(f"  preview page: {html_path}")
    print(f"  jpeg poll:    {jpeg}")

    for name, argv in _browser_candidates():
        try:
            subprocess.Popen([*argv, uri], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print(f"  opened in {name}")
            return html_path
        except OSError as exc:
            print(f"  {name} failed: {exc}")

    opened = webbrowser.open(uri)
    print(f"  opened via default browser (ok={opened})")
    return html_path


def cmd_live_start(lab: Any, tag: str) -> None:
    _pp(lab.start_live_feed(tag, channel="stream"))
    open_live_preview_browser(lab, tag)


def cmd_live_end(lab: Any, tag: str) -> None:
    _pp(lab.end_live_feed(tag, channel="all"))


def cmd_live_open(lab: Any, tag: str) -> None:
    """Re-open the auto-refresh preview page (feed must already be started)."""
    open_live_preview_browser(lab, tag)


def cmd_smoke(lab: Any, tag: str, default_kernel: str) -> None:
    """Non-interactive-ish path: status → kernels → capture → probe centroid → plot."""
    print(f"\nSmoke path on {tag}\n")
    cmd_status(lab, tag)
    print("\n— kernels —")
    cmd_list_kernels(lab)
    print("\n— capture + plot —")
    capture_and_show(lab, tag, field=DEFAULT_FIELD, show=True)
    print(f"\n— probe {default_kernel} —")
    try:
        out = lab.probe_kernel(tag, DEFAULT_FIELD, kernel_id=default_kernel)
        print(f"  result={out!r}")
    except Exception as exc:  # noqa: BLE001
        print(f"  probe FAIL: {exc}")
        return
    overlay = None
    if isinstance(out, (list, tuple)) and len(out) >= 2:
        try:
            overlay = [float(out[0]), float(out[1])]
        except (TypeError, ValueError):
            overlay = None
    print("\n— recapture with kernel overlay —")
    capture_and_show(
        lab,
        tag,
        field=DEFAULT_FIELD,
        show=True,
        overlay_xy=overlay,
        title_extra=f"{default_kernel} → {out!r}",
    )
    print("\n— batch probe (catalog builtins) —")
    # Reuse batch without prompts
    available = {
        str(r.get("id") or r.get("kernel_id"))
        for r in (lab.list_kernels() or [])
        if (r.get("id") or r.get("kernel_id"))
    }
    for kid in PROBE_KERNELS:
        if available and kid not in available:
            print(f"  skip {kid}")
            continue
        try:
            print(f"  {kid:40}  → {lab.probe_kernel(tag, DEFAULT_FIELD, kernel_id=kid)!r}")
        except Exception as exc:  # noqa: BLE001
            print(f"  {kid:40}  FAIL: {exc}")
    print("\n  smoke done")


def _menu(tag: str) -> None:
    print(
        f"""
======== camera + kernels  ({tag}) ========
  1  status / declared measurables
  2  list edge kernels
  3  capture camera_image (save + plot)
  4  probe one kernel (optional overlay plot)
  5  probe batch (builtins / demos)
  6  start live feed (+ open Edge/Chrome preview)
  7  end live feed
  8  open live preview in browser (no restart)
  s  full smoke (capture + centroid + batch)
  q  quit / release lease
===========================================
""".rstrip()
    )


def run_menu(lab: Any, tag: str, kernel: str) -> int:
    print(f"lease_id={lab.lease_id}  backend={lab.backend_id}  holder={lab.holder}")
    print(f"camera={tag}  default_kernel={kernel}")
    while True:
        _menu(tag)
        choice = input("choice> ").strip().lower()
        try:
            if choice in ("q", "quit", "exit"):
                return 0
            if choice == "1":
                cmd_status(lab, tag)
            elif choice == "2":
                cmd_list_kernels(lab)
            elif choice == "3":
                cmd_capture(lab, tag)
            elif choice == "4":
                cmd_probe(lab, tag, kernel)
            elif choice == "5":
                cmd_probe_batch(lab, tag)
            elif choice == "6":
                cmd_live_start(lab, tag)
            elif choice == "7":
                cmd_live_end(lab, tag)
            elif choice == "8":
                cmd_live_open(lab, tag)
            elif choice == "s":
                cmd_smoke(lab, tag, kernel)
            else:
                print("  unknown choice")
        except KeyboardInterrupt:
            print("\n  (interrupted — back to menu)")
        except Exception as exc:  # noqa: BLE001
            _LOG.exception("action failed")
            print(f"  ERROR: {exc}")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Camera measurables + kernels smoke (default tag_22 / real.default)",
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--backend", default=DEFAULT_BACKEND)
    parser.add_argument("--camera", default=DEFAULT_CAMERA, help="camera tag_id")
    parser.add_argument("--kernel", default=DEFAULT_KERNEL, help="default probe kernel_id")
    parser.add_argument(
        "--once",
        choices=["status", "kernels", "capture", "probe", "smoke"],
        default=None,
        help="run one action then exit",
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
    tag = (args.camera or DEFAULT_CAMERA).strip()
    kernel = (args.kernel or DEFAULT_KERNEL).strip()
    _LOG.info("connecting backend=%s camera=%s base_url=%s", backend_id, tag, base_url)

    with connect(
        backend_id,
        base_url=base_url,
        verbose=True,
        timeout_s=float(args.timeout),
    ) as lab:
        if args.once == "status":
            cmd_status(lab, tag)
            return 0
        if args.once == "kernels":
            cmd_list_kernels(lab)
            return 0
        if args.once == "capture":
            capture_and_show(lab, tag, show=True)
            return 0
        if args.once == "probe":
            out = lab.probe_kernel(tag, DEFAULT_FIELD, kernel_id=kernel)
            print(out)
            return 0
        if args.once == "smoke":
            cmd_smoke(lab, tag, kernel)
            return 0
        return run_menu(lab, tag, kernel)


if __name__ == "__main__":
    sys.exit(main())
