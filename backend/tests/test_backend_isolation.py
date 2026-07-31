"""Per-backend coordinator_data isolation and ControlManager cache keys."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path


def _seed_edge_lab_view(root: Path) -> None:
    """Minimal *edge* lab_view (layout/library/motors) — not coordinator store."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "control").mkdir(exist_ok=True)
    (root / "recipes").mkdir(exist_ok=True)
    (root / "catalog_store").mkdir(exist_ok=True)
    layout = {
        "version": 1,
        "lab_bounds_mm": {
            "x_min": -100,
            "x_max": 100,
            "y_min": -100,
            "y_max": 100,
        },
        "danger_zone": {"radius_mm": 40, "padding_mm": 5},
        "frame_safety": {
            "clearance_mm": 10,
            "minimum_component_footprint_mm": {"width": 40, "height": 40},
        },
        "manual_motion_workspace": {"corner_cutoff_mm": 80},
        "storage": {
            "rule": "negative_xy",
            "grid_nx": 2,
            "grid_ny": 2,
            "extent_from_origin_mm": {"width_mm": 50, "height_mm": 50},
        },
        "breadboard": {"grid_spacing_mm": 25, "origin_offset_mm": {"x": 0, "y": 0}},
    }
    (root / "layout.json").write_text(json.dumps(layout), encoding="utf-8")
    (root / "laser_lines.json").write_text(
        json.dumps({"version": 1, "lines": [], "snap_line_id": None}),
        encoding="utf-8",
    )
    (root / "component_library.json").write_text(
        json.dumps({"schema_version": 1, "components": {}}),
        encoding="utf-8",
    )
    (root / "active_catalog.json").write_text(
        json.dumps({"version": 1, "tag_ids": []}),
        encoding="utf-8",
    )
    (root / "motor_rotations.json").write_text("{}", encoding="utf-8")
    (root / "lab_manifest.json").write_text(
        json.dumps(
            {
                "version": 1,
                "communicator": "mock",
                "session_checkpoint": False,
            }
        ),
        encoding="utf-8",
    )
    (root / "catalog_store" / "pins.json").write_text(
        json.dumps({"version": 1, "pins": {}}),
        encoding="utf-8",
    )


class BackendIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="backend_isolation_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.mock_lv = self.tmp / "mock_lv"
        _seed_edge_lab_view(self.mock_lv)
        cfg = {
            "schema_version": 1,
            "backends": [
                {
                    "backend_id": "mock.default",
                    "label": "Mock",
                    "lab_view_path": str(self.mock_lv),
                    "coordinator_data_path": str(self.tmp / "coord_mock"),
                    "enabled": True,
                },
                {
                    "backend_id": "real.default",
                    "label": "Real",
                    "coordinator_data_path": str(self.tmp / "coord_real"),
                    "communicator": "real",
                    "lab_mode": "REAL",
                    "enabled": True,
                    "edge": {"base_url": "http://127.0.0.1:8200"},
                },
            ],
        }
        self.cfg_path = self.tmp / "backends.json"
        self.cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
        os.environ["CLOUDLABS_BACKENDS_CONFIG"] = str(self.cfg_path)
        self.addCleanup(os.environ.pop, "CLOUDLABS_BACKENDS_CONFIG", None)
        os.environ.pop("CLOUDLABS_STRICT_LAB_VIEW", None)

    def test_distinct_coordinator_control_dirs(self) -> None:
        from lab_model.coordinator.backends.registry import BackendRegistry
        from lab_model.coordinator.state.control_manager import (
            ControlManager,
            create_control_repo,
            list_control_repos,
        )

        reg = BackendRegistry.from_project(str(self.tmp))
        mock_rt = reg.get_runtime("mock.default", init=False)
        real_rt = reg.get_runtime("real.default", init=False)
        self.assertNotEqual(mock_rt.control_dir(), real_rt.control_dir())
        self.assertIn("coord_mock", mock_rt.control_dir().replace("\\", "/"))
        self.assertIn("coord_real", real_rt.control_dir().replace("\\", "/"))
        # Coordinator owns lab_state path (not the edge lab_view for mock).
        self.assertIn("coord_mock", mock_rt.paths.lab_state_json.replace("\\", "/"))

        create_control_repo(mock_rt.control_dir(), "laser-cavity")
        create_control_repo(real_rt.control_dir(), "default")

        mock_ids = {r["repo_id"] for r in list_control_repos(mock_rt.control_dir())}
        real_ids = {r["repo_id"] for r in list_control_repos(real_rt.control_dir())}
        self.assertEqual(mock_ids, {"laser-cavity"})
        self.assertEqual(real_ids, {"default"})

        create_control_repo(mock_rt.control_dir(), "shared-name")
        create_control_repo(real_rt.control_dir(), "shared-name")
        mock_mgr = ControlManager(mock_rt.control_dir(), "shared-name")
        real_mgr = ControlManager(real_rt.control_dir(), "shared-name")
        mock_rt.control_managers["shared-name"] = mock_mgr
        real_rt.control_managers["shared-name"] = real_mgr
        self.assertIsNot(
            mock_rt.control_managers["shared-name"],
            real_rt.control_managers["shared-name"],
        )
        self.assertNotEqual(mock_mgr.repo_dir, real_mgr.repo_dir)

    def test_real_probes_without_local_lab_view(self) -> None:
        from lab_model.coordinator.backends.registry import BackendRegistry

        reg = BackendRegistry.from_project(str(self.tmp))
        real_rt = reg.get_runtime("real.default", init=False)
        self.assertEqual(real_rt.availability, "ready")
        self.assertFalse(real_rt.spec.lab_view_path)
        self.assertTrue(os.path.isfile(real_rt.paths.lab_state_json))
        self.assertTrue(os.path.isdir(real_rt.control_dir()))

    def test_warn_shared_lab_view_path(self) -> None:
        from lab_model.coordinator.backends.registry import (
            BackendSpec,
            warn_shared_lab_view_paths,
        )

        shared = str(self.mock_lv)
        specs = [
            BackendSpec("a", "A", shared, "coord_a"),
            BackendSpec("b", "B", shared, "coord_b"),
        ]
        msgs = warn_shared_lab_view_paths(str(self.tmp), specs, strict=False)
        self.assertEqual(len(msgs), 1)
        self.assertIn("shared by", msgs[0])

        with self.assertRaises(ValueError):
            warn_shared_lab_view_paths(str(self.tmp), specs, strict=True)

    def test_project_backends_json_isolates_coordinator_data(self) -> None:
        from lab_model.coordinator.backends.registry import (
            BackendRegistry,
            warn_shared_coordinator_data_paths,
            warn_shared_lab_view_paths,
        )

        project = Path(__file__).resolve().parents[2]
        os.environ.pop("CLOUDLABS_BACKENDS_CONFIG", None)
        reg = BackendRegistry.from_project(str(project))
        mock_rt = reg.get_runtime("mock.default", init=False)
        real_rt = reg.get_runtime("real.default", init=False)
        self.assertEqual(mock_rt.availability, "ready")
        self.assertEqual(real_rt.availability, "ready")
        self.assertNotEqual(
            os.path.normcase(mock_rt.control_dir()),
            os.path.normcase(real_rt.control_dir()),
        )
        self.assertIn("coordinator_data", real_rt.control_dir().replace("\\", "/"))
        self.assertFalse(real_rt.spec.lab_view_path)
        self.assertEqual(
            warn_shared_lab_view_paths(str(project), reg.list_specs(), strict=False),
            [],
        )
        self.assertEqual(
            warn_shared_coordinator_data_paths(
                str(project), reg.list_specs(), strict=False
            ),
            [],
        )


class CoordinatorDataEnsureTests(unittest.TestCase):
    def test_ensure_creates_thin_store(self) -> None:
        from lab_model.coordinator.backends.coordinator_data import ensure_coordinator_data

        tmp = Path(tempfile.mkdtemp(prefix="coord_ensure_"))
        self.addCleanup(shutil.rmtree, tmp, True)
        paths = ensure_coordinator_data(
            str(tmp),
            "real.default",
            coordinator_data_path=str(tmp / "coordinator_data" / "real.default"),
        )
        self.assertTrue(paths.created)
        self.assertTrue(os.path.isfile(paths.lab_state_json))
        self.assertTrue(os.path.isdir(paths.control_dir))
        # Must not invent edge catalogs.
        root = Path(paths.root_dir)
        self.assertFalse((root / "component_library.json").exists())
        self.assertFalse((root / "layout.json").exists())
        self.assertFalse((root / "motor_rotations.json").exists())


if __name__ == "__main__":
    unittest.main()
