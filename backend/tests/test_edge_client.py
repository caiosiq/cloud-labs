"""Phase 3 EdgeClient resolver + HTTP contract mapping."""
from __future__ import annotations

import asyncio
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
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


def _jpeg_bytes(h: int = 4, w: int = 6) -> bytes:
    import cv2  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    arr = np.zeros((h, w, 3), dtype=np.uint8)
    ok, buf = cv2.imencode(".jpg", arr)
    assert ok
    return buf.tobytes()


class _EdgeStubHandler(BaseHTTPRequestHandler):
    jpeg: bytes = b""
    lab_state: dict = {}

    def log_message(self, *args) -> None:  # silence test server noise
        return

    def do_GET(self) -> None:  # noqa: N802 (stdlib name)
        if self.path == "/lab-state":
            body = json.dumps(type(self).lab_state).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.endswith("camera_image.jpg"):
            body = type(self).jpeg
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()


class HttpEdgeLabStateFetchTests(unittest.TestCase):
    """get_lab_state / fetch_bytes against a real (threaded) HTTP edge stub."""

    def setUp(self) -> None:
        _EdgeStubHandler.jpeg = _jpeg_bytes()
        _EdgeStubHandler.lab_state = {
            "system_status": "IDLE",
            "components": {
                "tag_22": {
                    "id": "tag_22",
                    "statecontrol": {"tunables": {}, "measurables": {"camera_image": None}},
                    "telemetry": {},
                }
            },
        }
        self.server = HTTPServer(("127.0.0.1", 0), _EdgeStubHandler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.client = HttpEdgeClient(base_url=f"http://127.0.0.1:{self.port}")

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2.0)

    def test_get_lab_state(self) -> None:
        state = self.client.get_lab_state()
        self.assertIsInstance(state, dict)
        self.assertEqual(state["system_status"], "IDLE")
        self.assertIn("tag_22", state["components"])

    def test_fetch_bytes_ok(self) -> None:
        data = self.client.fetch_bytes("/measurables/tag_22/camera_image.jpg")
        self.assertTrue(data)
        self.assertEqual(data, _EdgeStubHandler.jpeg)

    def test_fetch_bytes_missing_returns_none(self) -> None:
        self.assertIsNone(self.client.fetch_bytes("/measurables/tag_22/nope.bin"))

    def test_get_lab_state_bad_port_returns_none(self) -> None:
        client = HttpEdgeClient(base_url="http://127.0.0.1:9")
        self.assertIsNone(client.get_lab_state())


class ResolveTensorFromBytesTests(unittest.TestCase):
    def test_decodes_jpeg_to_hwc_uint8(self) -> None:
        from lab_model.language.measurables.resolve_data import resolve_tensor_from_bytes
        from lab_model.language.measurables.tensor import LazyRef, MeasurableTensor

        tensor = MeasurableTensor(
            tag_id="tag_22",
            field="camera_image",
            dtype="uint8",
            shape=(),
            axes={"0": "height", "1": "width", "2": "channel"},
            units={"0": "px", "1": "px", "2": "bgr"},
            domain="spatial",
            data=LazyRef(kind="url", href="/measurables/tag_22/camera_image.jpg", format="jpeg"),
        )
        resolved = resolve_tensor_from_bytes(tensor, _jpeg_bytes(4, 6))
        self.assertFalse(isinstance(resolved.data, LazyRef))
        self.assertEqual(tuple(resolved.shape), (4, 6, 3))
        self.assertEqual(str(resolved.data.dtype), "uint8")

    def test_empty_bytes_is_noop(self) -> None:
        from lab_model.language.measurables.resolve_data import resolve_tensor_from_bytes
        from lab_model.language.measurables.tensor import LazyRef, MeasurableTensor

        tensor = MeasurableTensor(
            tag_id="tag_22",
            field="camera_image",
            dtype="uint8",
            shape=(),
            axes={},
            units={},
            domain="spatial",
            data=LazyRef(kind="url", href="/x.jpg", format="jpeg"),
        )
        self.assertIs(resolve_tensor_from_bytes(tensor, b"").data.__class__, LazyRef)


if __name__ == "__main__":
    unittest.main()
