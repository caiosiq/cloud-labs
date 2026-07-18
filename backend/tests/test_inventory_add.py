"""Tests for Phase B inventory add + catalog merge."""

from __future__ import annotations

import asyncio
import copy
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from lab_model.coordinator.backends.lab_view_config import bootstrap_lab_view, get_lab_view_paths
from lab_model.coordinator.catalog.active_catalog_store import ensure_tag_in_active_catalog
from lab_model.coordinator.catalog.bundle import library_by_tag, merged_catalog_rows
from lab_model.language.domain.component import is_off_table, presence_of
from lab_model.coordinator.state.runtime_manager import MutationKind

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_MOCK_LAB_VIEW = _PROJECT_ROOT / "mock_edge" / "lab_view"


class InventoryAddTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ["LAB_VIEW_PATH"] = str(_MOCK_LAB_VIEW)
        bootstrap_lab_view(str(_PROJECT_ROOT))
        from lab_model.language.domain import motor_rotation_store as motor_rot

        motor_rot.configure(get_lab_view_paths().motor_rotations_json)
        with open(_MOCK_LAB_VIEW / "lab_state.json", "r", encoding="utf-8") as handle:
            cls.fixture_runtime = json.load(handle)

    def test_library_rows_include_off_catalog_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lab_view = Path(tmp) / "lab_view"
            shutil.copytree(_MOCK_LAB_VIEW, lab_view)
            active_path = lab_view / "active_catalog.json"
            with open(active_path, "r", encoding="utf-8") as handle:
                active = json.load(handle)
            active["tag_ids"] = [tid for tid in active["tag_ids"] if tid != "tag_10"]
            with open(active_path, "w", encoding="utf-8") as handle:
                json.dump(active, handle)

            os.environ["LAB_VIEW_PATH"] = str(lab_view)
            bootstrap_lab_view(str(_PROJECT_ROOT))

            by_tag = library_by_tag()
            self.assertIn("tag_10", by_tag)
            self.assertEqual(by_tag["tag_10"].get("name"), "Beam Splitter (BS)")

            rows = merged_catalog_rows(runtime_tag_ids=["tag_50"])
            merged_ids = {row["tag_id"] for row in rows}
            self.assertNotIn("tag_10", merged_ids)

    def test_merged_catalog_includes_runtime_tags(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lab_view = Path(tmp) / "lab_view"
            shutil.copytree(_MOCK_LAB_VIEW, lab_view)
            active_path = lab_view / "active_catalog.json"
            with open(active_path, "r", encoding="utf-8") as handle:
                active = json.load(handle)
            active["tag_ids"] = ["tag_9"]
            with open(active_path, "w", encoding="utf-8") as handle:
                json.dump(active, handle)

            os.environ["LAB_VIEW_PATH"] = str(lab_view)
            bootstrap_lab_view(str(_PROJECT_ROOT))

            rows = merged_catalog_rows(runtime_tag_ids=["tag_22", "tag_21"])
            tag_ids = {row["tag_id"] for row in rows}
            self.assertIn("tag_9", tag_ids)
            self.assertIn("tag_22", tag_ids)
            self.assertIn("tag_21", tag_ids)

    def test_remove_tag_from_active_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lab_view = Path(tmp) / "lab_view"
            shutil.copytree(_MOCK_LAB_VIEW, lab_view)
            os.environ["LAB_VIEW_PATH"] = str(lab_view)
            bootstrap_lab_view(str(_PROJECT_ROOT))
            from lab_model.coordinator.backends.lab_view_config import get_lab_view_paths
            from lab_model.coordinator.catalog.active_catalog_store import (
                ensure_tag_in_active_catalog,
                list_active_catalog_tags,
                remove_tag_from_active_catalog,
            )

            paths = get_lab_view_paths()
            ensure_tag_in_active_catalog("tag_77", paths=paths)
            self.assertIn("tag_77", list_active_catalog_tags(paths))
            remove_tag_from_active_catalog("tag_77", paths=paths)
            self.assertNotIn("tag_77", list_active_catalog_tags(paths))

    def test_track_component_places_off_table_on_breadboard(self) -> None:
        import asyncio

        from mock_edge.host.communicator import MockLabCommunicator
        from lab_model.coordinator.backends.lab_view_config import get_lab_view_paths
        from lab_model.language.domain import motor_rotation_store as motor_rot
        from lab_model.coordinator.catalog.active_catalog_store import (
            list_active_catalog_tags,
            remove_tag_from_active_catalog,
        )
        from lab_model.language.domain.component import is_off_table, is_on_table, presence_of

        with tempfile.TemporaryDirectory() as tmp:
            lab_view = Path(tmp) / "lab_view"
            shutil.copytree(_MOCK_LAB_VIEW, lab_view)
            state_path = lab_view / "lab_state.json"
            with open(state_path, "r", encoding="utf-8") as handle:
                state = json.load(handle)
            state["components"]["tag_19"]["statecontrol"]["tunables"]["presence"] = "off_table"
            with open(state_path, "w", encoding="utf-8") as handle:
                json.dump(state, handle)

            os.environ["LAB_VIEW_PATH"] = str(lab_view)
            bootstrap_lab_view(str(_PROJECT_ROOT))
            paths = get_lab_view_paths()
            motor_rot.configure(paths.motor_rotations_json)
            remove_tag_from_active_catalog("tag_19", paths=paths)

            lab = MockLabCommunicator()
            entry = lab.get_lab_state()["components"]["tag_19"]
            self.assertTrue(is_off_table(entry))

            async def _run() -> dict:
                return await lab.track_component({"tag_id": "tag_19"})

            result = asyncio.run(_run())
            self.assertTrue(result["tracked"])
            self.assertEqual(result["action"], "reactivated_on_table")
            after = lab.get_lab_state()["components"]["tag_19"]
            self.assertFalse(is_off_table(after))
            self.assertTrue(is_on_table(after))
            self.assertEqual(presence_of(after), "breadboard")
            self.assertIn("tag_19", list_active_catalog_tags(paths))

    def test_track_component_places_library_part_on_breadboard(self) -> None:
        import asyncio

        from mock_edge.host.communicator import MockLabCommunicator
        from lab_model.coordinator.backends.lab_view_config import get_lab_view_paths
        from lab_model.language.domain import motor_rotation_store as motor_rot
        from lab_model.language.domain.component import is_on_table, presence_of

        with tempfile.TemporaryDirectory() as tmp:
            lab_view = Path(tmp) / "lab_view"
            shutil.copytree(_MOCK_LAB_VIEW, lab_view)
            state_path = lab_view / "lab_state.json"
            with open(state_path, "r", encoding="utf-8") as handle:
                state = json.load(handle)
            state.get("components", {}).pop("tag_11", None)
            with open(state_path, "w", encoding="utf-8") as handle:
                json.dump(state, handle)

            active_path = lab_view / "active_catalog.json"
            with open(active_path, "r", encoding="utf-8") as handle:
                active = json.load(handle)
            active["tag_ids"] = [tid for tid in active["tag_ids"] if tid != "tag_11"]
            with open(active_path, "w", encoding="utf-8") as handle:
                json.dump(active, handle)

            os.environ["LAB_VIEW_PATH"] = str(lab_view)
            bootstrap_lab_view(str(_PROJECT_ROOT))
            motor_rot.configure(get_lab_view_paths().motor_rotations_json)

            lab = MockLabCommunicator()
            self.assertNotIn("tag_11", lab.get_lab_state()["components"])

            async def _run() -> dict:
                return await lab.track_component({"tag_id": "tag_11"})

            result = asyncio.run(_run())
            self.assertTrue(result["tracked"])
            self.assertEqual(result["action"], "placed_on_table")
            entry = lab.get_lab_state()["components"]["tag_11"]
            self.assertTrue(is_on_table(entry))
            self.assertEqual(presence_of(entry), "breadboard")

    def test_untrack_component_removes_runtime_row(self) -> None:
        import asyncio

        from mock_edge.host.communicator import MockLabCommunicator
        from lab_model.coordinator.backends.lab_view_config import get_lab_view_paths
        from lab_model.language.domain import motor_rotation_store as motor_rot
        from lab_model.coordinator.catalog.active_catalog_store import list_active_catalog_tags

        with tempfile.TemporaryDirectory() as tmp:
            lab_view = Path(tmp) / "lab_view"
            shutil.copytree(_MOCK_LAB_VIEW, lab_view)
            os.environ["LAB_VIEW_PATH"] = str(lab_view)
            bootstrap_lab_view(str(_PROJECT_ROOT))
            paths = get_lab_view_paths()
            motor_rot.configure(paths.motor_rotations_json)

            lab = MockLabCommunicator()
            self.assertIn("tag_50", lab.get_lab_state()["components"])
            self.assertIn("tag_50", list_active_catalog_tags(paths))

            async def _run() -> dict:
                return await lab.untrack_component("tag_50")

            result = asyncio.run(_run())
            self.assertFalse(result["tracked"])
            self.assertTrue(result["removed_from_runtime"])
            self.assertNotIn("tag_50", lab.get_lab_state()["components"])
            self.assertNotIn("tag_50", list_active_catalog_tags(paths))

    def test_ensure_tag_in_active_catalog_appends(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lab_view = Path(tmp) / "lab_view"
            shutil.copytree(_MOCK_LAB_VIEW, lab_view)
            os.environ["LAB_VIEW_PATH"] = str(lab_view)
            bootstrap_lab_view(str(_PROJECT_ROOT))
            paths = get_lab_view_paths()

            updated = ensure_tag_in_active_catalog("tag_77", paths=paths)
            self.assertTrue(updated)
            updated_again = ensure_tag_in_active_catalog("tag_77", paths=paths)
            self.assertFalse(updated_again)

            with open(paths.active_catalog_json, "r", encoding="utf-8") as handle:
                active = json.load(handle)
            self.assertIn("tag_77", active["tag_ids"])

    def _write_temp_lab_view(self, tmp: str, state: dict | None = None) -> Path:
        lab_view = Path(tmp) / "lab_view"
        shutil.copytree(_MOCK_LAB_VIEW, lab_view)
        if state is not None:
            with open(lab_view / "lab_state.json", "w", encoding="utf-8") as handle:
                json.dump(state, handle, indent=2)
                handle.write("\n")
        os.environ["LAB_VIEW_PATH"] = str(lab_view)
        bootstrap_lab_view(str(_PROJECT_ROOT))
        return lab_view

    def test_add_component_from_inventory_reactivates_off_table(self) -> None:
        from mock_edge.host.communicator import MockLabCommunicator

        with tempfile.TemporaryDirectory() as tmp:
            state = copy.deepcopy(self.fixture_runtime)
            tag = "tag_22"
            state["components"][tag]["statecontrol"]["tunables"]["presence"] = "off_table"
            self._write_temp_lab_view(tmp, state)

            lab = MockLabCommunicator()
            self.assertTrue(is_off_table(lab.get_lab_state()["components"][tag]))

            async def _run() -> dict:
                return await lab.add_component_from_inventory({"tag_id": tag})

            result = asyncio.run(_run())
            self.assertEqual(result["tag_id"], tag)
            self.assertEqual(result["presence"], "breadboard")

            entry = lab.get_lab_state()["components"][tag]
            self.assertFalse(is_off_table(entry))
            self.assertEqual(presence_of(entry), "breadboard")

            log = lab._lab_runtime_manager.mutation_log()
            self.assertTrue(any(r.source == f"inventory_add:{tag}" for r in log))
            self.assertTrue(any(r.kind == MutationKind.ADMINISTRATIVE_LOAD for r in log))

    def test_add_component_from_inventory_inserts_library_tag(self) -> None:
        from mock_edge.host.communicator import MockLabCommunicator

        with tempfile.TemporaryDirectory() as tmp:
            state = copy.deepcopy(self.fixture_runtime)
            tag = "tag_18"
            state["components"].pop(tag, None)
            self._write_temp_lab_view(tmp, state)

            lab = MockLabCommunicator()
            self.assertNotIn(tag, lab.get_lab_state()["components"])

            async def _run() -> dict:
                return await lab.add_component_from_inventory({"tag_id": tag})

            result = asyncio.run(_run())
            self.assertEqual(result["tag_id"], tag)
            self.assertIn(tag, lab.get_lab_state()["components"])
            self.assertEqual(lab.get_lab_state()["components"][tag]["type"], "OPTICAL_MIRROR")


if __name__ == "__main__":
    unittest.main()
