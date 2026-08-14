#!/usr/bin/env python3
"""Rebuild TorchScript kernel fixtures and optionally seed edge ``kernels/`` trees.

Usage (from repo root)::

    python scripts/ops/build_torchscript_kernels.py
    python scripts/ops/build_torchscript_kernels.py --seed-edges

Coordinator ``schemas/kernels/`` remains a CI/fixture catalog (Phase 5). Live
catalogs are the edge-owned ``cloudlabs_edge/kernels/`` trees.
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "schemas" / "kernels"

EDGE_KERNEL_DIRS = [
    ROOT / "mock_backend" / "cloudlabs_edge" / "kernels",
    ROOT / "simulation_edge" / "cloudlabs_edge" / "kernels",
    ROOT.parent / "lab_automation" / "cloudlabs_edge" / "kernels",
    ROOT
    / "packages"
    / "cloudlabs_edge_dev"
    / "src"
    / "cloudlabs_edge_dev"
    / "scaffold_fixtures"
    / "kernels",
]

# Artifacts that belong on every edge starter catalog.
EDGE_SEED_ARTIFACTS = (
    "demo_image_mean_score.pt",
    "demo_roi_mean_score.pt",
    "builtin_roi_centroid.pt",
    "builtin_beam_power.pt",
    "builtin_gaussian_beam_fit.pt",
    "builtin_beam_shift.pt",
)


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
    """Sub-pixel intensity CoM in the center-half ROI (full-frame coords).

    Returns ``[cx, cy, peak]`` where ``peak`` is the **full-frame** max gray
    intensity (presence proxy for OPTIMIZE latch vs first-eval peak).
    """
    import torch
    import torch.nn as nn

    class RoiCentroid(nn.Module):
        def forward(self, image: torch.Tensor) -> torch.Tensor:
            if image.dim() == 3:
                image = image.unsqueeze(0)
            _n, _c, h, w = image.shape
            full_gray = image[0].mean(dim=0)
            peak = full_gray.max()
            y0 = h // 4
            y1 = (3 * h) // 4
            x0 = w // 4
            x1 = (3 * w) // 4
            crop = image[0, :, y0:y1, x0:x1]
            gray = crop.mean(dim=0)
            local_peak = torch.clamp(gray.max(), min=1e-6)
            weights = gray * (gray > (0.5 * local_peak)).to(gray.dtype)
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
            return torch.stack([cx, cy, peak])

    module = torch.jit.script(RoiCentroid())
    out_path.parent.mkdir(parents=True, exist_ok=True)
    module.save(str(out_path))
    print(f"wrote {out_path}")


def build_gaussian_beam_fit(out_path: Path) -> None:
    """Moment-based Gaussian beam estimate: [amplitude, cx, cy, sigma_x, sigma_y]."""
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


def build_beam_power(out_path: Path) -> None:
    """Power proxy: [flux_above_background, peak, saturated_fraction]."""
    import torch
    import torch.nn as nn

    class BeamPower(nn.Module):
        def forward(self, image: torch.Tensor) -> torch.Tensor:
            if image.dim() == 3:
                image = image.unsqueeze(0)
            gray = image[0].mean(dim=0)
            flat = gray.reshape(-1)
            # Approximate median via sort midpoint (TorchScript-friendly).
            sorted_vals, _ = torch.sort(flat)
            n = int(sorted_vals.numel())
            mid = max(n // 2, 0)
            background = sorted_vals[mid] if n > 0 else torch.tensor(0.0, dtype=gray.dtype)
            above = torch.clamp(gray - background, min=0.0)
            flux = above.sum()
            peak = gray.max()
            sat = (gray >= 0.98).to(gray.dtype).mean()
            return torch.stack([flux, peak, sat])

    module = torch.jit.script(BeamPower())
    out_path.parent.mkdir(parents=True, exist_ok=True)
    module.save(str(out_path))
    print(f"wrote {out_path}")


def build_beam_shift(out_path: Path) -> None:
    """Offset of intensity CoM from this frame's geometric center.

    Outputs ``[dx, dy, magnitude]`` in pixels where the origin is
    ``(W/2, H/2)`` of the *current* capture — so different camera
    resolutions need no baked-in center like 2790. Pair with a
    **negative** objective weight on ``magnitude`` to maximize shift.
    No reference / baseline capture.
    """
    import torch
    import torch.nn as nn

    class BeamShift(nn.Module):
        def forward(self, image: torch.Tensor) -> torch.Tensor:
            if image.dim() == 3:
                image = image.unsqueeze(0)
            gray = image[0].mean(dim=0)
            peak = torch.clamp(gray.max(), min=1e-6)
            weights = gray * (gray > (0.5 * peak)).to(gray.dtype)
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
            # Geometric FOV center of *this* tensor — not a fixed lab pixel.
            dx = cx - (float(w) * 0.5)
            dy = cy - (float(h) * 0.5)
            mag = torch.sqrt(dx * dx + dy * dy)
            return torch.stack([dx, dy, mag])

    module = torch.jit.script(BeamShift())
    out_path.parent.mkdir(parents=True, exist_ok=True)
    module.save(str(out_path))
    print(f"wrote {out_path}")


EDGE_MANIFEST = {
    "schema_version": 1,
    "kernels": [
        {
            "id": "demo.image_mean_score",
            "label": "Demo image mean score",
            "artifact": "demo_image_mean_score.pt",
            "runtime": "torchscript",
            "output_kind": "scalar",
            "description": "Teaching scalar: mean intensity after RGB float normalize.",
        },
        {
            "id": "demo.roi_mean_score",
            "label": "Demo center ROI mean",
            "artifact": "demo_roi_mean_score.pt",
            "runtime": "torchscript",
            "output_kind": "scalar",
            "description": "Mean intensity over the center-half ROI.",
        },
        {
            "id": "builtin.roi_centroid",
            "label": "ROI centroid (center half)",
            "artifact": "builtin_roi_centroid.pt",
            "runtime": "torchscript",
            "output_kind": "features",
            "feature_names": ["cx", "cy", "peak"],
            "description": (
                "Sub-pixel intensity-weighted centroid in the center-half ROI, "
                "plus full-frame peak for beam-presence latching."
            ),
        },
        {
            "id": "builtin.beam_power",
            "label": "Beam power features",
            "artifact": "builtin_beam_power.pt",
            "runtime": "torchscript",
            "output_kind": "features",
            "feature_names": ["flux_above_background", "peak", "saturated_fraction"],
            "description": "Power proxy; saturation channel mandatory for closed loop.",
        },
        {
            "id": "builtin.gaussian_beam_fit",
            "label": "Gaussian beam moments",
            "artifact": "builtin_gaussian_beam_fit.pt",
            "runtime": "torchscript",
            "output_kind": "features",
            "feature_names": ["amplitude", "cx", "cy", "sigma_x", "sigma_y"],
            "description": "Moment-based Gaussian beam estimate.",
        },
        {
            "id": "builtin.beam_shift",
            "label": "Beam shift from FOV center",
            "artifact": "builtin_beam_shift.pt",
            "runtime": "torchscript",
            "output_kind": "features",
            "feature_names": ["dx", "dy", "magnitude"],
            "description": (
                "Intensity CoM offset from this frame's geometric center "
                "(W/2, H/2) as [dx, dy, magnitude] in px — resolution-agnostic, "
                "no reference capture. Maximize shift with weight=-1 on "
                "minimize_value(feature_index=2)."
            ),
        },
    ],
}


def _update_schemas_manifest() -> None:
    """Keep coordinator fixture manifest in sync (CI / local torchscript_runtime)."""
    path = OUT_DIR / "manifest.json"
    if path.is_file():
        payload = json.loads(path.read_text(encoding="utf-8"))
    else:
        payload = {"schema_version": 1, "kernels": []}
    by_id = {
        str(k.get("id")): dict(k)
        for k in (payload.get("kernels") or [])
        if isinstance(k, dict) and k.get("id")
    }
    for row in EDGE_MANIFEST["kernels"]:
        kid = row["id"]
        existing = by_id.get(kid, {})
        merged = dict(existing)
        merged.update(row)
        merged.setdefault("backend", "any")
        merged.setdefault("phase", "production")
        merged.setdefault("hooks", ["evaluate", "capture", "tensor"])
        by_id[kid] = merged
    # Keep demo.peak_intensity if previously present (CI fixture only).
    payload["kernels"] = sorted(by_id.values(), key=lambda r: str(r.get("id") or ""))
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"updated {path}")


def seed_edges() -> None:
    """Copy starter .pt + write edge manifests into mock/sim/real/scaffold trees."""
    for dest in EDGE_KERNEL_DIRS:
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "manifest.json").write_text(
            json.dumps(EDGE_MANIFEST, indent=2) + "\n",
            encoding="utf-8",
        )
        for name in EDGE_SEED_ARTIFACTS:
            src = OUT_DIR / name
            if not src.is_file():
                print(f"skip missing fixture {src}")
                continue
            shutil.copy2(src, dest / name)
            print(f"seeded {dest / name}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--seed-edges",
        action="store_true",
        help="Copy built .pt + edge manifests into mock/sim/real/scaffold kernels/",
    )
    args = parser.parse_args()

    build_image_mean_score(OUT_DIR / "demo_image_mean_score.pt")
    build_roi_mean_score(OUT_DIR / "demo_roi_mean_score.pt")
    build_peak_intensity(OUT_DIR / "demo_peak_intensity.pt")
    build_roi_centroid(OUT_DIR / "builtin_roi_centroid.pt")
    build_beam_power(OUT_DIR / "builtin_beam_power.pt")
    build_gaussian_beam_fit(OUT_DIR / "builtin_gaussian_beam_fit.pt")
    build_beam_shift(OUT_DIR / "builtin_beam_shift.pt")
    _update_schemas_manifest()
    if args.seed_edges:
        seed_edges()


if __name__ == "__main__":
    main()
