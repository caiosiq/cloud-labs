"""PR C: Twin catalog resolves from edge data, not coordinator_data."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from lab_model.coordinator.catalog.resolve_edge_catalog import (
    EdgeCatalogUnavailable,
    catalog_map_from_resolved,
    resolve_edge_catalog,
)


def _write_edge_data(edge_root: Path) -> None:
    data = edge_root / "data"
    data.mkdir(parents=True, exist_ok=True)
    (data / "library.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "components": {
                    "tag_10": {"tag_id": "tag_10", "type": "OPTICAL_LENS", "name": "Lens"},
                    "tag_99": {"tag_id": "tag_99", "type": "OPTICAL_MIRROR", "name": "Mir"},
                },
            }
        ),
        encoding="utf-8",
    )
    (data / "inventory.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "entries": {
                    "tag_10": {"placement": "table", "localize": True},
                },
            }
        ),
        encoding="utf-8",
    )


class ResolveEdgeCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="edge_catalog_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_teaching_disk_beside_lab_view(self) -> None:
        pkg = self.tmp / "mock_backend"
        lab_view = pkg / "lab_view"
        lab_view.mkdir(parents=True)
        _write_edge_data(pkg / "cloudlabs_edge")
        rt = SimpleNamespace(
            backend_id="mock.default",
            spec=SimpleNamespace(
                lab_view_path=str(lab_view),
                edge=SimpleNamespace(configured=False),
            ),
            paths=SimpleNamespace(
                root_dir=str(lab_view),
                component_library_json=str(
                    pkg / "cloudlabs_edge" / "data" / "library.json"
                ),
            ),
        )
        cat = resolve_edge_catalog(rt)
        self.assertEqual(cat.source, "edge_data_disk")
        self.assertEqual(cat.active_tag_ids(), ["tag_10"])
        self.assertEqual(len(cat.active_rows()), 1)
        self.assertEqual(len(cat.all_library_rows()), 2)

    def test_http_edge_preferred(self) -> None:
        from lab_model.execution.edge.endpoint import EdgeEndpointConfig

        lib = {"schema_version": 1, "components": {"tag_1": {"tag_id": "tag_1"}}}
        inv = {"schema_version": 1, "entries": {"tag_1": {"placement": "table"}}}
        edge = EdgeEndpointConfig(base_url="http://127.0.0.1:8200")
        rt = SimpleNamespace(
            backend_id="real.default",
            spec=SimpleNamespace(lab_view_path="", edge=edge),
            paths=SimpleNamespace(root_dir="", component_library_json=""),
        )
        with patch(
            "lab_model.coordinator.catalog.resolve_edge_catalog.HttpEdgeClient"
        ) as cls:
            client = MagicMock()
            client.get_library.return_value = lib
            client.get_inventory.return_value = inv
            cls.return_value = client
            cat = resolve_edge_catalog(rt)
        self.assertEqual(cat.source, "edge_http")
        self.assertEqual(cat.active_tag_ids(), ["tag_1"])

    def test_http_unavailable_fails_loud(self) -> None:
        from lab_model.execution.edge.endpoint import EdgeEndpointConfig

        edge = EdgeEndpointConfig(base_url="http://127.0.0.1:8200")
        rt = SimpleNamespace(
            backend_id="real.default",
            spec=SimpleNamespace(lab_view_path="", edge=edge),
            paths=SimpleNamespace(root_dir="", component_library_json=""),
        )
        with patch(
            "lab_model.coordinator.catalog.resolve_edge_catalog.HttpEdgeClient"
        ) as cls:
            client = MagicMock()
            client.get_library.return_value = None
            client.get_inventory.return_value = None
            cls.return_value = client
            with self.assertRaises(EdgeCatalogUnavailable):
                resolve_edge_catalog(rt)

    def test_project_mock_uses_edge_data_not_coordinator(self) -> None:
        from lab_model.coordinator.backends.registry import BackendRegistry

        project = Path(__file__).resolve().parents[2]
        os.environ.pop("CLOUDLABS_BACKENDS_CONFIG", None)
        reg = BackendRegistry.from_project(str(project))
        mock_rt = reg.get_runtime("mock.default", init=False)
        cat = resolve_edge_catalog(mock_rt)
        self.assertEqual(cat.source, "edge_data_disk")
        self.assertTrue(cat.active_tag_ids())
        # Must not be reading coordinator_data as library SoT.
        self.assertNotIn("coordinator_data", cat.source)

    def test_catalog_map_from_resolved_keys_by_tag_id(self) -> None:
        from lab_model.coordinator.catalog.resolve_edge_catalog import ResolvedEdgeCatalog
        from lab_model.execution.optimization.objective_measurements import (
            is_camera_capable_row,
        )

        cat = ResolvedEdgeCatalog(
            backend_id="real.default",
            source="test",
            library={
                "components": {
                    "tag_22": {
                        "id": "cam_gripper_1",
                        "tag_id": "tag_22",
                        "type": "OPTICAL_CAMERA",
                    }
                }
            },
            inventory={
                "entries": {"tag_22": {"placement": "table", "localize": True}}
            },
        )
        cmap = catalog_map_from_resolved(cat)
        self.assertIn("tag_22", cmap)
        self.assertNotIn("cam_gripper_1", cmap)
        self.assertTrue(is_camera_capable_row(cmap["tag_22"]))


if __name__ == "__main__":
    unittest.main()
