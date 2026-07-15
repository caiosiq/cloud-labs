#!/usr/bin/env python3
"""01 — Connect, prepare, fluent move, capture.

Requires::

    pip install -e ./packages/cloudlabs
    python backend/main.py

Run::

    python scripts/language/01_hello_lab.py
    python scripts/language/01_hello_lab.py --catalog-pin laser-cavity-main
    python scripts/language/01_hello_lab.py --reconcile laser-cavity main
"""
from __future__ import annotations

from cloudlabs import configure_logging, connect, resolve_backend_id

_LOG = logging.getLogger("01_hello_lab")


def main() -> int:
    parser = argparse.ArgumentParser(description="cloudlabs: connect + fluent move + capture")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--backend", default=None)
    parser.add_argument(
        "--reconcile",
        nargs=2,
        metavar=("REPO", "BRANCH"),
        default=None,
        help="Load local snapshot and run reconcile_hardware",
    )
    parser.add_argument(
        "--catalog-pin",
        default="laser-cavity-main",
        metavar="PIN_ID",
        help="Catalog pin for prepare (default: laser-cavity-main); use '' to skip",
    )
    parser.add_argument("--tag-move", default="tag_20")
    parser.add_argument("--tag-capture", default="tag_22")
    args = parser.parse_args()
    if args.reconcile and args.catalog_pin == "":
        parser.error("--reconcile needs a pin or use prepare with repo/branch via --reconcile alone")

    configure_logging()
    base_url = args.base_url.rstrip("/")
    backend_id = args.backend or resolve_backend_id(base_url)
    _LOG.info("backend_id=%s base_url=%s", backend_id, base_url)

    with connect(backend_id, base_url=base_url, verbose=True) as lab:
        _LOG.info("lease_id=%s holder=%s", lab.lease_id, lab.holder)

        if args.reconcile:
            repo, branch = args.reconcile
            snap = lab.load_snapshot(repo, branch)
            _LOG.info("loaded %s commit=%s", snap.label(), snap.configuration_id)
            result = lab.reconcile_hardware()
            _LOG.info(
                "reconcile: %s (%d/%d)",
                result.message,
                result.steps_executed,
                result.steps_total,
            )
            if not result.succeeded():
                return 1
        elif args.catalog_pin:
            lab.prepare(catalog_pin=args.catalog_pin, reconcile=False)

        mirror = lab.components[args.tag_move]
        mirror.move(x=12.5).wait_until_idle()
        mirror.motor(1, 0.5).wait_until_idle()
        _LOG.info("system_status=%s", lab.get_lab_state().get("system_status"))

        cam = lab.components[args.tag_capture]
        try:
            image = lab.capture_measurable(args.tag_capture, "camera_image")
            _LOG.info(
                "captured keys=%s",
                sorted(image.keys()) if isinstance(image, dict) else type(image).__name__,
            )
            tensor = cam.measurable("camera_image").resolve()
            _LOG.info(
                "MeasurableTensor dtype=%s shape=%s domain=%s",
                tensor.dtype,
                tensor.shape,
                tensor.domain,
            )
        except Exception as exc:
            _LOG.warning("capture/tensor skipped: %s", exc)

    _LOG.info("01_hello_lab complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
