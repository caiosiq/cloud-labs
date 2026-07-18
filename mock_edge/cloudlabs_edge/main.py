"""ASGI entry for the mock teaching ``cloudlabs_edge`` (Edge Contract v1).

Run from this folder::

    uvicorn main:app --host 0.0.0.0 --port 8100

Or from the monorepo::

    python -m mock_edge --port 8100
"""

from __future__ import annotations

from server.app import build_default_app, create_app  # noqa: F401

app = build_default_app()
