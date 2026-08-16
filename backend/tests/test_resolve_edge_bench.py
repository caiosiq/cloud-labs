"""Phase 4: Twin layout resolves from edge GET /bench (teaching disk fallback)."""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from lab_model.coordinator.catalog.resolve_edge_bench import (
    EdgeBenchUnavailable,
    resolve_edge_bench,
    unwrap_bench_layout,
)


class UnwrapBenchLayoutTests(unittest.TestCase):
    def test_unwraps_nested_edge_bench(self) -> None:
        flat = unwrap_bench_layout(
            {
                "backend_id": "mock.default",
                "layout": {
                    "lab_bounds_mm": {"x_min": -1, "x_max": 1, "y_min": -1, "y_max": 1}
                },
            }
        )
        self.assertEqual(flat["lab_bounds_mm"]["x_max"], 1)

    def test_accepts_flat_teaching_layout(self) -> None:
        flat = unwrap_bench_layout(
            {"lab_bounds_mm": {"x_min": 0, "x_max": 10, "y_min": 0, "y_max": 10}}
        )
        self.assertEqual(flat["lab_bounds_mm"]["x_max"], 10)


class ResolveEdgeBenchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="edge_bench_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_teaching_bench_beside_lab_view(self) -> None:
        pkg = self.tmp / "mock_backend"
        lab_view = pkg / "lab_view"
        lab_view.mkdir(parents=True)
        bench_dir = pkg / "cloudlabs_edge" / "bench"
        bench_dir.mkdir(parents=True)
        (bench_dir / "layout.json").write_text(
            json.dumps(
                {
                    "backend_id": "mock.default",
                    "layout": {
                        "lab_bounds_mm": {
                            "x_min": -500,
                            "x_max": 500,
                            "y_min": -500,
                            "y_max": 500,
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        # Edge data required so resolve_edge_root_beside_lab_view finds the tree.
        data = pkg / "cloudlabs_edge" / "data"
        data.mkdir(parents=True)
        (data / "library.json").write_text(
            json.dumps({"schema_version": 1, "components": {}}), encoding="utf-8"
        )
        (data / "inventory.json").write_text(
            json.dumps({"schema_version": 1, "entries": {}}), encoding="utf-8"
        )
        layout_path = lab_view / "layout.json"
        layout_path.write_text(
            json.dumps(
                {
                    "lab_bounds_mm": {
                        "x_min": 0,
                        "x_max": 1,
                        "y_min": 0,
                        "y_max": 1,
                    }
                }
            ),
            encoding="utf-8",
        )
        rt = SimpleNamespace(
            backend_id="mock.default",
            spec=SimpleNamespace(
                lab_view_path=str(lab_view),
                edge=SimpleNamespace(configured=False),
            ),
            paths=SimpleNamespace(layout_json=str(layout_path), root_dir=str(lab_view)),
        )
        resolved = resolve_edge_bench(rt)
        self.assertEqual(resolved.source, "edge_bench_disk")
        self.assertEqual(resolved.layout["lab_bounds_mm"]["x_max"], 500)

    def test_http_edge_preferred(self) -> None:
        from lab_model.execution.edge.endpoint import EdgeEndpointConfig

        edge = EdgeEndpointConfig(base_url="http://127.0.0.1:8200")
        rt = SimpleNamespace(
            backend_id="real.default",
            spec=SimpleNamespace(lab_view_path="", edge=edge),
            paths=SimpleNamespace(layout_json="", root_dir=""),
        )
        bench = {
            "backend_id": "real.default",
            "layout": {
                "lab_bounds_mm": {
                    "x_min": -100,
                    "x_max": 100,
                    "y_min": -100,
                    "y_max": 100,
                }
            },
        }
        with patch(
            "lab_model.coordinator.catalog.resolve_edge_bench.HttpEdgeClient"
        ) as cls:
            client = MagicMock()
            client.get_bench.return_value = bench
            cls.return_value = client
            resolved = resolve_edge_bench(rt)
        self.assertEqual(resolved.source, "edge_http")
        self.assertEqual(resolved.layout["lab_bounds_mm"]["x_max"], 100)

    def test_http_unavailable_fails_loud(self) -> None:
        from lab_model.execution.edge.endpoint import EdgeEndpointConfig

        edge = EdgeEndpointConfig(base_url="http://127.0.0.1:8200")
        rt = SimpleNamespace(
            backend_id="real.default",
            spec=SimpleNamespace(lab_view_path="", edge=edge),
            paths=SimpleNamespace(layout_json="", root_dir=""),
        )
        with patch(
            "lab_model.coordinator.catalog.resolve_edge_bench.HttpEdgeClient"
        ) as cls:
            client = MagicMock()
            client.get_bench.return_value = None
            cls.return_value = client
            with self.assertRaises(EdgeBenchUnavailable):
                resolve_edge_bench(rt)


class BindStorageGeometryTests(unittest.TestCase):
    def test_rebinds_process_geometry_from_active_edge_bench(self) -> None:
        """Mock Q3 must not stick when the active backend is real's centered strip."""
        from lab_model.coordinator.catalog.resolve_edge_bench import bind_storage_geometry
        from lab_model.language.domain.storage_region import (
            configure_from_layout_document,
            storage_grid_spec,
        )

        configure_from_layout_document(
            {
                "lab_bounds_mm": {
                    "x_min": -500,
                    "x_max": 500,
                    "y_min": -500,
                    "y_max": 500,
                },
                "danger_zone": {"radius_mm": 90, "padding_mm": 5},
                "storage": {"rule": "negative_xy", "grid_nx": 4, "grid_ny": 4},
            }
        )
        stuck = storage_grid_spec()
        self.assertEqual(stuck["nx"], 4)
        self.assertEqual(stuck["q3"]["x_max"], 0.0)

        real_layout = {
            "lab_bounds_mm": {
                "x_min": -500,
                "x_max": 500,
                "y_min": -500,
                "y_max": 500,
            },
            "danger_zone": {"radius_mm": 90, "padding_mm": 5},
            "storage": {
                "rule": "negative_xy",
                "grid_nx": 3,
                "grid_ny": 3,
                "bounds_mm": {
                    "x_min": -135.0,
                    "x_max": 135.0,
                    "y_min": -370.0,
                    "y_max": -100.0,
                },
            },
        }
        rt = SimpleNamespace(edge_layout_cache=None)
        with patch(
            "lab_model.coordinator.catalog.resolve_edge_bench.resolve_edge_bench",
            return_value=SimpleNamespace(layout=real_layout, source="edge_http"),
        ):
            bind_storage_geometry(rt)

        fixed = storage_grid_spec()
        self.assertEqual(fixed["nx"], 3)
        self.assertEqual(fixed["ny"], 3)
        self.assertEqual(fixed["q3"]["x_min"], -135.0)
        self.assertEqual(fixed["q3"]["x_max"], 135.0)
        self.assertEqual(fixed["q3"]["y_max"], -100.0)
        self.assertEqual(rt.edge_layout_cache, real_layout)


class MockEdgeBenchStorageLockstepTests(unittest.TestCase):
    """Mock edge bench must match lab_view packing extent (not bare full-Q3)."""

    def test_mock_edge_and_lab_view_storage_rects_agree(self) -> None:
        from lab_model.language.domain.storage_region import (
            configure_from_layout_document,
            storage_grid_spec,
        )

        repo = Path(__file__).resolve().parents[2]
        edge_doc = json.loads(
            (repo / "mock_backend" / "cloudlabs_edge" / "bench" / "layout.json").read_text(
                encoding="utf-8"
            )
        )
        lab_view_doc = json.loads(
            (repo / "mock_backend" / "lab_view" / "layout.json").read_text(encoding="utf-8")
        )
        edge_layout = unwrap_bench_layout(edge_doc)
        self.assertIn("extent_from_origin_mm", edge_layout.get("storage") or {})
        self.assertEqual(
            edge_layout["storage"]["extent_from_origin_mm"],
            lab_view_doc["storage"]["extent_from_origin_mm"],
        )

        configure_from_layout_document(edge_layout)
        edge_q3 = storage_grid_spec()["q3"]
        configure_from_layout_document(lab_view_doc)
        view_q3 = storage_grid_spec()["q3"]
        self.assertEqual(edge_q3, view_q3)
        # Dense mock packing (~75×95 mm cells), not full-lab 125×125 mm.
        self.assertAlmostEqual(edge_q3["x_min"], -300.0)
        self.assertAlmostEqual(edge_q3["y_min"], -380.0)
        self.assertLess(edge_q3["x_max"] - edge_q3["x_min"], 400.0)


if __name__ == "__main__":
    unittest.main()
