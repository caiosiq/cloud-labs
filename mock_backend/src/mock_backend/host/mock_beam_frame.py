"""Synthetic live-preview frames: laser beam on a table camera sensor."""

from __future__ import annotations

import math
import time
from typing import Optional

import cv2
import numpy as np


def render_mock_beam_frame(
    ts: Optional[float] = None,
    *,
    cam_id: int = 1,
    width: int = 480,
    height: int = 360,
) -> np.ndarray:
    """Return a BGR uint8 frame resembling a camera viewing a laser spot."""
    if ts is None:
        ts = time.perf_counter()
    cid = float(cam_id)
    h, w = int(height), int(width)
    frame = np.zeros((h, w, 3), dtype=np.uint8)

    # Dark sensor field with radial vignette.
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    cx_v, cy_v = w * 0.5, h * 0.52
    dist = np.sqrt(((xx - cx_v) / (w * 0.58)) ** 2 + ((yy - cy_v) / (h * 0.58)) ** 2)
    vignette = np.clip(1.0 - dist * 0.62, 0.12, 1.0)
    flicker = 0.04 * math.sin(ts * 1.7 + cid * 0.3)
    base = 10.0 + flicker
    frame[:, :, 0] = np.clip(base * vignette * 1.05, 0, 255).astype(np.uint8)
    frame[:, :, 1] = np.clip(base * vignette * 0.98, 0, 255).astype(np.uint8)
    frame[:, :, 2] = np.clip(base * vignette * 0.92, 0, 255).astype(np.uint8)

    # Faint optical-bench grid.
    grid_c = (16, 18, 22)
    step = 36
    for gx in range(0, w, step):
        cv2.line(frame, (gx, 0), (gx, h), grid_c, 1, cv2.LINE_AA)
    for gy in range(0, h, step):
        cv2.line(frame, (0, gy), (w, gy), grid_c, 1, cv2.LINE_AA)

    # Laser beam enters from the left with a slight angle per camera.
    angle = -0.07 + cid * 0.035 + 0.015 * math.sin(ts * 0.45)
    beam_y0 = int(h * (0.36 + 0.035 * math.sin(ts * 0.22 + cid)))
    beam_y1 = int(beam_y0 + (w - 24) * math.tan(angle))
    for thick, color in (
        (14, (18, 24, 90)),
        (8, (24, 48, 170)),
        (4, (48, 96, 230)),
        (2, (170, 210, 255)),
    ):
        cv2.line(frame, (0, beam_y0), (w - 20, beam_y1), color, thick, cv2.LINE_AA)

    # Bright impact spot — beam centroid on the sensor.
    spot_phase = ts * (2.6 + cid * 0.08)
    spot_x = int(w * (0.64 + 0.05 * math.sin(spot_phase * 0.42)))
    spot_y = int(beam_y0 + spot_x * math.tan(angle) + 10 * math.sin(spot_phase))
    for radius, color in (
        (52, (12, 20, 70)),
        (36, (18, 40, 140)),
        (22, (30, 70, 210)),
        (12, (80, 140, 255)),
        (5, (210, 235, 255)),
    ):
        cv2.circle(frame, (spot_x, spot_y), radius, color, -1, cv2.LINE_AA)
    cv2.circle(frame, (spot_x, spot_y), 2, (255, 255, 255), -1, cv2.LINE_AA)

    # Deterministic sensor grain (stable per frame, no RNG flicker).
    grain_seed = int(ts * 1000) % 9973
    rng = np.random.default_rng(grain_seed + int(cid))
    grain = rng.integers(0, 10, (h, w, 3), dtype=np.uint8)
    frame = cv2.add(frame, grain)

    cv2.rectangle(frame, (8, 8), (w - 8, h - 8), (28, 32, 40), 1, cv2.LINE_AA)
    cv2.circle(frame, (22, 22), 4, (0, 0, 210), -1, cv2.LINE_AA)
    cv2.putText(
        frame,
        f"LIVE  CAM{int(cam_id)}",
        (34, 28),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.45,
        (195, 205, 215),
        1,
        cv2.LINE_AA,
    )
    cv2.putText(
        frame,
        "laser spot on sensor",
        (34, 44),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.32,
        (115, 125, 140),
        1,
        cv2.LINE_AA,
    )
    return frame


def encode_mock_beam_jpeg(
    ts: Optional[float] = None,
    *,
    cam_id: int = 1,
    width: int = 480,
    height: int = 360,
    quality: int = 86,
) -> bytes:
    frame = render_mock_beam_frame(ts, cam_id=cam_id, width=width, height=height)
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, int(quality)])
    return buf.tobytes() if ok else b""
