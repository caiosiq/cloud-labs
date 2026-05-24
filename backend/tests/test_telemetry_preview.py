"""JPEGPoll preview route works with live_feed.stream-only catalog rows."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from lab_model.catalog.schema import live_feed_channel, resolve_telemetry_stream_backend


def _load_mock_catalog_row(tag_id: str) -> dict:
    lib = (
        Path(__file__).resolve().parents[1]
        / "lab_communicator"
        / "mock"
        / "lab_view"
        / "component_library.json"
    )
    with open(lib, encoding="utf-8") as f:
        data = json.load(f)
    row = (data.get("components") or {}).get(tag_id)
    if not isinstance(row, dict):
        raise KeyError(tag_id)
    return row


class TestTelemetryPreviewCatalog(unittest.TestCase):
    def test_tag_22_declares_stream_not_preview_channel(self) -> None:
        row = _load_mock_catalog_row("tag_22")
        stream = live_feed_channel(row, "stream")
        self.assertIsNotNone(stream)
        assert stream is not None
        self.assertEqual(stream.get("widget"), "JPEGPoll")
        self.assertIn("telemetry/preview", str(stream.get("url")))
        self.assertIsNone(live_feed_channel(row, "preview"))
        self.assertEqual(resolve_telemetry_stream_backend(row), "table_cam")


if __name__ == "__main__":
    unittest.main()
