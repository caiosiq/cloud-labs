"""Mock backend host — teaching physics behind Edge Contract."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mock_backend.host.communicator import MockLabCommunicator

__all__ = ["MockLabCommunicator", "LabCommunicator"]


def __getattr__(name: str):
    if name == "MockLabCommunicator":
        from mock_backend.host.communicator import MockLabCommunicator

        return MockLabCommunicator
    if name == "LabCommunicator":
        from mock_backend.host.base import LabCommunicator

        return LabCommunicator
    raise AttributeError(f"module 'mock_backend.host' has no attribute {name!r}")
