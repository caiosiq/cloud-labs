"""Server helpers for resolving backend_id on HTTP requests."""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import HTTPException, Request

from lab_model.backends.context import backend_context
from lab_model.backends.registry import BackendRegistry, BackendRuntime


def resolve_backend_id(
    *,
    request: Optional[Request] = None,
    query_backend_id: Optional[str] = None,
    body: Optional[Dict[str, Any]] = None,
    header_name: str = "X-CloudLabs-Backend",
) -> str:
    """Resolve backend_id from query, header, or JSON body (in that order)."""
    if query_backend_id and str(query_backend_id).strip():
        return str(query_backend_id).strip()
    if request is not None:
        header = (request.headers.get(header_name) or "").strip()
        if header:
            return header
    if isinstance(body, dict):
        raw = body.get("backend_id")
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    raise HTTPException(
        status_code=400,
        detail=(
            "backend_id is required (query ?backend_id=, header X-CloudLabs-Backend, "
            "or JSON body field backend_id). List options with GET /api/backends."
        ),
    )


def require_backend(
    registry: BackendRegistry,
    backend_id: str,
    *,
    init: bool = True,
) -> BackendRuntime:
    try:
        rt = registry.get_runtime(backend_id, init=init)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if rt.availability == "unavailable":
        raise HTTPException(
            status_code=503,
            detail={
                "message": f"backend {backend_id!r} is unavailable on this server",
                "reason": rt.unavailable_reason,
            },
        )
    if rt.availability == "error":
        raise HTTPException(
            status_code=503,
            detail={
                "message": f"backend {backend_id!r} failed to initialize",
                "reason": rt.init_error or rt.unavailable_reason,
            },
        )
    if init and rt.lab is None:
        raise HTTPException(
            status_code=503,
            detail=f"backend {backend_id!r} communicator not initialized",
        )
    return rt


class BackendSession:
    """Context manager binding paths + exposing lab handles for one backend."""

    def __init__(self, runtime: BackendRuntime) -> None:
        self.runtime = runtime
        self._ctx = None

    def __enter__(self) -> BackendRuntime:
        self._ctx = backend_context(self.runtime.paths, self.runtime.manifest)
        self._ctx.__enter__()
        return self.runtime

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._ctx is not None:
            self._ctx.__exit__(exc_type, exc, tb)


__all__ = ["BackendSession", "require_backend", "resolve_backend_id"]
