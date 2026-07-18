"""Request-scoped lab dispatch via contextvars (multi-backend)."""
from __future__ import annotations

import contextvars
from typing import Any, Optional

from fastapi import HTTPException

from lab_model.coordinator.backends.context import backend_context
from lab_model.coordinator.backends.registry import BackendRegistry, BackendRuntime
from lab_model.coordinator.backends.server import require_backend

_backend_id_ctx: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "cloudlabs_request_backend_id",
    default=None,
)


def set_request_backend_id(backend_id: str) -> contextvars.Token:
    return _backend_id_ctx.set(backend_id.strip())


def get_request_backend_id() -> Optional[str]:
    return _backend_id_ctx.get()


def reset_request_backend_id(token: contextvars.Token) -> None:
    _backend_id_ctx.reset(token)


def active_backend_id_for_request() -> str:
    bid = get_request_backend_id()
    if not bid:
        raise HTTPException(
            status_code=400,
            detail=(
                "backend_id is required for this API call. "
                "Pass ?backend_id=, header X-CloudLabs-Backend, or select a backend in the UI."
            ),
        )
    return bid


class RequestLab:
    """Drop-in proxy: delegates to the communicator for the request backend_id."""

    def __init__(self, registry: BackendRegistry) -> None:
        self._registry = registry

    def _runtime(self, *, init: bool = True) -> BackendRuntime:
        bid = active_backend_id_for_request()
        return require_backend(self._registry, bid, init=init)

    def __bool__(self) -> bool:
        try:
            rt = self._runtime(init=False)
            return rt.availability == "ready"
        except HTTPException:
            return False

    def __getattr__(self, name: str) -> Any:
        rt = self._runtime(init=True)
        if rt.lab is None:
            raise AttributeError(f"communicator not initialized for {rt.backend_id!r}")
        with backend_context(rt.paths, rt.manifest):
            return getattr(rt.lab, name)


class RequestRuntimeManager:
    def __init__(self, registry: BackendRegistry) -> None:
        self._registry = registry

    @property
    def mode(self) -> str:
        rt = require_backend(
            self._registry,
            active_backend_id_for_request(),
            init=True,
        )
        if rt.runtime_manager is not None:
            return rt.runtime_manager.mode
        return rt.lab_mode

    def __getattr__(self, name: str) -> Any:
        rt = require_backend(
            self._registry,
            active_backend_id_for_request(),
            init=True,
        )
        mgr = rt.runtime_manager
        if mgr is None:
            raise AttributeError("runtime_manager not available for this backend")
        with backend_context(rt.paths, rt.manifest):
            return getattr(mgr, name)


def control_dir_for_request(registry: BackendRegistry) -> str:
    return require_backend(
        registry,
        active_backend_id_for_request(),
        init=False,
    ).control_dir()


def recipes_dir_for_request(registry: BackendRegistry) -> str:
    return require_backend(
        registry,
        active_backend_id_for_request(),
        init=False,
    ).recipes_dir()


def catalog_pins_for_request(registry: BackendRegistry) -> Any:
    rt = require_backend(registry, active_backend_id_for_request(), init=False)
    return rt.catalog_pins_store


__all__ = [
    "RequestLab",
    "RequestRuntimeManager",
    "active_backend_id_for_request",
    "catalog_pins_for_request",
    "control_dir_for_request",
    "get_request_backend_id",
    "recipes_dir_for_request",
    "reset_request_backend_id",
    "set_request_backend_id",
]
