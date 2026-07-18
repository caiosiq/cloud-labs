#!/usr/bin/env python3
"""07 — Live plane: arm Tier B feed, then latch (same verbs as Twin).

Twin and the SDK share one language:

* ``START_LIVE_FEED`` / ``END_LIVE_FEED`` — arm / disarm wire JPEG/MJPEG
* ``RECORD_MEASURABLES`` (via ``capture_measurable``) — latched science path
* ``GET /api/lab-state`` — Tier C overview only (layout / leases), not live science

This script does **not** invent a side-door feed helper. After arming, Twin
widgets bind to catalog stream URLs; scripts that need truth still latch.

Requires::

    pip install -e ./packages/cloudlabs
    python backend/main.py

Run::

    python scripts/language/07_live_plane.py
"""
from __future__ import annotations

import argparse
import logging
import time

from cloudlabs import configure_logging, connect, resolve_backend_id

_LOG = logging.getLogger("07_live_plane")


def main() -> int:
    parser = argparse.ArgumentParser(description="cloudlabs: live plane arm + latch")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--backend", default=None)
    parser.add_argument(
        "--catalog-pin",
        default="laser-cavity-main",
        help="Catalog pin for prepare (default: laser-cavity-main); '' to skip",
    )
    parser.add_argument("--tag-camera", default="tag_22")
    parser.add_argument(
        "--hold-s",
        type=float,
        default=1.0,
        help="Seconds to leave live feed armed (Twin would show MJPEG here)",
    )
    args = parser.parse_args()

    configure_logging()
    base_url = args.base_url.rstrip("/")
    backend_id = args.backend or resolve_backend_id(base_url)
    _LOG.info("backend_id=%s base_url=%s", backend_id, base_url)

    with connect(backend_id, base_url=base_url, verbose=True) as lab:
        if args.catalog_pin:
            lab.prepare(catalog_pin=args.catalog_pin, reconcile=False)

        # Tier C — overview only (do not treat as live science).
        overview = lab.get_lab_state()
        _LOG.info(
            "Tier C lab-state: system_status=%s components=%d",
            overview.get("system_status"),
            len(overview.get("components") or {}),
        )

        cam = args.tag_camera
        armed = lab.start_live_feed(cam, channel="stream")
        _LOG.info(
            "START_LIVE_FEED %s epoch_ms=%s transport=%s",
            cam,
            armed.get("epoch_ms"),
            armed.get("edge_transport"),
        )

        time.sleep(max(0.0, float(args.hold_s)))

        # Latched observation — same path Twin "Capture measurables" uses.
        image = lab.capture_measurable(cam, "camera_image")
        _LOG.info(
            "RECORD_MEASURABLES %s → %s",
            cam,
            type(image).__name__ if image is not None else None,
        )

        ended = lab.end_live_feed(cam)
        _LOG.info("END_LIVE_FEED %s status=%s", cam, ended.get("status"))

    _LOG.info(
        "done — Twin equivalent: startLiveFeed / recordMeasurables / endLiveFeed "
        "(frontend/js/cloudlabs/client.js)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
