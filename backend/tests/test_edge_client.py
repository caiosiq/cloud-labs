"""Phase 3 EdgeClient resolver + HTTP contract mapping."""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import MagicMock

from lab_model.execution.edge.client import (
    EdgeTransport,
    HttpEdgeClient,
    InProcessEdgeClient,
    PollEdgeClient,
    command_to_execute_body,
    resolve_edge_client,
)
from lab_model.execution.edge.endpoint import EdgeEndpointConfig, parse_edge_endpoint
from lab_model.execution.edge.registry import EdgeAgentRegistry
from lab_model.execution.edge.stream_proxy import resolve_live_channel_path


class EdgeEndpointParseTests(unittest.TestCase):
    def test_parse_null_base(self) -> None:
        cfg = parse_edge_endpoint(
            {"base_url": None, "contract_version": "1.0.0", "lan_direct_ok": True}
        )
        self.assertFalse(cfg.configured)
        self.assertTrue(cfg.lan_direct_ok)
        self.assertEqual(cfg.contract_version, "1.0.0")

    def test_parse_http_base(self) -> None:
        cfg = parse_edge_endpoint({"base_url": "http://127.0.0.1:8100/"})
        self.assertTrue(cfg.configured)
        self.assertEqual(cfg.normalized_base_url(), "http://127.0.0.1:8100")


class ResolveEdgeClientTests(unittest.TestCase):
    def setUp(self) -> None:
        # Isolate from process-global registry used by the live server.
        import lab_model.execution.edge.client as client_mod

        self._orig_reg = client_mod.edge_agent_registry
        self.reg = EdgeAgentRegistry(stale_after_s=60.0)
        client_mod.edge_agent_registry = self.reg

    def tearDown(self) -> None:
        import lab_model.execution.edge.client as client_mod

        client_mod.edge_agent_registry = self._orig_reg

    def test_in_process_default(self) -> None:
        lab = MagicMock()
        client = resolve_edge_client("mock.default", lab=lab, edge_config=EdgeEndpointConfig())
        self.assertIsInstance(client, InProcessEdgeClient)
        self.assertEqual(client.transport, EdgeTransport.IN_PROCESS)

    def test_http_when_configured(self) -> None:
        cfg = EdgeEndpointConfig(base_url="http://127.0.0.1:8100", contract_version="1.0.0")
        client = resolve_edge_client("mock.default", lab=None, edge_config=cfg)
        self.assertIsInstance(client, HttpEdgeClient)
        self.assertEqual(client.transport, EdgeTransport.HTTP)

    def test_poll_wins_over_http(self) -> None:
        self.reg.register(backend_id="mock.default", agent_id="edge_test")
        cfg = EdgeEndpointConfig(base_url="http://127.0.0.1:8100")
        client = resolve_edge_client("mock.default", lab=None, edge_config=cfg)
        self.assertIsInstance(client, PollEdgeClient)
        self.assertEqual(client.transport, EdgeTransport.POLL)


class CommandMappingTests(unittest.TestCase):
    def test_maps_action_and_channel(self) -> None:
        body = command_to_execute_body(
            {
                "action": "START_LIVE_FEED",
                "target_id": "tag_22",
                "channel": "stream",
            }
        )
        self.assertEqual(body["primitive"], "START_LIVE_FEED")
        self.assertEqual(body["args"]["tag_id"], "tag_22")
        self.assertEqual(body["args"]["channel"], "stream")


class StreamProxyHelperTests(unittest.TestCase):
    def test_resolve_mjpeg_channel(self) -> None:
        caps = {
            "telemetry_channels": {
                "tag_22.camera_image": {
                    "transport": "jpeg_poll",
                    "path": "/stream/tag_22/camera_image.jpg",
                    "requires_primitive": "START_LIVE_FEED",
                    "binds_measurable": "tag_22.camera_image",
                },
                "tag_22.camera_image.mjpeg": {
                    "transport": "mjpeg_http",
                    "path": "/stream/tag_22/camera_image.mjpg",
                    "requires_primitive": "START_LIVE_FEED",
                    "binds_measurable": "tag_22.camera_image",
                },
            }
        }
        resolved = resolve_live_channel_path(
            caps, measurable_or_channel="tag_22.camera_image", prefer_mjpeg=True
        )
        assert resolved is not None
        path, transport = resolved
        self.assertEqual(path, "/stream/tag_22/camera_image.mjpg")
        self.assertEqual(transport, "mjpeg_http")


class HttpEdgeClientSmokeTests(unittest.TestCase):
    def test_execute_maps_refused(self) -> None:
        """Unit-level: map a refused HTTP body without a live server."""

        async def _run() -> None:
            client = HttpEdgeClient(base_url="http://127.0.0.1:9")
            # Port 9 should fail fast / connection refused → ok=False
            result = await client.execute_command(
                {"action": "EVAL_KERNEL", "target_id": "tag_22"},
                timeout_s=0.5,
            )
            self.assertFalse(result.ok)
            self.assertEqual(result.transport, EdgeTransport.HTTP)

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()
