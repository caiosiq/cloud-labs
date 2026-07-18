#!/usr/bin/env python3
"""06 — Client-side loop on camera measurables (author in the loop).

Counterpart to ``03_closed_loop_catalog.py`` / edge ``OPTIMIZE``:

* **Here:** each iteration runs on the laptop — capture ``camera_image``,
  turn the ``MeasurableTensor`` into a local numpy array, score it in
  Python, decide the next motor step, then issue a primitive.
* **Not here:** TorchScript kernels, ``probe_kernel``, or ``run_cobyla``.
  The edge only acts/senses one step at a time; the decision loop is yours.

This is the imperative pattern from Learn → *Imperative vs closed-loop*:
one HTTP round-trip per decision, with SciPy / custom logic free to run
between steps.

Requires::

    pip install -e ./packages/cloudlabs
    # numpy used for the local score (usually already present)

Run (mock server up)::

    python scripts/language/06_client_side_measurable_loop.py
"""
from __future__ import annotations

import logging

from cloudlabs import configure_logging, connect, resolve_backend_id

_LOG = logging.getLogger("06_client_side_measurable_loop")

BASE_URL = "http://127.0.0.1:8000"
CATALOG_PIN = "laser-cavity-main"
MIRROR_TAG = "tag_20"
CAMERA_TAG = "tag_22"
MOTOR_PATH = "tunables.nominal_motor_positions.1"
START_MOTOR_DEG = 0.5
N_STEPS = 6
STEP_DEG = 0.35
TARGET_MEAN = 0.35  # normalized mean brightness we walk toward


def _local_mean_brightness(bgr) -> float:
    """Score the resolved camera tensor entirely on the client (no kernel)."""
    import numpy as np

    arr = np.asarray(bgr)
    if arr.ndim != 3 or arr.shape[-1] != 3:
        raise ValueError(f"expected HxWx3 BGR uint8, got shape={getattr(arr, 'shape', None)}")
    # BGR uint8 → mean intensity in [0, 1]
    return float(arr.mean()) / 255.0


def main() -> int:
    configure_logging()
    backend_id = resolve_backend_id(BASE_URL)
    _LOG.info("backend_id=%s — client-side measurable loop (no OPTIMIZE / no kernels)", backend_id)

    with connect(backend_id, base_url=BASE_URL, verbose=True) as lab:
        lab.prepare(catalog_pin=CATALOG_PIN, reconcile=False)
        lab.set_tunable(MIRROR_TAG, MOTOR_PATH, START_MOTOR_DEG)
        lab.wait_until_idle()

        cam = lab.components[CAMERA_TAG]
        angle = float(START_MOTOR_DEG)
        history: list[tuple[float, float]] = []

        for i in range(N_STEPS):
            # Capture + materialize MeasurableTensor on the client (BGR HxWx3).
            tensor = cam.measurable("camera_image").resolve(record=True)
            score = _local_mean_brightness(tensor.data)
            history.append((angle, score))
            err = score - TARGET_MEAN
            _LOG.info(
                "step=%d angle=%.3f° mean=%.4f err=%+.4f dtype=%s shape=%s",
                i,
                angle,
                score,
                err,
                tensor.dtype,
                tensor.shape,
            )

            if abs(err) < 0.02:
                _LOG.info("close enough to target=%.2f — stopping early", TARGET_MEAN)
                break

            # Author-side policy: nudge motor opposite the brightness error.
            # (Teaching mock: mean brightness responds to tip; real benches need a real policy.)
            angle = angle - STEP_DEG if err > 0 else angle + STEP_DEG
            lab.set_tunable(MIRROR_TAG, MOTOR_PATH, angle)
            lab.wait_until_idle()

        _LOG.info("history (angle_deg, mean_brightness)=%s", history)
        _LOG.info(
            "done — decisions ran on the laptop; contrast with "
            "03_closed_loop_catalog.py where OPTIMIZE keeps the loop on the edge"
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
