#!/usr/bin/env python3
"""03 — Catalog pin → catalog TorchScript → OPTIMIZE (COBYLA).

Closed-loop math runs on the edge inside the OPTIMIZE primitive. Measure M0
once with ``probe_kernel`` (EVAL_KERNEL), then bake it as ``target`` in the
job IR — the edge cannot call back into Python.

For author-defined ``session.*`` kernels see ``04_session_kernels.py``.

Requires::

    pip install -e ./packages/cloudlabs

Run (mock server up)::

    python scripts/language/03_closed_loop_catalog.py
"""
from __future__ import annotations

from cloudlabs import configure_logging, connect, resolve_backend_id

BASE_URL = "http://127.0.0.1:8000"
CATALOG_PIN = "laser-cavity-main"
START_MOTOR_DEG = 0.5
M0 = 0.35  # baked target; set None to measure from camera instead
MAX_EVALS = 8
RECONCILE = False

MIRROR_TAG = "tag_20"
CAMERA_TAG = "tag_22"
MOTOR_PATH = "tunables.nominal_motor_positions.1"
KERNEL_ID = "demo.image_mean_score"


def main() -> int:
    configure_logging()
    backend_id = resolve_backend_id(BASE_URL)

    with connect(backend_id, base_url=BASE_URL, verbose=True) as lab:
        lab.prepare(catalog_pin=CATALOG_PIN, reconcile=RECONCILE)
        lab.set_tunable(MIRROR_TAG, MOTOR_PATH, START_MOTOR_DEG)
        lab.wait_until_idle()

        m0 = (
            float(M0)
            if M0 is not None
            else float(lab.probe_kernel(CAMERA_TAG, "camera_image", kernel_id=KERNEL_ID))
        )
        print(f"M0={m0}")

        result = lab.run_cobyla(
            variables=[
                lab.variable(
                    MIRROR_TAG,
                    MOTOR_PATH,
                    bounds=(START_MOTOR_DEG - 2.0, START_MOTOR_DEG + 2.0),
                    delta=True,
                )
            ],
            match_kernel=lab.kernel_match(
                CAMERA_TAG,
                "camera_image",
                kernel_id=KERNEL_ID,
                target=m0,
            ),
            max_evals=MAX_EVALS,
        )

        status = str(result.get("status") or "")
        print(f"optimize status = {status}")
        return 0 if status == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
