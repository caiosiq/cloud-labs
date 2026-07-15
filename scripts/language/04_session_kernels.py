#!/usr/bin/env python3
"""04 — Author a TorchScript kernel, register it, run feature OPTIMIZE.

1. Author a small ``nn.Module`` (PyTorch tensor ops only).
2. ``lab.register_kernel(...)`` uploads an **artifact** (not a lab action).
3. ``probe_kernel`` (EVAL_KERNEL) measures once; bake targets into the job IR.
4. Edge OPTIMIZE / ``run_optimize`` uses your artifact every eval (in-process).

Minimal catalog-kernel path: ``03_closed_loop_catalog.py``.
See ``docs/SESSION_KERNELS.md``.

Run (mock server up)::

    pip install -e ./packages/cloudlabs
    python scripts/language/04_session_kernels.py
"""
from __future__ import annotations

from typing import Any, List

import torch
import torch.nn as nn

from cloudlabs import (
    ObjectiveGraphBuilder,
    configure_logging,
    connect,
    resolve_backend_id,
)

BASE_URL = "http://127.0.0.1:8000"
CATALOG_PIN = "laser-cavity-main"
START_MOTOR_DEG = 0.5
MAX_EVALS = 10
FEATURE_MAX_EVALS = 8
RECONCILE = False

MIRROR_TAG = "tag_20"
CAMERA_TAG = "tag_22"
MOTOR_PATH = "tunables.nominal_motor_positions.1"


class CenterRoiMean(nn.Module):
    """Scalar score: mean intensity of the center half of the frame."""

    def forward(self, image: torch.Tensor) -> torch.Tensor:
        if image.dim() == 3:
            image = image.unsqueeze(0)
        _n, _c, h, w = image.shape
        y0, y1 = h // 4, (3 * h) // 4
        x0, x1 = w // 4, (3 * w) // 4
        return image[:, :, y0:y1, x0:x1].mean()


class ImageMoments(nn.Module):
    """Feature vector: [brightness, contrast, peak] — loss stays in the IR."""

    def forward(self, image: torch.Tensor) -> torch.Tensor:
        if image.dim() == 3:
            image = image.unsqueeze(0)
        brightness = image.mean()
        contrast = image.std()
        peak = image.amax()
        return torch.stack([brightness, contrast, peak])


def _print_features(names: List[str], values: Any) -> None:
    feats = list(values) if isinstance(values, (list, tuple)) else [float(values)]
    parts = []
    for i, v in enumerate(feats):
        label = names[i] if i < len(names) else f"f{i}"
        parts.append(f"{label}={float(v):.4f}")
    print("features  " + "  ".join(parts))


def main() -> int:
    configure_logging()
    backend_id = resolve_backend_id(BASE_URL)

    with connect(
        backend_id,
        base_url=BASE_URL,
        holder="language:04_session_kernels",
        verbose=True,
    ) as lab:
        lab.prepare(catalog_pin=CATALOG_PIN, reconcile=RECONCILE)

        score_id = lab.register_kernel(
            "center_roi_mean",
            module=CenterRoiMean(),
            output_kind="scalar",
            description="Author demo: center-ROI mean intensity",
        )
        print(f"registered scalar kernel  {score_id}")

        feat_names = ["brightness", "contrast", "peak"]
        feat_id = lab.register_kernel(
            "image_moments",
            module=ImageMoments(),
            output_kind="features",
            feature_names=feat_names,
            description="Author demo: brightness / contrast / peak",
        )
        print(f"registered feature kernel {feat_id}")

        lab.set_tunable(MIRROR_TAG, MOTOR_PATH, START_MOTOR_DEG)
        lab.wait_until_idle()

        m0 = lab.probe_kernel(CAMERA_TAG, "camera_image", kernel_id=score_id)
        print(f"M0 (center ROI mean) = {float(m0):.6f}")

        feats = lab.probe_kernel(CAMERA_TAG, "camera_image", kernel_id=feat_id)
        _print_features(feat_names, feats)
        brightness0 = float(feats[0]) if isinstance(feats, list) else float(feats)

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
                kernel_id=score_id,
                target=float(m0),
            ),
            max_evals=MAX_EVALS,
            session_label=f"author CenterRoiMean -> COBYLA  M0={float(m0):.4f}",
        )

        status = str(result.get("status") or "")
        audit = (result.get("result") or {}).get("kernel_audit") or result.get(
            "kernel_audit"
        )
        print(f"optimize status = {status}")
        if audit:
            print(f"kernel audit  = {audit}")
        if status != "succeeded":
            return 1

        graph = (
            ObjectiveGraphBuilder()
            .term(
                term_id="match_brightness",
                weight=1.0,
                metric="squared_error",
                source={
                    "tag_id": CAMERA_TAG,
                    "kind": "torchscript_features",
                    "kernel_id": feat_id,
                    "from": "measurables.camera_image",
                    "feature_index": 0,
                    "target_scalar": brightness0,
                },
            )
            .term(
                term_id="minimize_contrast",
                weight=0.25,
                metric="minimize_value",
                source={
                    "tag_id": CAMERA_TAG,
                    "kind": "torchscript_features",
                    "kernel_id": feat_id,
                    "from": "measurables.camera_image",
                    "feature_index": 1,
                },
            )
        )
        feature_result = lab.run_optimize(
            variables=[
                lab.variable(
                    MIRROR_TAG,
                    MOTOR_PATH,
                    bounds=(START_MOTOR_DEG - 2.0, START_MOTOR_DEG + 2.0),
                    delta=True,
                )
            ],
            objective=graph,
            max_evals=FEATURE_MAX_EVALS,
            session_label=(
                f"author ImageMoments features -> COBYLA  "
                f"brightness0={brightness0:.4f}"
            ),
        )
        feature_status = str(feature_result.get("status") or "")
        print(f"feature optimize status = {feature_status}")
        feat_audit = (feature_result.get("result") or {}).get(
            "kernel_audit"
        ) or feature_result.get("kernel_audit")
        if feat_audit:
            print(f"feature kernel audit = {feat_audit}")

    return 0 if feature_status == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
