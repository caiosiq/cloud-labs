#!/usr/bin/env python3
"""02 — Catalog kernels, builtins, and kernel_match.

Kernels are **inputs** to primitives (EVAL_KERNEL probe, OPTIMIZE), not peer verbs.

Requires::

    pip install -e ./packages/cloudlabs

Run (mock server up)::

    python scripts/language/02_kernels_and_match.py
"""
from __future__ import annotations

from cloudlabs import configure_logging, connect, resolve_backend_id

BASE_URL = "http://127.0.0.1:8000"
CAMERA_TAG = "tag_22"
TARGET_PX = (512.0, 384.0)


def main() -> int:
    configure_logging()
    backend_id = resolve_backend_id(BASE_URL)

    with connect(backend_id, base_url=BASE_URL, verbose=True) as lab:
        rows = lab.list_kernels()
        print(f"kernels ({len(rows)}):")
        for row in rows[:12]:
            print(f"  {row.get('id')}  [{row.get('runtime') or row.get('backend')}]")

        cam = lab.components[CAMERA_TAG]
        cx_cy = cam.probe_kernel("camera_image", kernel_id="builtin.roi_centroid")
        print(f"builtin.roi_centroid → {cx_cy}")

        gauss = lab.probe_kernel(
            CAMERA_TAG,
            "camera_image",
            kernel_id="builtin.gaussian_beam_fit",
        )
        amp, cx, cy, sx, sy = gauss  # type: ignore[misc]
        print(
            f"builtin.gaussian_beam_fit → amp={amp:.3f} "
            f"cx={cx:.1f} cy={cy:.1f} sx={sx:.2f} sy={sy:.2f}"
        )

        # Closed-loop sketch: drive ROI centroid toward TARGET_PX.
        result = lab.run_cobyla(
            variables=[
                lab.variable(
                    "tag_20",
                    "tunables.nominal_motor_positions.1",
                    bounds=(-3.0, 3.0),
                    delta=True,
                )
            ],
            match_kernel=lab.kernel_match(
                CAMERA_TAG,
                "camera_image",
                kernel_id="builtin.roi_centroid",
                target=TARGET_PX,
                feature_index=(0, 1),
            ),
            max_evals=6,
        )
        print(f"optimize status = {result.get('status')}")
        return 0 if str(result.get("status") or "") == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
