"""Instantiate a :class:`LabCommunicator` from a manifest ``communicator`` id."""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Dict

if TYPE_CHECKING:
    from lab_communicator.base import LabCommunicator

_CommunicatorFactory = Callable[[], "LabCommunicator"]
_FACTORIES: Dict[str, _CommunicatorFactory] = {}


def register_communicator(communicator_id: str, factory: _CommunicatorFactory) -> None:
    """Register a backend factory (used by ``scripts/create_lab_communicator.py``)."""
    key = (communicator_id or "").strip().lower()
    if not key:
        raise ValueError("communicator_id must be non-empty")
    _FACTORIES[key] = factory


def known_communicator_ids() -> frozenset[str]:
    return frozenset(_FACTORIES.keys())


def create_communicator(communicator_id: str) -> "LabCommunicator":
    """Import and construct the backend named in ``lab_manifest.json``."""
    key = (communicator_id or "").strip().lower()
    factory = _FACTORIES.get(key)
    if factory is None:
        raise ValueError(
            f"Unknown communicator {communicator_id!r} in lab_manifest.json "
            f"(supported: {', '.join(sorted(_FACTORIES))})"
        )
    return factory()


def _register_builtins() -> None:
    from lab_communicator.mock import MockLabCommunicator
    from lab_communicator.real import RealLabCommunicator

    register_communicator("mock", MockLabCommunicator)
    register_communicator("real", RealLabCommunicator)


_register_builtins()

# Optional third-party backends (append-only; see scripts/create_lab_communicator.py).
try:
    from lab_communicator import extra_communicators  # noqa: F401
except ImportError:
    pass
