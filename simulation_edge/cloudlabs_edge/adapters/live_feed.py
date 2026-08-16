"""START_LIVE_FEED / END_LIVE_FEED — soft arm bits + stub JPEG."""

from __future__ import annotations

from typing import Any

from adapters import context


def arm_live_feed(
    channel: str,
    profile: str | None = None,
    exposure_time_ms: float | None = None,
) -> dict:
    _ = profile
    ch = channel or "tag_22.camera_image"
    context.live_active.add(ch)
    context.live_active.add("tag_22")
    if ch.startswith("tag_22"):
        context.live_active.add("tag_22.camera_image")
        context.live_active.add("tag_22.camera_image.mjpeg")
    out: dict[str, Any] = {"channel": ch, "active": True}
    if exposure_time_ms is not None:
        out["live_exposure_time_ms"] = float(exposure_time_ms)
        if not hasattr(context, "live_exposure_ms"):
            context.live_exposure_ms = {}
        tag = ch.split(".", 1)[0] if "." in ch else "tag_22"
        context.live_exposure_ms[tag] = float(exposure_time_ms)
    return out


def set_live_exposure(args: dict[str, Any]) -> dict[str, Any]:
    tag_id = str(args.get("tag_id") or args.get("target_id") or "tag_22").strip()
    raw = args.get("exposure_time_ms")
    if raw is None:
        raw = args.get("live_exposure_time_ms")
    if raw is None or float(raw) <= 0:
        raise ValueError("SET_LIVE_EXPOSURE requires positive exposure_time_ms")
    if not is_live_armed(f"{tag_id}.camera_image") and not is_live_armed(tag_id):
        raise RuntimeError("LIVE_NOT_STARTED")
    exp = float(raw)
    if not hasattr(context, "live_exposure_ms"):
        context.live_exposure_ms = {}
    context.live_exposure_ms[tag_id] = exp
    return {
        "tag_id": tag_id,
        "live_exposure_time_ms": exp,
        "exposure_time_ms": exp,
    }


def disarm_live_feed(channel: str) -> dict:
    _ = channel
    context.live_active.clear()
    if hasattr(context, "live_exposure_ms"):
        context.live_exposure_ms.clear()
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
