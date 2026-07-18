"""MuJoCo simulation host (physics). Edge Contract face is ``cloudlabs_edge/``."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from simulation_edge.host.simulation_host import SimulationHost

__all__ = ["SimulationHost"]


def __getattr__(name: str):
    if name == "SimulationHost":
        from simulation_edge.host.simulation_host import SimulationHost

        return SimulationHost
    raise AttributeError(f"module 'simulation_edge.host' has no attribute {name!r}")
