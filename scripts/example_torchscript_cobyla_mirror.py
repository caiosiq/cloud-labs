#!/usr/bin/env python3
"""Educational example: catalog pin -> move mirror -> TorchScript M -> COBYLA on (M-M0)^2.

Closed-loop math runs on the edge, not in this process. Measure M0 once here
(or hard-code it), then bake it as ``target_scalar`` in the job IR — the edge
loop cannot call back into Python.

Minimal catalog-kernel path (~80 lines). For author-defined ``session.*``
kernels and multi-term feature objectives, see
``scripts/example_session_kernel_author.py``.

Run (server must be up on mock)::

    python scripts/example_torchscript_cobyla_mirror.py
"""
from __future__ import annotations

import os
import sys

_BACKEND_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
)
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from lab_model.optimization.sdk import (  # noqa: E402
    configure_logging,
    connect,
    resolve_backend_id,
)

# --- Hard-coded demo knobs -------------------------------------------------
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
            else lab.eval_kernel(CAMERA_TAG, "camera_image", kernel_id=KERNEL_ID)
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
    return 0 if status == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
