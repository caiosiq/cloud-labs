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


def _resolve_cam_id(channel: str, tag_id: str | None = None) -> int:
    """Map a Twin tag to mock recorder cam_id (1 or 2)."""
    tag = (tag_id or channel.split(".", 1)[0] if "." in channel else channel).strip()
    try:
        from lab_model.coordinator.catalog.schema import resolve_cam_id_for_tag

        lab = context.get_lab()
        catalog = getattr(lab, "catalog_map", None) or {}
        row = catalog.get(tag) if isinstance(catalog, dict) else None
        cam_id = resolve_cam_id_for_tag(row if isinstance(row, dict) else None)
        if cam_id is not None:
            return int(cam_id)
    except Exception:  # noqa: BLE001
        pass
    if tag.endswith("_2") or tag in ("tag_23", "tag_24"):
        return 2
    return 1


def _ensure_table_cam_live(lab: Any, cam_id: int) -> None:
    """Connect mock table cam and enable streaming for edge JPEG polls."""
    try:
        if hasattr(lab, "table_cam_connect"):
            lab.table_cam_connect(int(cam_id))
        if hasattr(lab, "table_cam_live_set"):
            lab.table_cam_live_set(int(cam_id), True)
    except Exception:  # noqa: BLE001
        pass


def _stop_table_cam_live(lab: Any, cam_id: int) -> None:
    try:
        if hasattr(lab, "table_cam_live_set"):
            lab.table_cam_live_set(int(cam_id), False)
    except Exception:  # noqa: BLE001
        pass


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
    tag = ch.split(".", 1)[0] if "." in ch else "tag_22"
    cam_id = _resolve_cam_id(ch, tag)
    lab = context.get_lab()
    _ensure_table_cam_live(lab, cam_id)
    out: dict[str, Any] = {"channel": ch, "active": True}
    if exposure_time_ms is not None:
        try:
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
    cam_id = _resolve_cam_id(f"{tag_id}.camera_image", tag_id)
    try:
        lab = context.get_lab()
        if hasattr(lab, "table_cam_send_vexp"):
            lab.table_cam_send_vexp(int(cam_id), float(exposure_ms) / 1000.0)
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
    try:
        lab = context.get_lab()
        for cam_id in (1, 2):
            _stop_table_cam_live(lab, cam_id)
    except Exception:  # noqa: BLE001
        pass
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
    """Return one JPEG from the mock table-cam preview, or a beam frame fallback."""
    if not is_live_armed(channel):
        raise RuntimeError("LIVE_NOT_STARTED")
    cam_id = _resolve_cam_id(channel)
    lab = context.get_lab()
    _ensure_table_cam_live(lab, cam_id)
    try:
        if hasattr(lab, "fetch_table_cam_preview_jpeg"):
            jpeg = lab.fetch_table_cam_preview_jpeg(int(cam_id))
            if isinstance(jpeg, (bytes, bytearray)) and len(jpeg) > 64:
                return bytes(jpeg)
    except Exception:  # noqa: BLE001
        pass
    try:
        from mock_backend.host.mock_beam_frame import encode_mock_beam_jpeg

        return encode_mock_beam_jpeg(cam_id=int(cam_id))
    except Exception:  # noqa: BLE001
        from cloudlabs_edge_dev.stub_server import _STUB_JPEG

        return _STUB_JPEG
