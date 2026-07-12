#!/usr/bin/env python3
"""Rebuild approved TorchScript fixtures under schemas/kernels/.

Usage (from repo root)::

    python scripts/build_torchscript_kernels.py
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "schemas" / "kernels"


def build_image_mean_score(out_path: Path) -> None:
    import torch
    import torch.nn as nn

    class ImageMeanScore(nn.Module):
        def forward(self, image: torch.Tensor) -> torch.Tensor:
            if image.dim() == 3:
                image = image.unsqueeze(0)
            return image.mean()

    module = torch.jit.script(ImageMeanScore())
    out_path.parent.mkdir(parents=True, exist_ok=True)
    module.save(str(out_path))
    print(f"wrote {out_path}")


def build_roi_mean_score(out_path: Path) -> None:
    import torch
    import torch.nn as nn

    class RoiMeanScore(nn.Module):
        def forward(self, image: torch.Tensor) -> torch.Tensor:
            if image.dim() == 3:
                image = image.unsqueeze(0)
            # NCHW — center half crop
            _n, _c, h, w = image.shape
            y0 = h // 4
            y1 = (3 * h) // 4
            x0 = w // 4
            x1 = (3 * w) // 4
            crop = image[:, :, y0:y1, x0:x1]
            return crop.mean()

    module = torch.jit.script(RoiMeanScore())
    out_path.parent.mkdir(parents=True, exist_ok=True)
    module.save(str(out_path))
    print(f"wrote {out_path}")


def build_peak_intensity(out_path: Path) -> None:
    import torch
    import torch.nn as nn

    class PeakIntensity(nn.Module):
        def forward(self, image: torch.Tensor) -> torch.Tensor:
            if image.dim() == 3:
                image = image.unsqueeze(0)
            return image.amax()

    module = torch.jit.script(PeakIntensity())
    out_path.parent.mkdir(parents=True, exist_ok=True)
    module.save(str(out_path))
    print(f"wrote {out_path}")


def main() -> None:
    build_image_mean_score(OUT_DIR / "demo_image_mean_score.pt")
    build_roi_mean_score(OUT_DIR / "demo_roi_mean_score.pt")
    build_peak_intensity(OUT_DIR / "demo_peak_intensity.pt")


if __name__ == "__main__":
    main()
