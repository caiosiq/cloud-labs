"""Phase 1: per-backend lab_view isolation and ControlManager cache keys."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path


def _seed_lab_view(root: Path) -> None:
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
        self.real_lv = self.tmp / "real_lv"
        _seed_lab_view(self.mock_lv)
        _seed_lab_view(self.real_lv)
        cfg = {
            "schema_version": 1,
            "backends": [
                {
                    "backend_id": "mock.default",
                    "label": "Mock",
                    "lab_view_path": str(self.mock_lv),
                    "enabled": True,
                },
                {
                    "backend_id": "real.default",
                    "label": "Real",
                    "lab_view_path": str(self.real_lv),
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

    def test_distinct_control_dirs_and_repos(self) -> None:
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

        create_control_repo(mock_rt.control_dir(), "laser-cavity")
        create_control_repo(real_rt.control_dir(), "default")

        mock_ids = {r["repo_id"] for r in list_control_repos(mock_rt.control_dir())}
        real_ids = {r["repo_id"] for r in list_control_repos(real_rt.control_dir())}
        self.assertEqual(mock_ids, {"laser-cavity"})
        self.assertEqual(real_ids, {"default"})
        self.assertEqual(set(mock_rt.list_control_repo_ids()), {"laser-cavity"})
        self.assertEqual(set(real_rt.list_control_repo_ids()), {"default"})

        # Same repo_id string on two backends must not share ControlManager state.
        create_control_repo(mock_rt.control_dir(), "shared-name")
        create_control_repo(real_rt.control_dir(), "shared-name")
        mock_mgr = ControlManager(mock_rt.control_dir(), "shared-name")
        real_mgr = ControlManager(real_rt.control_dir(), "shared-name")
        mock_rt.control_managers["shared-name"] = mock_mgr
        real_rt.control_managers["shared-name"] = real_mgr
        self.assertIsNot(mock_rt.control_managers["shared-name"], real_rt.control_managers["shared-name"])
        self.assertNotEqual(mock_mgr.repo_dir, real_mgr.repo_dir)

    def test_warn_shared_lab_view_path(self) -> None:
        from lab_model.coordinator.backends.registry import (
            BackendSpec,
            warn_shared_lab_view_paths,
        )

        shared = str(self.mock_lv)
        specs = [
            BackendSpec("a", "A", shared),
            BackendSpec("b", "B", shared),
        ]
        msgs = warn_shared_lab_view_paths(str(self.tmp), specs, strict=False)
        self.assertEqual(len(msgs), 1)
        self.assertIn("shared by", msgs[0])

        with self.assertRaises(ValueError):
            warn_shared_lab_view_paths(str(self.tmp), specs, strict=True)

    def test_project_backends_json_isolates_real(self) -> None:
        """Repo schemas/backends.json: real.default must not share mock lab_view."""
        from lab_model.coordinator.backends.registry import (
            BackendRegistry,
            warn_shared_lab_view_paths,
        )

        project = Path(__file__).resolve().parents[2]
        os.environ.pop("CLOUDLABS_BACKENDS_CONFIG", None)
        reg = BackendRegistry.from_project(str(project))
        mock_rt = reg.get_runtime("mock.default", init=False)
        real_rt = reg.get_runtime("real.default", init=False)
        self.assertEqual(mock_rt.availability, "ready")
        self.assertEqual(real_rt.availability, "ready")
        self.assertTrue(mock_rt.paths.root_dir)
        self.assertTrue(real_rt.paths.root_dir)
        self.assertNotEqual(
            os.path.normcase(mock_rt.paths.root_dir),
            os.path.normcase(real_rt.paths.root_dir),
        )
        self.assertIn("real.default", real_rt.spec.lab_view_path.replace("\\", "/"))
        msgs = warn_shared_lab_view_paths(
            str(project),
            reg.list_specs(),
            strict=False,
        )
        self.assertEqual(msgs, [])


if __name__ == "__main__":
    unittest.main()
