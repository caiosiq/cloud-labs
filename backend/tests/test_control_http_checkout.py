"""Control soft-checkout must not require an in-process communicator."""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import main
from lab_model.coordinator.catalog.resolve_edge_catalog import ResolvedEdgeCatalog
from lab_model.coordinator.state.control_manager import ControlManager
from lab_model.coordinator.state.lab_state_store import LabStateStore
from lab_model.language.domain.holding import SYSTEM_STATUS_IDLE


def _empty_runtime() -> dict:
    return {
        "system_status": SYSTEM_STATUS_IDLE,
        "holding": {
            "tag_id": None,
            "nominal_pose": None,
            "requires_operator_confirm": False,
        },
        "components": {},
    }


class SoftCheckoutHttpEdgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_soft_checkout_uses_store_when_lab_missing(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="ctrl_soft_"))
        self.addCleanup(shutil.rmtree, tmp, True)
        control_dir = tmp / "control"
        control_dir.mkdir(parents=True)

        store = LabStateStore("real.default", str(tmp / "lab_state.json"))
        store.replace_state(_empty_runtime(), persist=True)

        mgr = ControlManager(str(control_dir), "testing-experiments")
        commit = mgr.commit_from_runtime(
            _empty_runtime(),
            message="seed",
            branch="main",
        )
        cfg_id = str(commit["id"])

        broken_lab = MagicMock()
        broken_lab.get_lab_state.side_effect = AttributeError(
            "communicator not initialized for 'real.default'"
        )

        payload = main.ControlCheckoutBody(
            configuration_id=cfg_id,
            mode="soft",
            preview=False,
            finalize=False,
        )

        with patch.object(main, "lab", broken_lab), patch.object(
            main, "_get_control_manager", return_value=mgr
        ), patch.object(
            main, "_control_runtime_state", return_value=store.snapshot()
        ), patch.object(
            main, "_assert_lab_idle_for_control"
        ), patch.object(
            main,
            "_control_catalog_context",
            return_value={
                "catalog_hash": "abc",
                "catalog_tag_ids": [],
                "library_tag_ids": [],
                "source": "edge_http",
            },
        ):
            out = await main.control_checkout(
                "testing-experiments", payload, request=MagicMock()
            )

        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["mode"], "soft")
        self.assertEqual(out["configuration_id"], cfg_id)
        broken_lab.get_lab_state.assert_not_called()


class ControlCatalogContextTests(unittest.TestCase):
    def test_prefers_edge_catalog_over_active_catalog_file(self) -> None:
        cat = ResolvedEdgeCatalog(
            backend_id="real.default",
            source="edge_http",
            library={
                "schema_version": 1,
                "components": {
                    "tag_8": {"tag_id": "tag_8", "type": "OPTICAL_MIRROR"},
                },
            },
            inventory={
                "schema_version": 1,
                "entries": {"tag_8": {"placement": "table"}},
            },
        )
        with patch(
            "lab_model.coordinator.catalog.resolve_edge_catalog.resolve_edge_catalog",
            return_value=cat,
        ), patch.object(main, "_runtime_for_active", return_value=MagicMock()):
            ctx = main._control_catalog_context()
        self.assertEqual(ctx["source"], "edge_http")
        self.assertEqual(ctx["catalog_tag_ids"], ["tag_8"])
        self.assertIn("tag_8", ctx["library_tag_ids"])
        self.assertTrue(ctx["catalog_hash"])


if __name__ == "__main__":
    unittest.main()
