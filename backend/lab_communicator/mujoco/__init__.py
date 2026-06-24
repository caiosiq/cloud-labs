"""MuJoCo-backed cloud-labs communicator."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lab_communicator.mujoco.communicator import MujocoLabCommunicator

__all__ = ["MujocoLabCommunicator"]


def __getattr__(name: str):
    if name == "MujocoLabCommunicator":
        from lab_communicator.mujoco.communicator import MujocoLabCommunicator

        return MujocoLabCommunicator
    raise AttributeError(f"module 'lab_communicator.mujoco' has no attribute {name!r}")
