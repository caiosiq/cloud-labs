"""External HTTP edges must not require an in-process lab communicator."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import main
from lab_model.execution.edge.client import EdgeTransport


class HttpEdgeRefreshRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.http_client = SimpleNamespace(transport=EdgeTransport.HTTP)

    def test_session_reconciliation_routes_external_edge_to_coordinator(self) -> None:
        """HTTP edges no longer hard-skip; coordinator store owns checkpoints."""
        fake_rt = SimpleNamespace(
            backend_id="real.default",
            paths=SimpleNamespace(session_checkpoint_json=""),
            manifest=SimpleNamespace(
                session_checkpoint=True,
                reconciliation_position_mm=8.0,
                reconciliation_yaw_deg=10.0,
                as_dict=lambda: {
                    "session_checkpoint": True,
                    "session_reconciliation": {
                        "position_mm": 8,
                        "yaw_deg": 10,
                        "stale_warning_hours": 168,
                    },
                },
            ),
            lab_mode="REAL",
            lab=None,
            lab_state_store=None,
        )
        with patch.object(main, "_edge_client_for", return_value=self.http_client):
            with patch.object(main, "_runtime_for_active", return_value=fake_rt):
                result = main._session_reconciliation_offers_dict()

        self.assertNotEqual(result.get("skipped_reason"), "external_edge")
        self.assertEqual(result.get("skipped_reason"), "lab_unavailable")
        self.assertFalse(result["enabled"])

    def test_pose_preview_uses_selection_offers_for_external_edge(self) -> None:
        """HTTP edges get checkbox selection offers (no dry-run scan preview)."""
        with patch.object(main, "_edge_client_for", return_value=self.http_client):
            result = main._pose_refresh_offers_dict()

        self.assertTrue(result["supported"])
        self.assertTrue(result.get("selection_only"))
        self.assertIsNone(result.get("skipped_reason"))


if __name__ == "__main__":
    unittest.main()
