"""Phase 5: validate edge-owned premade kernels on synthetic Gaussians.

Loads artifacts from a temp edge ``kernels/`` tree (not coordinator
``schemas/kernels/``) to prove the edge-owned path.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

import numpy as np

_REPO = Path(__file__).resolve().parents[2]
_EDGE_KERNELS = _REPO / "mock_backend" / "cloudlabs_edge" / "kernels"


def _torch_ok() -> bool:
    try:
        import torch  # noqa: F401
    except Exception:
        return False
    return True


def _synthetic_gaussian(
    h: int,
    w: int,
    *,
    cx: float,
    cy: float,
    sigma: float = 8.0,
    amplitude: float = 200.0,
    background: float = 10.0,
    saturate: bool = False,
) -> np.ndarray:
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float64)
    g = amplitude * np.exp(-(((xs - cx) ** 2) + ((ys - cy) ** 2)) / (2.0 * sigma * sigma))
    frame = background + g
    if saturate:
        frame = np.where(g > 0.5 * amplitude, 255.0, frame)
    frame = np.clip(frame, 0, 255).astype(np.uint8)
    return np.stack([frame, frame, frame], axis=-1)


@unittest.skipUnless(_torch_ok(), "PyTorch not installed")
@unittest.skipUnless(_EDGE_KERNELS.is_dir(), "mock edge kernels/ missing")
class BuiltinKernelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmpdir = tempfile.TemporaryDirectory(prefix="edge_kernels_")
        cls.kernels_dir = Path(cls._tmpdir.name)
        for name in (
            "manifest.json",
            "builtin_roi_centroid.pt",
            "builtin_beam_power.pt",
            "builtin_gaussian_beam_fit.pt",
            "builtin_beam_shift.pt",
            "builtin_beam_com.pt",
        ):
            src = _EDGE_KERNELS / name
            if not src.is_file():
                raise unittest.SkipTest(f"missing edge artifact {src}")
            shutil.copy2(src, cls.kernels_dir / name)

        from cloudlabs_edge_dev.optimization.kernels import clear_caches

        clear_caches()

    @classmethod
    def tearDownClass(cls) -> None:
        from cloudlabs_edge_dev.optimization.kernels import clear_caches

        clear_caches()
        cls._tmpdir.cleanup()

    def _eval(self, kernel_id: str, bgr: np.ndarray):
        from cloudlabs_edge_dev.optimization.kernels import eval_kernel

        return eval_kernel(kernel_id, bgr, kernels_dir=self.kernels_dir)

    def test_manifest_lists_production_builtins(self) -> None:
        payload = json.loads((self.kernels_dir / "manifest.json").read_text(encoding="utf-8"))
        ids = {row["id"] for row in payload["kernels"]}
        for kid in (
            "builtin.roi_centroid",
            "builtin.beam_power",
            "builtin.gaussian_beam_fit",
            "builtin.beam_shift",
            "builtin.beam_com",
        ):
            self.assertIn(kid, ids)

    def test_roi_centroid_within_half_pixel(self) -> None:
        cx_t, cy_t = 90.0, 70.0
        bgr = _synthetic_gaussian(128, 160, cx=cx_t, cy=cy_t, sigma=6.0)
        kind, feats = self._eval("builtin.roi_centroid", bgr)
        self.assertEqual(kind, "features")
        self.assertEqual(len(feats), 3)
        self.assertLessEqual(abs(feats[0] - cx_t), 0.5)
        self.assertLessEqual(abs(feats[1] - cy_t), 0.5)
        # Peak is full-frame max after float normalize (~0..1).
        self.assertGreater(feats[2], 0.5)

    def test_beam_power_monotonic_with_amplitude(self) -> None:
        low = _synthetic_gaussian(96, 96, cx=48, cy=48, amplitude=40.0, background=5.0)
        high = _synthetic_gaussian(96, 96, cx=48, cy=48, amplitude=180.0, background=5.0)
        _, feats_lo = self._eval("builtin.beam_power", low)
        _, feats_hi = self._eval("builtin.beam_power", high)
        self.assertEqual(len(feats_lo), 3)
        self.assertGreater(feats_hi[0], feats_lo[0])  # flux
        self.assertGreater(feats_hi[1], feats_lo[1])  # peak

    def test_beam_power_saturation_flag(self) -> None:
        clean = _synthetic_gaussian(64, 64, cx=32, cy=32, amplitude=80.0, saturate=False)
        sat = _synthetic_gaussian(64, 64, cx=32, cy=32, amplitude=200.0, saturate=True)
        _, feats_clean = self._eval("builtin.beam_power", clean)
        _, feats_sat = self._eval("builtin.beam_power", sat)
        self.assertLess(feats_clean[2], 0.05)
        self.assertGreater(feats_sat[2], 0.05)

    def test_gaussian_beam_fit_recovers_center(self) -> None:
        cx_t, cy_t = 55.0, 40.0
        bgr = _synthetic_gaussian(96, 112, cx=cx_t, cy=cy_t, sigma=7.0, amplitude=220.0)
        kind, feats = self._eval("builtin.gaussian_beam_fit", bgr)
        self.assertEqual(kind, "features")
        self.assertEqual(len(feats), 5)
        # [amplitude, cx, cy, sigma_x, sigma_y]
        self.assertLessEqual(abs(feats[1] - cx_t), 1.0)
        self.assertLessEqual(abs(feats[2] - cy_t), 1.0)
        self.assertGreater(feats[3], 2.0)
        self.assertGreater(feats[4], 2.0)

    def test_beam_shift_matches_injected_displacement(self) -> None:
        """Magnitude is ‖CoM − (W/2, H/2)‖ for *this* frame size (no fixed center)."""
        h, w = 100, 120
        cx_t, cy_t = 70.0, 35.0
        bgr = _synthetic_gaussian(h, w, cx=cx_t, cy=cy_t, sigma=5.0, amplitude=210.0)
        kind, feats = self._eval("builtin.beam_shift", bgr)
        self.assertEqual(kind, "features")
        self.assertEqual(len(feats), 3)
        dx_exp = cx_t - (w * 0.5)
        dy_exp = cy_t - (h * 0.5)
        self.assertAlmostEqual(feats[0], dx_exp, delta=0.75)
        self.assertAlmostEqual(feats[1], dy_exp, delta=0.75)
        mag_exp = float(np.hypot(dx_exp, dy_exp))
        self.assertAlmostEqual(feats[2], mag_exp, delta=1.0)

        # Same physical offset fraction on a different resolution stays relative to that FOV.
        h2, w2 = 200, 240
        cx2, cy2 = 140.0, 70.0
        bgr2 = _synthetic_gaussian(h2, w2, cx=cx2, cy=cy2, sigma=10.0, amplitude=210.0)
        _, feats2 = self._eval("builtin.beam_shift", bgr2)
        self.assertAlmostEqual(feats2[0], cx2 - w2 * 0.5, delta=1.5)
        self.assertAlmostEqual(feats2[1], cy2 - h2 * 0.5, delta=1.5)

    def test_beam_com_matches_injected_center(self) -> None:
        h, w = 100, 120
        cx_t, cy_t = 70.0, 35.0
        bgr = _synthetic_gaussian(h, w, cx=cx_t, cy=cy_t, sigma=5.0, amplitude=210.0)
        kind, feats = self._eval("builtin.beam_com", bgr)
        self.assertEqual(kind, "features")
        self.assertEqual(len(feats), 3)
        self.assertAlmostEqual(feats[0], cx_t, delta=0.75)
        self.assertAlmostEqual(feats[1], cy_t, delta=0.75)
        self.assertGreater(feats[2], 0.5)


if __name__ == "__main__":
    unittest.main()
