"""START_LIVE_FEED / END_LIVE_FEED backed by mock session state + preview JPEG."""

from __future__ import annotations

from typing import Any, Optional

from adapters import context


def _optional_exposure_ms(args: dict[str, Any] | None) -> float | None:
    if not isinstance(args, dict):
        return None
    raw = args.get("exposure_time_ms")
    if raw is None:
        raw = args.get("live_exposure_time_ms")
    if raw is None:
        return None
    return float(raw)


def arm_live_feed(
    channel: str,
    profile: str | None = None,
    exposure_time_ms: float | None = None,
) -> dict:
    """Arm Tier B wire for ``channel`` (mock in-memory arm bit)."""
    _ = profile
    ch = channel or "tag_22.camera_image"
    # Twin may still send ``stream`` on older coordinators — treat as camera.
    if ch in ("stream", "preview", "all"):
        ch = "tag_22.camera_image"
    context.live_active.add(ch)
    context.live_active.add("tag_22")
    if ch.startswith("tag_22") or ch.endswith(".camera_image"):
        # Prefer the concrete measurable key when Twin remaps to {tag}.camera_image.
        base = ch if ch.endswith(".camera_image") else "tag_22.camera_image"
        if ch.endswith(".camera_image"):
            base = ch
        context.live_active.add(base)
        context.live_active.add(f"{base}.mjpeg")
        if base.startswith("tag_"):
            context.live_active.add(base.split(".", 1)[0])
    out: dict[str, Any] = {"channel": ch, "active": True}
    if exposure_time_ms is not None:
        tag = ch.split(".", 1)[0] if "." in ch else "tag_22"
        try:
            lab = context.get_lab()
            cam_id = 0
            if hasattr(lab, "table_cam_send_vexp"):
                lab.table_cam_send_vexp(int(cam_id), float(exposure_time_ms) / 1000.0)
        except Exception:  # noqa: BLE001
            pass
        out["live_exposure_time_ms"] = float(exposure_time_ms)
        if not hasattr(context, "live_exposure_ms"):
            context.live_exposure_ms = {}
        context.live_exposure_ms[tag] = float(exposure_time_ms)
    return out


def set_live_exposure(args: dict[str, Any]) -> dict[str, Any]:
    """Preview exposure only (mock) — does not write science tunables."""
    tag_id = str(args.get("tag_id") or args.get("target_id") or "tag_22").strip()
    exposure_ms = _optional_exposure_ms(args)
    if exposure_ms is None or exposure_ms <= 0:
        raise ValueError("SET_LIVE_EXPOSURE requires positive exposure_time_ms")
    if not is_live_armed(f"{tag_id}.camera_image") and not is_live_armed(tag_id):
        raise RuntimeError("LIVE_NOT_STARTED")
    try:
        lab = context.get_lab()
        if hasattr(lab, "table_cam_send_vexp"):
            lab.table_cam_send_vexp(0, float(exposure_ms) / 1000.0)
    except Exception:  # noqa: BLE001
        pass
    if not hasattr(context, "live_exposure_ms"):
        context.live_exposure_ms = {}
    context.live_exposure_ms[tag_id] = float(exposure_ms)
    return {
        "tag_id": tag_id,
        "live_exposure_time_ms": float(exposure_ms),
        "exposure_time_ms": float(exposure_ms),
    }


def disarm_live_feed(channel: str) -> dict:
    """Clear all live arm bits (mock treats live as a single session)."""
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
