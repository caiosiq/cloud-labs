"""ASGI entry for the simulation ``cloudlabs_edge`` (Edge Contract v1).

Run from this folder::

    uvicorn main:app --host 0.0.0.0 --port 8120

Or from the monorepo::

    python -m simulation_edge --port 8120
"""

from __future__ import annotations

from server.app import build_default_app, create_app  # noqa: F401

app = build_default_app()
