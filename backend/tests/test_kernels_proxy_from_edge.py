"""Phase 3: Twin /api/kernels proxies the active edge catalog."""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock


class KernelsProxyFromEdgeTests(unittest.TestCase):
    def test_api_kernels_uses_edge_rows_not_schemas(self) -> None:
        # Ensure coordinator backend/main is imported, not a teaching edge main.
        backend_dir = str(Path(__file__).resolve().parents[1])
        sys.path = [p for p in sys.path if "cloudlabs_edge" not in p.replace("\\", "/")]
        if backend_dir not in sys.path:
            sys.path.insert(0, backend_dir)
        sys.modules.pop("main", None)

        import main as main_mod

        class _Resolved:
            backend_id = "real.default"
            source = "edge_http"
            kernels = [
                {
                    "id": "builtin.roi_centroid",
                    "label": "ROI centroid",
                    "runtime": "torchscript",
                    "artifact_present": False,
                    "scope": "catalog",
                },
                {
                    "id": "demo.image_mean_score",
                    "label": "Demo mean",
                    "runtime": "torchscript",
                    "artifact_present": True,
                    "scope": "catalog",
                },
            ]

        req = mock.Mock()
        req.headers = {}

        with mock.patch.object(main_mod, "_active_backend_id", return_value="real.default"):
            with mock.patch.object(main_mod, "_extract_lease_id", return_value=None):
                with mock.patch.object(main_mod, "_runtime_for_active", return_value=object()):
                    with mock.patch(
                        "lab_model.coordinator.catalog.resolve_edge_kernels.resolve_edge_kernels",
                        return_value=_Resolved(),
                    ):
                        body = asyncio.run(main_mod.list_edge_kernels(request=req, backend=None))

        ids = {k["id"] for k in body["kernels"]}
        self.assertIn("builtin.roi_centroid", ids)
        self.assertIn("demo.image_mean_score", ids)
        self.assertEqual(body.get("source"), "edge_http")
        self.assertNotEqual(body.get("source"), "coordinator_fallback")


if __name__ == "__main__":
    unittest.main()
