"""Safe mock/MuJoCo runtime switching without touching the physical backend."""

from __future__ import annotations

import copy
import threading
import uuid
from typing import Any, Dict, Optional, Tuple

from lab_communicator.mujoco import MujocoLabCommunicator
from lab_communicator.mujoco.runtime import (
    MUJOCO_PLANNER_CUSTOM_IK,
    MUJOCO_PLANNER_MOVEIT,
)
from lab_communicator.shared.lab_view_config import load_layout_document
from lab_model.domain.component import get_telemetry
from lab_model.domain.holding import get_holding


class RuntimeModeError(RuntimeError):
    pass


MUJOCO_CUSTOM_IK_MODE = "mujoco"
MUJOCO_MOVEIT_MODE = "mujoco_moveit"
MUJOCO_RUNTIME_MODES = frozenset({MUJOCO_CUSTOM_IK_MODE, MUJOCO_MOVEIT_MODE})


def is_mujoco_runtime_mode(mode: str | None) -> bool:
    return str(mode or "").strip().lower() in MUJOCO_RUNTIME_MODES


def planner_backend_for_mode(mode: str) -> str:
    return (
        MUJOCO_PLANNER_MOVEIT
        if str(mode).strip().lower() == MUJOCO_MOVEIT_MODE
        else MUJOCO_PLANNER_CUSTOM_IK
    )


