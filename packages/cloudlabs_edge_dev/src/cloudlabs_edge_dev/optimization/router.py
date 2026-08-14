"""Actuator router ABC — lab fills continuous / invasive apply paths."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Mapping, Optional, Sequence

from .guards import ClearanceError


class ActuatorRouter(ABC):
    """Apply search steps to hardware with clearance and touch-and-go rules."""

    @abstractmethod
    def enter_continuous_block(self, variable_ids: Sequence[str]) -> None:
        """Release grippers / clear path for continuous (motor) variables."""

    @abstractmethod
    def enter_invasive_block(self, variable_ids: Sequence[str]) -> None:
        """Prepare for invasive pose moves (touch-and-go)."""

    @abstractmethod
    def apply_eval(
        self,
        physical_values: Mapping[str, float],
        *,
        block_id: str,
    ) -> None:
        """Apply one candidate point."""

    @abstractmethod
    def exit_block(self, block_id: str) -> None:
        """Leave block with actuators in a safe / bookkeeping-consistent state."""

    def assert_optical_path_clear(self) -> None:
        """Raise :class:`ClearanceError` if the arm occludes the beam.

        Called by the session on continuous-block enter and before each
        continuous ``apply_eval``. Labs override; default is a no-op so
        measurable-only dry-runs still work.
        """
        return None


class RecordingRouter(ActuatorRouter):
    """Test helper that records enter/apply/exit without hardware."""

    def __init__(self, *, clearance_ok: bool = True) -> None:
        self.events: List[tuple] = []
        self.engaged: bool = False
        self.clearance_ok = clearance_ok
        self.last_applied: Optional[dict] = None

    def assert_optical_path_clear(self) -> None:
        self.events.append(("assert_clear",))
        if not self.clearance_ok:
            raise ClearanceError("recording router: optical path not clear")

    def enter_continuous_block(self, variable_ids: Sequence[str]) -> None:
        self.engaged = True
        self.events.append(("enter_continuous", list(variable_ids)))

    def enter_invasive_block(self, variable_ids: Sequence[str]) -> None:
        self.engaged = True
        self.events.append(("enter_invasive", list(variable_ids)))

    def apply_eval(
        self,
        physical_values: Mapping[str, float],
        *,
        block_id: str,
    ) -> None:
        self.last_applied = dict(physical_values)
        self.events.append(("apply", block_id, dict(physical_values)))

    def exit_block(self, block_id: str) -> None:
        self.engaged = False
        self.events.append(("exit", block_id))


class StubActuatorRouter(ActuatorRouter):
    def enter_continuous_block(self, variable_ids: Sequence[str]) -> None:
        return None

    def enter_invasive_block(self, variable_ids: Sequence[str]) -> None:
        return None

    def apply_eval(
        self,
        physical_values: Mapping[str, float],
        *,
        block_id: str,
    ) -> None:
        return None

    def exit_block(self, block_id: str) -> None:
        return None


__all__ = ["ActuatorRouter", "ClearanceError", "RecordingRouter", "StubActuatorRouter"]
