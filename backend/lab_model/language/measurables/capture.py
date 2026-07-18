"""Shared camera PNG capture for RECORD_MEASURABLES and ensemble eval."""
from __future__ import annotations

import os
from typing import Any, Dict, Mapping, Optional, Tuple

from lab_model.coordinator.catalog.schema import (
    resolve_cam_id_for_tag,
    resolve_hardware_binding,
    resolve_telemetry_stream_backend,
)


def capture_png_for_tag(
    bridge: Any,
    tag_id: str,
    catalog_meta: Optional[Mapping[str, Any]] = None,
    *,
    exposure_s: Optional[float] = None,
) -> Optional[bytes]:
    """Capture one still frame as PNG bytes from table or overhead camera.

    Used by both the ``camera_image`` measurable observer and real ensemble eval
    so exposure / device routing stay in one place.
    """
    meta = dict(catalog_meta or {})
    if exposure_s is None:
        exposure_s = _exposure_s_from_bridge(bridge, tag_id)

    binding = resolve_hardware_binding(meta)
    stream_backend = resolve_telemetry_stream_backend(meta)
    if stream_backend == "overhead" or (
        binding is not None and binding.backend in ("opencv_usb", "overhead")
    ):
        png = _call_capture(bridge, "capture_overhead_cam", exposure=exposure_s)
        return png if isinstance(png, (bytes, bytearray)) else None

    cam_id = resolve_cam_id_for_tag(meta) or 1
    connect = getattr(bridge, "table_cam_connect", None)
    if callable(connect):
        connected = getattr(bridge, "_table_cam_connected", {})
        if isinstance(connected, dict) and not connected.get(int(cam_id)):
            connect(int(cam_id))
    png = _call_capture(bridge, "capture_table_cam", int(cam_id), exposure=exposure_s)
    return png if isinstance(png, (bytes, bytearray)) else None


def read_camera_bgr_for_tag(
    bridge: Any,
    tag_id: str,
    catalog_meta: Optional[Mapping[str, Any]] = None,
    *,
    exposure_s: Optional[float] = None,
) -> Optional[Any]:
    """Capture one still and decode to OpenCV BGR uint8 HxWx3 (Step C bridge).

    Same routing as :func:`capture_png_for_tag`. Returns ``None`` when capture
    or decode fails (real benches must not invent synthetic frames).
    """
    from lab_model.execution.optimization.metrics.image_features import decode_png_bytes_to_bgr

    png = capture_png_for_tag(
        bridge,
        tag_id,
        catalog_meta,
        exposure_s=exposure_s,
    )
    if not png:
        return None
    return decode_png_bytes_to_bgr(bytes(png))


def write_png_capture(
    png: bytes,
    *,
    directory: str,
    filename: str,
) -> str:
    """Write PNG bytes to ``directory/filename`` and return the absolute path."""
    os.makedirs(directory, exist_ok=True)
    out_path = os.path.join(directory, filename)
    with open(out_path, "wb") as handle:
        handle.write(png)
    return out_path


def camera_image_meta_from_png(
    bridge: Any,
    tag_id: str,
    catalog_meta: Optional[Mapping[str, Any]],
    png: bytes,
    *,
    filename: Optional[str] = None,
    source: Optional[str] = None,
) -> Dict[str, Any]:
    """Persist a capture and return the ``camera_image`` measurable dict."""
    meta = dict(catalog_meta or {})
    binding = resolve_hardware_binding(meta)
    stream_backend = resolve_telemetry_stream_backend(meta)
    if stream_backend == "overhead" or (
        binding is not None and binding.backend in ("opencv_usb", "overhead")
    ):
        cam_id = int(binding.device_index if binding and binding.device_index is not None else 0)
        default_source = "overhead_cam"
    else:
        cam_id = int(resolve_cam_id_for_tag(meta) or 1)
        default_source = "table_cam"

    cap_dir, dir_source = _resolve_capture_dir(bridge)
    default_source = source or dir_source or default_source

    out_name = filename or f"{tag_id}_last.png"
    path = write_png_capture(png, directory=cap_dir, filename=out_name)
    return {
        "path": path,
        "source": default_source,
        "cam_id": cam_id,
        "format": "png",
    }


def capture_and_materialize_camera_image(
    bridge: Any,
    tag_id: str,
    catalog_meta: Optional[Mapping[str, Any]] = None,
    *,
    exposure_s: Optional[float] = None,
    filename: Optional[str] = None,
    source: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Capture → write PNG → return camera_image measurable metadata."""
    png = capture_png_for_tag(
        bridge,
        tag_id,
        catalog_meta,
        exposure_s=exposure_s,
    )
    if not png:
        return None
    return camera_image_meta_from_png(
        bridge,
        tag_id,
        catalog_meta,
        png,
        filename=filename,
        source=source,
    )


def _call_capture(bridge: Any, method_name: str, *args: Any, **kwargs: Any) -> Any:
    fn = getattr(bridge, method_name, None)
    if not callable(fn):
        return None
    try:
        return fn(*args, **kwargs)
    except Exception:
        return None


def _resolve_capture_dir(bridge: Any) -> Tuple[str, str]:
    """Pick a writable capture directory; ignore non-string mock callables."""
    for attr, hint in (
        ("_camera_images_base_dir", "real_table_cam"),
        ("_camera_captures_dir", "mock_table_cam"),
    ):
        fn = getattr(bridge, attr, None)
        if not callable(fn):
            continue
        try:
            path = fn()
        except Exception:
            continue
        # Strict ``str`` only — MagicMock implements PathLike via auto attrs.
        if type(path) is str and path.strip():
            return path, hint
    return os.path.join(os.getcwd(), "camera_captures"), "camera"


def _exposure_s_from_bridge(bridge: Any, tag_id: str) -> float:
    exp_ms = 200.0
    tun_fn = getattr(bridge, "return_tunables_for_tag", None)
    if callable(tun_fn):
        try:
            tun = tun_fn(tag_id) or {}
        except Exception:
            tun = {}
        if isinstance(tun, dict) and isinstance(tun.get("exposure_time_ms"), (int, float)):
            exp_ms = float(tun["exposure_time_ms"])
    return exp_ms / 1000.0


__all__ = [
    "camera_image_meta_from_png",
    "capture_and_materialize_camera_image",
    "capture_png_for_tag",
    "read_camera_bgr_for_tag",
    "write_png_capture",
]
