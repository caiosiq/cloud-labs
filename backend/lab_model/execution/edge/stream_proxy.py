"""Coordinator stream proxy for Edge Contract Tier B (Phase 3).

Default product rule: Twin attaches to coordinator URLs; coordinator relays
JPEG/MJPEG bytes from the edge without re-encoding BGR. Optional LAN direct
when ``edge.lan_direct_ok`` and the client is on the same secure LAN (caller
decides whether to return the absolute edge URL instead of proxying).
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Dict, Optional, Tuple

import httpx
from fastapi.responses import StreamingResponse


async def iter_proxy_bytes(
    url: str,
    *,
    timeout_s: float = 30.0,
) -> AsyncIterator[bytes]:
    async with httpx.AsyncClient(timeout=timeout_s) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            async for chunk in resp.aiter_bytes():
                if chunk:
                    yield chunk


def proxy_stream_response(
    url: str,
    *,
    media_type: str,
    timeout_s: float = 60.0,
) -> StreamingResponse:
    return StreamingResponse(
        iter_proxy_bytes(url, timeout_s=timeout_s),
        media_type=media_type,
    )


def resolve_live_channel_path(
    capabilities: Optional[Dict[str, Any]],
    *,
    measurable_or_channel: str,
    prefer_mjpeg: bool = True,
) -> Optional[Tuple[str, str]]:
    """Return ``(path, transport)`` from capabilities.telemetry_channels."""
    if not isinstance(capabilities, dict):
        return None
    channels = capabilities.get("telemetry_channels") or {}
    if not isinstance(channels, dict):
        return None

    key = measurable_or_channel
    candidates = [key]
    if prefer_mjpeg and not key.endswith(".mjpeg"):
        candidates.insert(0, f"{key}.mjpeg")

    for cand in candidates:
        meta = channels.get(cand)
        if not isinstance(meta, dict):
            continue
        path = meta.get("path")
        transport = str(meta.get("transport") or "")
        if path:
            return str(path), transport

    # Fallback: first channel that binds this measurable.
    for meta in channels.values():
        if not isinstance(meta, dict):
            continue
        if meta.get("binds_measurable") == measurable_or_channel:
            path = meta.get("path")
            if path:
                return str(path), str(meta.get("transport") or "")
    return None


def media_type_for_transport(transport: str) -> str:
    if transport == "mjpeg_http":
        return "multipart/x-mixed-replace; boundary=frame"
    if transport in ("jpeg_poll", "jpeg_http"):
        return "image/jpeg"
    return "application/octet-stream"


__all__ = [
    "iter_proxy_bytes",
    "media_type_for_transport",
    "proxy_stream_response",
    "resolve_live_channel_path",
]
