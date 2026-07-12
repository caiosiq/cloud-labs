"""Shared image-derived metrics for edge ensemble evaluation (Phase G.7)."""
from __future__ import annotations

from typing import Any, Optional, Tuple


def compute_beam_centroid_px(bgr: Any) -> Optional[Tuple[float, float]]:
    """Intensity-weighted centroid of the brightest region in a BGR frame."""
    if bgr is None:
        return None
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None

    if getattr(bgr, "ndim", 0) != 3 or bgr.shape[2] < 3:
        return None

    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    flat = gray.astype(np.float64).ravel()
    if flat.size == 0:
        return None

    threshold = float(np.percentile(flat, 92.0))
    mask = gray.astype(np.float64) >= threshold
    weights = gray.astype(np.float64) * mask
    total = float(weights.sum())
    if total <= 1e-6:
        return None

    ys, xs = np.indices(gray.shape)
    cx = float((xs * weights).sum() / total)
    cy = float((ys * weights).sum() / total)
    return cx, cy


def compute_beam_power_scalar(bgr: Any) -> Optional[float]:
    """Normalized bright-pixel energy in [0, 1] for scalar objective terms."""
    if bgr is None:
        return None
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None

    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    flat = gray.astype(np.float64).ravel()
    if flat.size == 0:
        return None
    threshold = float(np.percentile(flat, 90.0))
    bright = flat[flat >= threshold]
    if bright.size == 0:
        return 0.0
    return float(np.clip(bright.mean() / 255.0, 0.0, 1.0))


def decode_png_bytes_to_bgr(data: bytes) -> Optional[Any]:
    """Decode PNG/JPEG bytes to a BGR numpy array for OpenCV metrics."""
    if not data or len(data) < 8:
        return None
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None

    arr = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_UNCHANGED)
    if img is None:
        return None
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    elif img.ndim == 3 and img.shape[2] == 4:
        img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
    if img.ndim != 3 or img.shape[2] != 3:
        return None
    return img


__all__ = [
    "compute_beam_centroid_px",
    "compute_beam_power_scalar",
    "decode_png_bytes_to_bgr",
]
