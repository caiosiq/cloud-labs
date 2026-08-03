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

    def test_session_reconciliation_skips_external_edge(self) -> None:
        with patch.object(main, "_edge_client_for", return_value=self.http_client):
            result = main._session_reconciliation_offers_dict()

        self.assertFalse(result["enabled"])
        self.assertEqual(result["skipped_reason"], "external_edge")

    def test_pose_preview_skips_external_edge_without_local_lab(self) -> None:
        with patch.object(main, "_edge_client_for", return_value=self.http_client):
            result = main._pose_refresh_offers_dict()

        self.assertFalse(result["supported"])
        self.assertEqual(result["skipped_reason"], "external_edge")


if __name__ == "__main__":
    unittest.main()
