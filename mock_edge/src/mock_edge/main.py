"""ASGI entry for the filled mock cloudlabs_edge skeleton.

``python -m mock_edge`` and ``uvicorn mock_edge.main:app`` both serve this app.
HTTP is Edge Contract FastAPI from ``mock_edge.server.app.create_app``.
"""

from __future__ import annotations

from mock_edge.server.app import build_default_app, create_app  # noqa: F401

app = build_default_app()
