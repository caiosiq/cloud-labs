"""Per-request backend path context (async-safe via contextvars)."""
from __future__ import annotations

import contextvars
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator, Optional

from lab_model.coordinator.backends.lab_view_config import LabViewManifest, LabViewPaths

_backend_paths_ctx: contextvars.ContextVar[Optional[LabViewPaths]] = contextvars.ContextVar(
    "cloudlabs_backend_paths",
    default=None,
)
_backend_manifest_ctx: contextvars.ContextVar[Optional[LabViewManifest]] = contextvars.ContextVar(
    "cloudlabs_backend_manifest",
    default=None,
)


@dataclass(frozen=True)
class BackendContext:
    paths: LabViewPaths
    manifest: LabViewManifest


def get_context_paths() -> Optional[LabViewPaths]:
    return _backend_paths_ctx.get()


def get_context_manifest() -> Optional[LabViewManifest]:
    return _backend_manifest_ctx.get()


def bind_backend_context(paths: LabViewPaths, manifest: LabViewManifest) -> contextvars.Token:
    """Install paths/manifest for the current async task or thread.

    Also re-applies that backend's ``layout.json`` into the process-global
    storage geometry so multi-backend probing cannot leave the wrong grid active.
    """
    tok_paths = _backend_paths_ctx.set(paths)
    _backend_manifest_ctx.set(manifest)
    if paths.root_dir and paths.layout_json:
        try:
            import json
            import os

            from lab_model.language.domain.storage_region import configure_from_layout_document

            if os.path.isfile(paths.layout_json):
                with open(paths.layout_json, "r", encoding="utf-8") as fh:
                    doc = json.load(fh)
                if isinstance(doc, dict):
                    configure_from_layout_document(doc)
        except Exception:
            pass
    return tok_paths


def reset_backend_context(token: contextvars.Token) -> None:
    _backend_paths_ctx.reset(token)
    _backend_manifest_ctx.set(None)


@contextmanager
def backend_context(paths: LabViewPaths, manifest: LabViewManifest) -> Iterator[BackendContext]:
    token = bind_backend_context(paths, manifest)
    try:
        yield BackendContext(paths=paths, manifest=manifest)
    finally:
        reset_backend_context(token)


__all__ = [
    "BackendContext",
    "backend_context",
    "bind_backend_context",
    "get_context_manifest",
    "get_context_paths",
    "reset_backend_context",
]
