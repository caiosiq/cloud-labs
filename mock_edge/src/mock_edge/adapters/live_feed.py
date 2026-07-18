"""START_LIVE_FEED / END_LIVE_FEED backed by mock session state + preview JPEG."""

from __future__ import annotations

from typing import Optional

from mock_edge.adapters import context


def arm_live_feed(channel: str, profile: str | None = None) -> dict:
    """Arm Tier B wire for ``channel`` (mock in-memory arm bit)."""
    _ = profile
    ch = channel or "tag_22.camera_image"
    context.live_active.add(ch)
    context.live_active.add("tag_22")
    if ch.startswith("tag_22"):
        context.live_active.add("tag_22.camera_image")
        context.live_active.add("tag_22.camera_image.mjpeg")
    return {"channel": ch, "active": True}


def disarm_live_feed(channel: str) -> dict:
    """Clear all live arm bits (mock treats live as a single session)."""
    _ = channel
    context.live_active.clear()
    return {"active": False}


def is_live_armed(channel: str) -> bool:
    if not context.live_active:
        return False
    if channel in context.live_active:
        return True
    return channel.startswith("tag_22.camera_image") and "tag_22.camera_image" in context.live_active


def read_live_jpeg(channel: str) -> bytes:
    """Return one JPEG from the mock table-cam preview, or a tiny stub."""
    from cloudlabs_edge_dev.stub_server import _STUB_JPEG

    if not is_live_armed(channel):
        raise RuntimeError("LIVE_NOT_STARTED")
    lab = context.get_lab()
    try:
        if hasattr(lab, "fetch_table_cam_preview_jpeg"):
            jpeg = lab.fetch_table_cam_preview_jpeg(0, 0.2)
            if isinstance(jpeg, (bytes, bytearray)) and len(jpeg) > 2:
                return bytes(jpeg)
    except Exception:  # noqa: BLE001
        pass
    return _STUB_JPEG