class RuntimeLabProxy:
    """Delegate the existing global ``lab`` surface to mock or MuJoCo.

    This proxy is constructed only for a manifest whose communicator is
    already ``mock``. Physical deployments keep their direct communicator.
    """

    _LOCAL_ATTRS = frozenset(
        {
            "_mock",
            "_active",
            "_mode",
            "_lock",
            "_operation_tokens",
            "_switching",
            "_last_simulator_error",
        }
    )

    def __init__(self, mock_communicator: Any) -> None:
        object.__setattr__(self, "_mock", mock_communicator)
        object.__setattr__(self, "_active", mock_communicator)
        object.__setattr__(self, "_mode", "mock")
        object.__setattr__(self, "_lock", threading.RLock())
        object.__setattr__(self, "_operation_tokens", set())
        object.__setattr__(self, "_switching", False)
        object.__setattr__(self, "_last_simulator_error", None)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._active, name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in self._LOCAL_ATTRS:
            object.__setattr__(self, name, value)
            return
        setattr(self._active, name, value)

    @property
    def mode(self) -> str:
        return self._mode

    def command_target(self) -> Any:
        with self._lock:
            return self._active

    def reserve_operation(self) -> Tuple[Any, str]:
        with self._lock:
            if self._switching:
                raise RuntimeModeError("Runtime mode is currently switching")
            token = uuid.uuid4().hex
            self._operation_tokens.add(token)
            return self._active, token

    def release_operation(self, token: str) -> None:
        with self._lock:
            self._operation_tokens.discard(token)

    def supports_primitive(self, action: str) -> bool:
        target = self.command_target()
        if is_mujoco_runtime_mode(self._mode):
            status = target.simulator_status()
            if not status.get("running"):
                with self._lock:
                    self._last_simulator_error = (
                        status.get("last_error")
                        or "MuJoCo process is not running"
                    )
                return False
        checker = getattr(target, "supports_primitive", None)
        return bool(checker(action)) if callable(checker) else True

    def mode_info(self) -> Dict[str, Any]:
        with self._lock:
            active = self._active
            mode = self._mode
            last_error = self._last_simulator_error
        simulator = {
            "running": False,
            "pid": None,
            "viewer": False,
            "realtime": False,
            "profile": None,
            "last_error": last_error,
        }
        if is_mujoco_runtime_mode(mode):
            status = getattr(active, "simulator_status", None)
            if callable(status):
                simulator.update(status())
                if simulator.get("last_error"):
                    last_error = simulator["last_error"]
        return {
            "active_mode": mode,
            "physical_armed": False,
            "locked": False,
            "available_modes": [
                {"id": "mock", "label": "Mock UI", "enabled": True},
                {
                    "id": MUJOCO_CUSTOM_IK_MODE,
                    "label": "MuJoCo: custom IK",
                    "enabled": True,
                },
                {
                    "id": MUJOCO_MOVEIT_MODE,
                    "label": "MuJoCo: MoveIt",
                    "enabled": True,
                },
                {
                    "id": "physical",
                    "label": "Physical Experiment",
                    "enabled": False,
                    "reason": "backend not armed for hardware",
                },
            ],
            "simulator": simulator,
            "last_simulator_error": last_error,
        }

    def switch_mode(self, requested_mode: str) -> Dict[str, Any]:
        mode = str(requested_mode or "").strip().lower()
        if mode not in {"mock", *MUJOCO_RUNTIME_MODES}:
            raise RuntimeModeError(
                "Mock deployments may switch only between mock and MuJoCo modes"
            )
        with self._lock:
            if mode == self._mode:
                return self.mode_info()
            self._assert_switchable_locked()
            self._switching = True
            source = self._active
            source_mode = self._mode

        try:
            if is_mujoco_runtime_mode(mode):
                snapshot = source.get_lab_state()
                catalog_rows = source.get_catalog()
                try:
                    simulator = MujocoLabCommunicator(
                        snapshot,
                        catalog_rows,
                        load_layout_document(),
                        planner_backend=planner_backend_for_mode(mode),
                    )
                except Exception as exc:
                    with self._lock:
                        self._last_simulator_error = str(exc)
                        if source is self._mock:
                            self._active = self._mock
                            self._mode = "mock"
                        else:
                            self._active = source
                            self._mode = source_mode
                    raise RuntimeModeError(str(exc)) from exc
                with self._lock:
                    self._active = simulator
                    self._mode = mode
                    self._last_simulator_error = None
                if source is not self._mock:
                    source.shutdown_lab_processes()
            else:
                snapshot = copy.deepcopy(source.get_lab_state())
                self._mock.set_lab_state(snapshot)
                source.shutdown_lab_processes()
                with self._lock:
                    self._active = self._mock
                    self._mode = "mock"
            return self.mode_info()
        finally:
            with self._lock:
                self._switching = False

    def _assert_switchable_locked(self) -> None:
        if self._switching:
            raise RuntimeModeError("Runtime mode is already switching")
        if self._operation_tokens:
            raise RuntimeModeError("Cannot switch while a command or recipe is active")
        state = self._active.get_lab_state()
        status = state.get("system_status") or "IDLE"
        if status != "IDLE":
            raise RuntimeModeError(
                f"Cannot switch runtime mode while system status is {status}"
            )
        holding = get_holding(state)
        if holding.get("tag_id") or holding.get("requires_operator_confirm"):
            raise RuntimeModeError("Cannot switch while a component is held")
        if state.get("optimization_target_id"):
            raise RuntimeModeError("Cannot switch during optimization")
        for entry in (state.get("components") or {}).values():
            if not isinstance(entry, dict):
                continue
            teleop = get_telemetry(entry).get("teleop") or {}
            if isinstance(teleop, dict) and (
                teleop.get("active") or teleop.get("ready")
            ):
                raise RuntimeModeError("Cannot switch during a TeleOp session")

    def shutdown_lab_processes(self) -> None:
        with self._lock:
            active = self._active
            mode = self._mode
        if is_mujoco_runtime_mode(mode):
            try:
                snapshot = active.get_lab_state()
                self._mock.set_lab_state(snapshot)
            finally:
                active.shutdown_lab_processes()
                with self._lock:
                    self._active = self._mock
                    self._mode = "mock"
        else:
            active.shutdown_lab_processes()

    def stop_teleop_sweeper(self, *, join_timeout_s: float = 1.0) -> None:
        self._active.stop_teleop_sweeper(join_timeout_s=join_timeout_s)
