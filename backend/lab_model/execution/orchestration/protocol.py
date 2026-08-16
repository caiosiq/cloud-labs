"""Protocols for :class:`~lab_communicator.base.LabCommunicator` orchestration helpers."""
from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional, Protocol

from lab_model.coordinator.state.snapshot import LabPose


class StateHost(Protocol):
    """Minimal bridge surface for state + persistence helpers."""

    current_state: Dict[str, Any]
    _state_lock: threading.RLock
    log_prefix: str

    def _persist_state(self) -> None: ...
    def _null_measurables_for_targets(
        self, target_ids: List[str], *, persist: bool = True
    ) -> None: ...


class MoveHost(StateHost, Protocol):
    """Bridge surface for table-move primitive orchestration."""

    def _set_status(self, status: str, *, persist: bool = True) -> None: ...

    def _allocate_storage_slot(
        self, target_id: str
    ) -> Optional[tuple[float, float, int, int]]: ...

    async def _primitive_move_component(
        self, target_id: str, commanded: LabPose
    ) -> Optional[LabPose]: ...

    def _after_move_to_storage(self, target_id: str, slot_i: int, slot_j: int) -> None: ...
    def _after_move_out_of_storage(self, target_id: str) -> None: ...
    def _apply_is_placed_flag(self, target_id: str, value: bool) -> None: ...


class MotorHost(StateHost, Protocol):
    """Bridge surface for motor primitive orchestration."""

    def _set_status(self, status: str, *, persist: bool = True) -> None: ...

    async def _primitive_move_motor(
        self, target_id: str, motor_id: int, distance: float
    ) -> None: ...

    def _motor_catalog_ok(self, target_id: str, motor_id: int) -> bool: ...
    def _catalog_meta_for_tag(self, tag_id: str) -> Optional[Dict[str, Any]]: ...


class InAirHost(StateHost, Protocol):
    """Bridge surface for PICK / HOVER / PLACE_FROM_HOVER."""

    max_safe_hover_z_lab_mm: float

    def _set_status(self, status: str, *, persist: bool = True) -> None: ...

    async def _primitive_pick_component(
        self, target_id: str, commanded: LabPose, params: Dict[str, Any]
    ) -> float: ...

    async def _primitive_hover_component(
        self, target_id: str, commanded: LabPose, speed: int
    ) -> Optional[LabPose]: ...

    async def _primitive_place_from_hover(
        self,
        target_id: str,
        commanded: LabPose,
        params: Dict[str, Any],
    ) -> None: ...


class OptimizeHost(StateHost, Protocol):
    """Bridge surface for OPTIMIZE (legacy strategy and ensemble)."""

    def _catalog_meta_for_tag(self, tag_id: str) -> Optional[Dict[str, Any]]: ...
    def _set_status(self, status: str, *, persist: bool = True) -> None: ...

    def _primitive_prepare_optimization_run(
        self, target_id: str, strategy_name: str
    ) -> Optional[str]: ...

    async def _primitive_optimize_component(
        self,
        *,
        target_id: str,
        strategy_name: str,
        params: Dict[str, Any],
        progress_callback: Any,
    ) -> Optional[Dict[str, Any]]: ...

    async def _primitive_run_ensemble_optimization(
        self,
        *,
        spec: Any,
        x0: Dict[str, float],
        session_id: str,
        progress_callback: Any,
        should_abort: Any = None,
    ) -> Optional[Dict[str, Any]]: ...

    def _primitive_finalize_optimization_run(self) -> None: ...


class RecordMeasurablesHost(StateHost, Protocol):
    """Bridge surface for RECORD_MEASURABLES orchestration."""

    def return_measurables_for_tag(self, tag_id: str) -> Dict[str, Any]: ...
    def _catalog_meta_for_tag(self, tag_id: str) -> Optional[Dict[str, Any]]: ...

    async def _primitive_record_measurables(
        self, tag_id: str, catalog_meta: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]: ...

    async def end_live_feed(self, target_id: str, *, channel: str = "all") -> None: ...


class LiveFeedHost(StateHost, Protocol):
    """Bridge surface for START_LIVE_FEED / END_LIVE_FEED."""

    def _catalog_meta_for_tag(self, tag_id: str) -> Optional[Dict[str, Any]]: ...

    async def _primitive_start_live_feed(
        self,
        target_id: str,
        *,
        channel: str,
        backend: str,
        cam_id: Optional[int],
        profile: str = "default",
        exposure_time_ms: Optional[float] = None,
    ) -> tuple[bool, str]: ...

    async def _primitive_end_live_feed(
        self,
        target_id: str,
        *,
        channel: str,
        catalog_meta: Dict[str, Any],
    ) -> tuple[bool, str]: ...
