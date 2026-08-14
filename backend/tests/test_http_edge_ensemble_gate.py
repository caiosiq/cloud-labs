"""HTTP ensemble host must surface edge runtime_sync for OPTIMIZE gates."""
from __future__ import annotations

import copy
import unittest
from typing import Any, Dict
from unittest import mock

from lab_model.coordinator.lab_initialization import (
    LabNotInitializedError,
    ensure_action_allowed,
)
from lab_model.execution.edge.ensemble_host import HttpEdgeEnsembleHost


class _FakeStore:
    def __init__(self, state: Dict[str, Any]) -> None:
        self.runtime = mock.Mock()
        self.runtime.state = state

    def snapshot(self) -> Dict[str, Any]:
        return copy.deepcopy(self.runtime.state)


class HttpEdgeEnsembleGateTests(unittest.TestCase):
    def test_get_lab_state_merges_edge_runtime_sync(self) -> None:
        working = {
            "system_status": "IDLE",
            "components": {"tag_22": {"id": "tag_22", "statecontrol": {"tunables": {}}}},
        }
        # Working store intentionally has no runtime_sync (edge-owned).
        self.assertNotIn("runtime_sync", working)
        host = HttpEdgeEnsembleHost(
            backend_id="real.default",
            base_url="http://edge.test",
            state_store=_FakeStore(working),
            edge_state_fetch=lambda: {
                "runtime_sync": {"status": "ready", "errors": []},
                "components": {},
            },
        )
        state = host.get_lab_state()
        self.assertEqual(state["runtime_sync"]["status"], "ready")
        self.assertEqual(state["active_backend_id"], "real.default")
        ensure_action_allowed("real.default", "OPTIMIZE", state)

    def test_get_lab_state_without_edge_still_missing_sync(self) -> None:
        working = {"system_status": "IDLE", "components": {}}
        host = HttpEdgeEnsembleHost(
            backend_id="real.default",
            base_url="",
            state_store=_FakeStore(working),
            edge_state_fetch=lambda: None,
        )
        state = host.get_lab_state()
        with self.assertRaises(LabNotInitializedError):
            ensure_action_allowed("real.default", "OPTIMIZE", state)


if __name__ == "__main__":
    unittest.main()
