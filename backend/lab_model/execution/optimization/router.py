"""ActuatorRouter — continuous vs invasive_discrete hardware sequencing."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Mapping, Sequence


class ActuatorRouter(ABC):
    """
    Apply normalized search steps to hardware with clearance and touch-and-go rules.

    Mock/real implementations live in ``lab_communicator``.
    """

    @abstractmethod
    def enter_continuous_block(self, variable_ids: Sequence[str]) -> None:
        """Release grippers, retract to safe home, clear optical path."""

    @abstractmethod
    def enter_invasive_block(self, variable_ids: Sequence[str]) -> None:
        """Prepare for lazy re-engage touch-and-go on invasive variables."""

    @abstractmethod
    def apply_eval(
        self,
        physical_values: Mapping[str, float],
        *,
        block_id: str,
    ) -> None:
        """Apply one candidate point (continuous setpoints and/or invasive moves)."""

    @abstractmethod
    def exit_block(self, block_id: str) -> None:
        """Leave block with arm clear; no post-block re-clamp."""


class StubActuatorRouter(ActuatorRouter):
    """No-op router for dry-run / unit tests."""

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


__all__ = ["ActuatorRouter", "StubActuatorRouter"]
