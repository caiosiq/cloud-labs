"""Process-local binding of the simulation host for filled adapters."""

from __future__ import annotations

from typing import Any, Optional, Set

_lab: Any = None
live_active: Set[str] = set()
teleop_active: Set[str] = set()


def bind_lab(lab: Any) -> None:
    """Attach the SimulationHost for this process."""
    global _lab
    _lab = lab


def get_lab() -> Any:
    """Return the bound lab host, bootstrapping if needed."""
    global _lab
    if _lab is None:
        from simulation_edge.bootstrap import bootstrap_host

        host, _ = bootstrap_host()
        _lab = host
    return _lab


def tag_from_args(args: dict[str, Any], default: str = "") -> str:
    return str(args.get("tag_id") or args.get("target_id") or default)
