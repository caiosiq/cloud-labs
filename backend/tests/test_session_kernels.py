"""Tests for session TorchScript kernels + feature metrics."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import numpy as np

from lab_model.optimization.kernels import session_store
from lab_model.optimization.kernels.torchscript_runtime import (
    clear_torchscript_caches,
    run_torchscript_features,
    run_torchscript_output,
    run_torchscript_scalar,
    torch_available,
)
from lab_model.optimization.metrics.feature_metrics import (
    metric_minimize_value,
    metric_rms_distance,
)
from lab_model.optimization.metrics.squared_error import metric_squared_error
from lab_model.optimization.spec import ObjectiveTermSpec


@unittest.skipUnless(torch_available(), "PyTorch not installed")
class SessionStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["CLOUDLABS_KERNEL_SESSION_ROOT"] = self._tmp.name
        clear_torchscript_caches()
        session_store.clear_extra_kernels_roots()

    def tearDown(self) -> None:
        session_store.clear_extra_kernels_roots()
        clear_torchscript_caches()
        self._tmp.cleanup()
        os.environ.pop("CLOUDLABS_KERNEL_SESSION_ROOT", None)

    def _tiny_pt(self) -> bytes:
        import torch
        import torch.nn as nn

        class Mean(nn.Module):
            def forward(self, image: torch.Tensor) -> torch.Tensor:
                if image.dim() == 3:
                    image = image.unsqueeze(0)
                return image.mean()

        path = Path(self._tmp.name) / "t.pt"
        torch.jit.script(Mean()).save(str(path))
        return path.read_bytes()

    def _features_pt(self) -> bytes:
        import torch
        import torch.nn as nn

        class Feats(nn.Module):
            def forward(self, image: torch.Tensor) -> torch.Tensor:
                if image.dim() == 3:
                    image = image.unsqueeze(0)
                m = image.mean()
                return torch.stack([m, m * 0.5, m * 0.25])

        path = Path(self._tmp.name) / "f.pt"
        torch.jit.script(Feats()).save(str(path))
        return path.read_bytes()

    def test_register_and_resolve(self) -> None:
        entry = session_store.register_package(
            backend_id="mock.default",
            lease_id="lease_test",
            name="mean",
            artifact_bytes=self._tiny_pt(),
            output_kind="scalar",
        )
        kid = entry["id"]
        self.assertTrue(kid.startswith("session."))
        session_store.activate_lease_roots("mock.default", "lease_test")
        bgr = np.full((16, 16, 3), 128, dtype=np.uint8)
        score = run_torchscript_scalar(kid, bgr)
        self.assertAlmostEqual(score, 128 / 255.0, delta=0.05)

    def test_features_kernel(self) -> None:
        entry = session_store.register_package(
            backend_id="mock.default",
            lease_id="lease_feat",
            name="beam",
            artifact_bytes=self._features_pt(),
            output_kind="features",
            feature_names=["cx", "cy", "radius"],
        )
        kid = entry["id"]
        session_store.activate_lease_roots("mock.default", "lease_feat")
        bgr = np.full((16, 16, 3), 255, dtype=np.uint8)
        kind, value = run_torchscript_output(kid, bgr)
        self.assertEqual(kind, "features")
        self.assertEqual(len(value), 3)
        feats = run_torchscript_features(kid, bgr)
        self.assertEqual(len(feats), 3)

    def test_size_limit(self) -> None:
        with self.assertRaises(ValueError):
            session_store.register_package(
                backend_id="mock.default",
                lease_id="lease_big",
                name="big",
                artifact_bytes=b"x" * (session_store.MAX_ARTIFACT_BYTES + 1),
            )

    def test_policy_blocks_non_mock(self) -> None:
        os.environ.pop("CLOUDLABS_ALLOW_SESSION_KERNELS", None)
        with self.assertRaises(PermissionError):
            session_store.register_package(
                backend_id="real.lab_1",
                lease_id="lease_x",
                name="mean",
                artifact_bytes=self._tiny_pt(),
            )

    def test_stage_for_job(self) -> None:
        entry = session_store.register_package(
            backend_id="mock.default",
            lease_id="lease_job",
            name="mean",
            artifact_bytes=self._tiny_pt(),
        )
        kid = entry["id"]
        audit = session_store.stage_packages_for_job(
            backend_id="mock.default",
            lease_id="lease_job",
            job_id="job_1",
            kernel_ids=[kid],
        )
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit[0]["kernel_id"], kid)
        self.assertTrue(str(audit[0]["digest"]).startswith("sha256:"))
        session_store.activate_job_roots("mock.default", "job_1")
        score = run_torchscript_scalar(kid, np.full((8, 8, 3), 64, dtype=np.uint8))
        self.assertGreaterEqual(score, 0.0)


class FeatureMetricTests(unittest.TestCase):
    def test_squared_error_feature_index(self) -> None:
        term = ObjectiveTermSpec.model_validate(
            {
                "id": "t",
                "weight": 1.0,
                "source": {
                    "tag_id": "tag_22",
                    "kind": "torchscript_features",
                    "kernel_id": "x",
                    "feature_index": 1,
                    "target_scalar": 0.5,
                },
                "metric": "squared_error",
            }
        )
        loss = metric_squared_error({"features": [0.0, 0.5, 1.0]}, term)
        self.assertAlmostEqual(loss, 0.0)

    def test_rms_distance(self) -> None:
        term = ObjectiveTermSpec.model_validate(
            {
                "id": "t",
                "weight": 1.0,
                "source": {
                    "tag_id": "tag_22",
                    "kind": "torchscript_features",
                    "kernel_id": "x",
                    "feature_index": [0, 1],
                    "target_px": {"x": 10.0, "y": 10.0},
                },
                "metric": "rms_distance",
            }
        )
        loss = metric_rms_distance({"features": [10.0, 10.0, 3.0]}, term)
        self.assertAlmostEqual(loss, 0.0)

    def test_minimize_value(self) -> None:
        term = ObjectiveTermSpec.model_validate(
            {
                "id": "t",
                "weight": 1.0,
                "source": {
                    "tag_id": "tag_22",
                    "kind": "torchscript_features",
                    "kernel_id": "x",
                    "feature_index": 2,
                },
                "metric": "minimize_value",
            }
        )
        loss = metric_minimize_value({"features": [1.0, 2.0, 3.5]}, term)
        self.assertAlmostEqual(loss, 3.5)


@unittest.skipUnless(torch_available(), "PyTorch not installed")
class CatalogCuratedKernels(unittest.TestCase):
    def setUp(self) -> None:
        clear_torchscript_caches()

    def test_roi_and_peak_present(self) -> None:
        from lab_model.optimization.kernels import get_kernel

        for kid in ("demo.roi_mean_score", "demo.peak_intensity"):
            desc = get_kernel(kid)
            self.assertIsNotNone(desc)
            assert desc is not None
            self.assertTrue(desc.artifact_present)
            bgr = np.full((32, 32, 3), 200, dtype=np.uint8)
            score = run_torchscript_scalar(kid, bgr)
            self.assertGreaterEqual(score, 0.0)
            self.assertLessEqual(score, 1.0)


if __name__ == "__main__":
    unittest.main()
