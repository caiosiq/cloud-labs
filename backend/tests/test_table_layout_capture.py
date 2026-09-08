import asyncio
import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request

import main


def _request_with_body(payload: bytes) -> Request:
    delivered = False

    async def receive():
        nonlocal delivered
        if delivered:
            return {"type": "http.request", "body": b"", "more_body": False}
        delivered = True
        return {"type": "http.request", "body": payload, "more_body": False}

    return Request({"type": "http", "method": "POST", "path": "/"}, receive)


class TableLayoutCaptureTest(unittest.TestCase):
    def test_png_is_saved_in_simulation_capture_directory_without_sidecar(self):
        payload = main._PNG_SIGNATURE + b"test-png-payload"
        with tempfile.TemporaryDirectory() as root:
            with patch.object(main, "_project_root", root):
                result = asyncio.run(
                    main.save_table_layout_capture(
                        _request_with_body(payload),
                        variant="no-text",
                    )
                )

            expected_dir = os.path.join(root, "simulation_edge", "captures")
            self.assertEqual(os.path.dirname(result["path"]), expected_dir)
            self.assertTrue(result["filename"].endswith("-4000x2800.png"))
            with open(result["path"], "rb") as capture_file:
                self.assertEqual(capture_file.read(), payload)
            self.assertEqual(os.listdir(expected_dir), [result["filename"]])

    def test_non_png_payload_is_rejected(self):
        with self.assertRaises(HTTPException) as raised:
            asyncio.run(
                main.save_table_layout_capture(
                    _request_with_body(b"not a png"),
                    variant="labeled",
                )
            )
        self.assertEqual(raised.exception.status_code, 415)


if __name__ == "__main__":
    unittest.main()
