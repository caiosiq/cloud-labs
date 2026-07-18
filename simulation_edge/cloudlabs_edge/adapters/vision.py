"""Synthetic camera capture for sim observe / kernel eval."""

from __future__ import annotations

from typing import Any, Optional


def capture_bgr(tag_id: str, *, exposure_s: Optional[float] = None) -> Any:
    _ = exposure_s
    import numpy as np

    h, w = 48, 64
    yy, xx = np.mgrid[0:h, 0:w]
    seed = sum(ord(c) for c in (tag_id or "tag")) % 200
    b = ((xx + seed) % 256).astype(np.uint8)
    g = ((yy + seed) % 256).astype(np.uint8)
    r = np.full((h, w), seed, dtype=np.uint8)
    return np.stack([b, g, r], axis=-1)


def encode_jpeg(bgr: Any, *, quality: int = 80, scale: float = 1.0) -> bytes:
    _ = scale
    try:
        import cv2
        import numpy as np

        arr = np.asarray(bgr)
        ok, buf = cv2.imencode(
            ".jpg", arr, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)]
        )
        if ok:
            return bytes(buf)
    except Exception:  # noqa: BLE001
        pass
    from cloudlabs_edge_dev.stub_server import _STUB_JPEG

    return _STUB_JPEG


def capture_jpeg(tag_id: str, *, profile: str | None = None) -> bytes:
    _ = profile
    return encode_jpeg(capture_bgr(tag_id))
