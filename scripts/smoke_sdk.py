#!/usr/bin/env python3
"""Smoke test for the cloud-labs imperative SDK (Phase B.1).

Run with the FastAPI server up::

    cd backend
    python ..\\scripts\\smoke_sdk.py

Optional reconcile from a local branch or a frozen catalog pin::

    python ..\\scripts\\smoke_sdk.py --reconcile laser-cavity main
    python ..\\scripts\\smoke_sdk.py --catalog-pin laser-cavity-main
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

# Allow ``python scripts/smoke_sdk.py`` from repo root without PYTHONPATH.
_BACKEND_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
)
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from lab_model.optimization.sdk import CloudLabsClient, resolve_backend_id

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
_LOG = logging.getLogger("smoke_sdk")


def main() -> int:
    parser = argparse.ArgumentParser(description="cloud-labs SDK smoke test")
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000",
        help="FastAPI origin (default: http://127.0.0.1:8000)",
    )
    parser.add_argument(
        "--backend",
        default=None,
        help="Backend id (default: read active_backend_id from server)",
    )
    parser.add_argument(
        "--reconcile",
        nargs=2,
        metavar=("REPO", "BRANCH"),
        default=None,
        help="Optional: load local snapshot and run reconcile_hardware",
    )
    parser.add_argument(
        "--catalog-pin",
        default=None,
        metavar="PIN_ID",
        help="Optional: load frozen catalog pin and run reconcile_hardware",
    )
    parser.add_argument(
        "--tag-move",
        default="tag_20",
        help="Component tag for demo move (default: tag_20)",
    )
    parser.add_argument(
        "--tag-capture",
        default="tag_22",
        help="Component tag for demo capture (default: tag_22)",
    )
    args = parser.parse_args()
    if args.reconcile and args.catalog_pin:
        parser.error("pass either --reconcile REPO BRANCH or --catalog-pin PIN_ID, not both")

    base_url = args.base_url.rstrip("/")
    backend_id = args.backend or resolve_backend_id(base_url)
    _LOG.info("backend_id=%s base_url=%s", backend_id, base_url)

    with CloudLabsClient.connect(backend_id, base_url=base_url) as lab:
        _LOG.info("lease_id=%s holder=%s", lab.lease_id, lab.holder)

        if args.catalog_pin or args.reconcile:
            if args.catalog_pin:
                snap = lab.load_snapshot(catalog_pin=args.catalog_pin)
                assert snap.source == "catalog"
            else:
                repo, branch = args.reconcile
                snap = lab.load_snapshot(repo, branch)
                assert snap.source == "local"
            _LOG.info(
                "loaded %s commit=%s",
                snap.label(),
                snap.configuration_id,
            )
            result = lab.reconcile_hardware(on_progress=_print_progress)
            _LOG.info(
                "reconcile: %s (%d/%d steps)",
                result.message,
                result.steps_executed,
                result.steps_total,
            )
            if not result.succeeded():
                _LOG.error("reconcile did not finalize cleanly")
                return 1

        state = lab.get_lab_state()
        pose = (
            state.get("components", {})
            .get(args.tag_move, {})
            .get("statecontrol", {})
            .get("tunables", {})
            .get("nominal_pose", {})
        )
        x_before = float(pose.get("x", 0.0))
        x_after = x_before + 0.5
        _LOG.info("move %s nominal_pose.x: %s -> %s", args.tag_move, x_before, x_after)
        lab.move_component(args.tag_move, "tunables.nominal_pose.x", x_after)
        lab.wait_until_idle()
        _LOG.info("system_status after move: %s", lab.get_lab_state().get("system_status"))

        try:
            image = lab.capture_measurable(args.tag_capture, "measurables.camera_image")
            _LOG.info(
                "captured %s.camera_image keys=%s",
                args.tag_capture,
                sorted(image.keys()) if isinstance(image, dict) else type(image).__name__,
            )
            tensor = lab.measurable(args.tag_capture, "camera_image").resolve()
            _LOG.info(
                "tensor %s.%s dtype=%s shape=%s domain=%s",
                tensor.tag_id,
                tensor.field,
                tensor.dtype,
                tensor.shape,
                tensor.domain,
            )
        except Exception as exc:
            _LOG.warning("capture/tensor skipped or failed: %s", exc)

    _LOG.info("smoke_sdk complete")
    return 0


def _print_progress(message: str, step, done: int, total: int) -> None:
    label = getattr(step, "label", str(step))
    if total > 0:
        _LOG.info("%s | %s", message, label)
    else:
        _LOG.info("%s", message)


if __name__ == "__main__":
    sys.exit(main())
