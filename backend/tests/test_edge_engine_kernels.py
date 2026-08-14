"""Phase 1: edge kernel loader (manifest + digest + torch-absent)."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from cloudlabs_edge_dev.optimization import kernels as kern


class KernelLoaderTests(unittest.TestCase):
    def test_unknown_id_refuses(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "manifest.json").write_text(
                json.dumps({"schema_version": 1, "kernels": []}),
                encoding="utf-8",
            )
            with self.assertRaises(kern.KernelError):
                kern.load_module("builtin.missing", root)

    def test_digest_mismatch_refuses(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(kern.KernelError) as ctx:
                kern.provision_bytes(
                    "session.test.score.abcd1234",
                    b"hello-kernel",
                    kernels_dir=root,
                    digest="sha256:" + ("0" * 64),
                )
            self.assertIn("digest mismatch", str(ctx.exception))

    def test_provision_and_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = b"not-really-torchscript"
            digest = "sha256:" + hashlib.sha256(data).hexdigest()
            path = kern.provision_bytes(
                "session.test.score.abcd1234",
                data,
                kernels_dir=root,
                digest=digest,
                output_kind="scalar",
            )
            self.assertTrue(path.is_file())
            rows = kern.list_kernels(root)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["id"], "session.test.score.abcd1234")
            self.assertTrue(rows[0]["artifact_present"])

    def test_torch_absent_refuses_load(self) -> None:
        if kern.torch_available():
            self.skipTest("torch is installed; cannot assert absent path")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "manifest.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "kernels": [
                            {
                                "id": "demo.x",
                                "artifact": "demo.pt",
                                "runtime": "torchscript",
                                "output_kind": "scalar",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (root / "demo.pt").write_bytes(b"x")
            with self.assertRaises(kern.KernelError) as ctx:
                kern.load_module("demo.x", root)
            self.assertIn("torchscript_execution unavailable", str(ctx.exception))

    @unittest.skipUnless(kern.torch_available(), "requires torch")
    def test_scalar_and_features_eval(self) -> None:
        import torch

        class MeanScore(torch.nn.Module):
            def forward(self, x: torch.Tensor) -> torch.Tensor:  # noqa: ANN001
                return x.mean()

        class CentroidStub(torch.nn.Module):
            def forward(self, x: torch.Tensor) -> torch.Tensor:  # noqa: ANN001
                # Return fake [cx, cy]
                return torch.tensor([12.0, 34.0])

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mean_path = root / "mean.pt"
            feat_path = root / "feat.pt"
            torch.jit.script(MeanScore()).save(str(mean_path))
            torch.jit.script(CentroidStub()).save(str(feat_path))
            (root / "manifest.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "kernels": [
                            {
                                "id": "demo.mean",
                                "artifact": "mean.pt",
                                "runtime": "torchscript",
                                "output_kind": "scalar",
                            },
                            {
                                "id": "demo.centroid",
                                "artifact": "feat.pt",
                                "runtime": "torchscript",
                                "output_kind": "features",
                                "feature_names": ["cx", "cy"],
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            import numpy as np

            bgr = np.zeros((8, 8, 3), dtype=np.uint8)
            bgr[:] = 128
            kind, value = kern.eval_kernel("demo.mean", bgr, kernels_dir=root)
            self.assertEqual(kind, "scalar")
            self.assertTrue(0.0 <= float(value) <= 1.0)
            kind, feats = kern.eval_kernel("demo.centroid", bgr, kernels_dir=root)
            self.assertEqual(kind, "features")
            self.assertEqual(list(feats), [12.0, 34.0])


if __name__ == "__main__":
    unittest.main()
