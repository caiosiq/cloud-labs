"""Mock edge host — teaching physics behind Edge Contract."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mock_edge.host.communicator import MockLabCommunicator

__all__ = ["MockLabCommunicator", "LabCommunicator"]


def __getattr__(name: str):
    if name == "MockLabCommunicator":
        from mock_edge.host.communicator import MockLabCommunicator

        return MockLabCommunicator
    if name == "LabCommunicator":
        from mock_edge.host.base import LabCommunicator

        return LabCommunicator
    raise AttributeError(f"module 'mock_edge.host' has no attribute {name!r}")
