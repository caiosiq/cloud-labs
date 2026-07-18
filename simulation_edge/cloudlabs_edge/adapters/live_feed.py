"""START_LIVE_FEED / END_LIVE_FEED — soft arm bits + stub JPEG."""

from __future__ import annotations

from adapters import context


def arm_live_feed(channel: str, profile: str | None = None) -> dict:
    _ = profile
    ch = channel or "tag_22.camera_image"
    context.live_active.add(ch)
    context.live_active.add("tag_22")
    if ch.startswith("tag_22"):
        context.live_active.add("tag_22.camera_image")
        context.live_active.add("tag_22.camera_image.mjpeg")
    return {"channel": ch, "active": True}


def disarm_live_feed(channel: str) -> dict:
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
    from cloudlabs_edge_dev.stub_server import _STUB_JPEG

    if not is_live_armed(channel):
        raise RuntimeError("LIVE_NOT_STARTED")
    return _STUB_JPEG
