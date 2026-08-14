"""Guide APIs must work on HTTP-edge backends without an in-process communicator."""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import main
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
        "alignment_guides": [],
    }


class GuidesHttpEdgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_add_guide_uses_store_when_communicator_missing(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="guides_http_"))
        self.addCleanup(shutil.rmtree, tmp, True)

        store = LabStateStore("real.default", str(tmp / "lab_state.json"))
        store.replace_state(_empty_runtime(), persist=True)

        broken_lab = MagicMock()
        broken_lab.get_lab_state.side_effect = AttributeError(
            "communicator not initialized for 'real.default'"
        )

        payload = {
            "p1": {"x": 0.0, "y": 0.0},
            "p2": {"x": 100.0, "y": 0.0},
        }

        with patch.object(main, "lab", broken_lab), patch.object(
            main, "_control_runtime_state", side_effect=lambda: store.snapshot()
        ), patch.object(
            main, "_lab_runtime_manager", return_value=store.runtime
        ), patch.object(
            main, "_persist_lab_state_if_host"
        ):
            out = await main.add_guide(payload)

        self.assertIn("guide", out)
        self.assertEqual(out["guide"]["p1"]["x"], 0.0)
        self.assertEqual(out["guide"]["p2"]["x"], 100.0)
        self.assertEqual(len(out["guides"]), 1)
        self.assertEqual(len(store.snapshot().get("alignment_guides") or []), 1)
        broken_lab.get_lab_state.assert_not_called()
