"""Measurable plugin: ``camera_image`` (OPTICAL_CAMERA)."""
from __future__ import annotations

from typing import Any, Dict, Optional

from .capture import capture_and_materialize_camera_image
from .registry import register_measurable


@register_measurable(
    field_id="camera_image",
    widget="ImageViewer",
    dtype="uint8",
    domain="spatial",
    axes={"y": "pixel", "x": "pixel", "c": "bgr"},
    units={"y": "px", "x": "px", "c": "channel"},
    layout="lazy_image",
)
async def observe(
    bridge: Any,
    tag_id: str,
    catalog_meta: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Capture or synthesize a still frame; return metadata dict for state commit."""
    if (catalog_meta or {}).get("type") != "OPTICAL_CAMERA":
        return None

    filename = (
        f"{tag_id}_record.png"
        if hasattr(bridge, "_camera_images_base_dir")
        else f"{tag_id}_last.png"
    )
    return capture_and_materialize_camera_image(
        bridge,
        tag_id,
        catalog_meta,
        filename=filename,
    )
