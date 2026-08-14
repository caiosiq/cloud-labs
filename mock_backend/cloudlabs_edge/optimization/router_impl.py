"""Actuator router stub — Phase 2 wires motors / pose."""
from __future__ import annotations
from typing import Mapping, Sequence
from cloudlabs_edge_dev.optimization import ActuatorRouter

class EdgeActuatorRouter(ActuatorRouter):
    def enter_continuous_block(self, variable_ids: Sequence[str]) -> None:
        raise NotImplementedError("Phase 2: enter_continuous_block")
    def enter_invasive_block(self, variable_ids: Sequence[str]) -> None:
        raise NotImplementedError("Phase 2: enter_invasive_block")
    def apply_eval(self, physical_values: Mapping[str, float], *, block_id: str) -> None:
        raise NotImplementedError(f"Phase 2: apply_eval {block_id}")
    def exit_block(self, block_id: str) -> None:
        raise NotImplementedError(f"Phase 2: exit_block {block_id}")
