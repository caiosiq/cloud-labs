"""Measurable plugin: ``camera_image`` (OPTICAL_CAMERA)."""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

from lab_model.catalog.schema import resolve_cam_id_for_tag, resolve_telemetry_stream_backend

from .registry import register_measurable


@register_measurable(field_id="camera_image", widget="ImageViewer")
async def observe(
    bridge: Any,
    tag_id: str,
    catalog_meta: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Capture or synthesize a still frame; return metadata dict for state commit."""
    if (catalog_meta or {}).get("type") != "OPTICAL_CAMERA":
        return None

    exp_ms = 200.0
    if hasattr(bridge, "return_tunables_for_tag"):
        tun = bridge.return_tunables_for_tag(tag_id) or {}
        if isinstance(tun.get("exposure_time_ms"), (int, float)):
            exp_ms = float(tun["exposure_time_ms"])

    exposure_s = exp_ms / 1000.0
    stream_backend = resolve_telemetry_stream_backend(catalog_meta)
    if stream_backend == "overhead" and hasattr(bridge, "capture_overhead_cam"):
        png = bridge.capture_overhead_cam(exposure=exposure_s)
        cam_id = 0
        mock_source = "mock_overhead_cam"
    else:
        cam_id = resolve_cam_id_for_tag(catalog_meta) or 1
        if hasattr(bridge, "table_cam_connect"):
            connected = getattr(bridge, "_table_cam_connected", {}).get(int(cam_id))
            if not connected:
                bridge.table_cam_connect(int(cam_id))
        png = bridge.capture_table_cam(int(cam_id), exposure=exposure_s)
        mock_source = "mock_table_cam"

    if not png:
        return None

    if hasattr(bridge, "_camera_images_base_dir"):
        cap_dir = bridge._camera_images_base_dir()
        out_name = f"{tag_id}_record.png"
        source = "real_table_cam"
    else:
        cap_dir = bridge._camera_captures_dir()
        out_name = f"{tag_id}_last.png"
        source = mock_source
    os.makedirs(cap_dir, exist_ok=True)
    out_path = os.path.join(cap_dir, out_name)
    with open(out_path, "wb") as f:
        f.write(png)
    return {
        "path": out_path,
        "source": source,
        "cam_id": int(cam_id),
        "format": "png",
    }
