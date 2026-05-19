"""Instantiate a :class:`LabCommunicator` from a manifest ``communicator`` id."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lab_communicator.base import LabCommunicator

_KNOWN = frozenset({"mock", "real"})


def known_communicator_ids() -> frozenset[str]:
    return _KNOWN


def create_communicator(communicator_id: str) -> "LabCommunicator":
    """Import and construct the backend named in ``lab_manifest.json``."""
    key = (communicator_id or "").strip().lower()
    if key not in _KNOWN:
        raise ValueError(
            f"Unknown communicator {communicator_id!r} in lab_manifest.json "
            f"(supported: {', '.join(sorted(_KNOWN))})"
        )
    if key == "real":
        from lab_communicator.real import RealLabCommunicator

        return RealLabCommunicator()
    from lab_communicator.mock import MockLabCommunicator

    return MockLabCommunicator()
