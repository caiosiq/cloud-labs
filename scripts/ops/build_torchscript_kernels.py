#!/usr/bin/env python3
"""Rebuild approved TorchScript fixtures under schemas/kernels/.

Usage (from repo root)::

    python scripts/ops/build_torchscript_kernels.py
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
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


def build_roi_centroid(out_path: Path) -> None:
    """Sub-pixel intensity CoM in the center-half ROI (full-frame coords)."""
    import torch
    import torch.nn as nn

    class RoiCentroid(nn.Module):
        def forward(self, image: torch.Tensor) -> torch.Tensor:
            if image.dim() == 3:
                image = image.unsqueeze(0)
            _n, _c, h, w = image.shape
            y0 = h // 4
            y1 = (3 * h) // 4
            x0 = w // 4
            x1 = (3 * w) // 4
            crop = image[0, :, y0:y1, x0:x1]
            gray = crop.mean(dim=0)
            peak = torch.clamp(gray.max(), min=1e-6)
            weights = gray * (gray > (0.5 * peak)).to(gray.dtype)
            total = torch.clamp(weights.sum(), min=1e-6)
            ch = int(gray.size(0))
            cw = int(gray.size(1))
            ys = (
                torch.arange(ch, dtype=gray.dtype, device=gray.device)
                .unsqueeze(1)
                .expand(ch, cw)
            )
            xs = (
                torch.arange(cw, dtype=gray.dtype, device=gray.device)
                .unsqueeze(0)
                .expand(ch, cw)
            )
            cy = (weights * ys).sum() / total + float(y0)
            cx = (weights * xs).sum() / total + float(x0)
            return torch.stack([cx, cy])

    module = torch.jit.script(RoiCentroid())
    out_path.parent.mkdir(parents=True, exist_ok=True)
    module.save(str(out_path))
    print(f"wrote {out_path}")


def build_gaussian_beam_fit(out_path: Path) -> None:
    """Moment-based Gaussian beam estimate: [amplitude, cx, cy, sigma_x, sigma_y].

    Not a nonlinear least-squares fit — intensity-weighted first/second moments
    after a soft peak threshold. Good default for alignment loops on real beams.
    """
    import torch
    import torch.nn as nn

    class GaussianBeamFit(nn.Module):
        def forward(self, image: torch.Tensor) -> torch.Tensor:
            if image.dim() == 3:
                image = image.unsqueeze(0)
            gray = image[0].mean(dim=0)
            amplitude = gray.max()
            peak = torch.clamp(amplitude, min=1e-6)
            weights = gray * (gray > (0.1 * peak)).to(gray.dtype)
            total = torch.clamp(weights.sum(), min=1e-6)
            h = int(gray.size(0))
            w = int(gray.size(1))
            ys = (
                torch.arange(h, dtype=gray.dtype, device=gray.device)
                .unsqueeze(1)
                .expand(h, w)
            )
            xs = (
                torch.arange(w, dtype=gray.dtype, device=gray.device)
                .unsqueeze(0)
                .expand(h, w)
            )
            cy = (weights * ys).sum() / total
            cx = (weights * xs).sum() / total
            var_y = (weights * (ys - cy) * (ys - cy)).sum() / total
            var_x = (weights * (xs - cx) * (xs - cx)).sum() / total
            sigma_y = torch.sqrt(torch.clamp(var_y, min=1e-6))
            sigma_x = torch.sqrt(torch.clamp(var_x, min=1e-6))
            return torch.stack([amplitude, cx, cy, sigma_x, sigma_y])

    module = torch.jit.script(GaussianBeamFit())
    out_path.parent.mkdir(parents=True, exist_ok=True)
    module.save(str(out_path))
    print(f"wrote {out_path}")


def main() -> None:
    build_image_mean_score(OUT_DIR / "demo_image_mean_score.pt")
    build_roi_mean_score(OUT_DIR / "demo_roi_mean_score.pt")
    build_peak_intensity(OUT_DIR / "demo_peak_intensity.pt")
    build_roi_centroid(OUT_DIR / "builtin_roi_centroid.pt")
    build_gaussian_beam_fit(OUT_DIR / "builtin_gaussian_beam_fit.pt")


if __name__ == "__main__":
    main()
