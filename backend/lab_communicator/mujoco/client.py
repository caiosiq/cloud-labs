"""Parent-process client for the dedicated MuJoCo viewer process."""

from __future__ import annotations

import multiprocessing
import os
import queue
import threading
import uuid
from typing import Any, Dict, Optional

from lab_communicator.mujoco.runtime import (
    MUJOCO_PLANNER_CUSTOM_IK,
    simulation_process_main,
)
from lab_communicator.mujoco.scene import SceneSpec


class SimulatorProcessError(RuntimeError):
    def __init__(self, message: str, *, details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.details = dict(details or {})


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class MuJoCoProcessClient:
    def __init__(
        self,
        scene: SceneSpec,
        *,
        show_viewer: Optional[bool] = None,
        realtime: Optional[bool] = None,
        planner_backend: str = MUJOCO_PLANNER_CUSTOM_IK,
    ) -> None:
        self.scene = scene
        self.planner_backend = str(planner_backend or MUJOCO_PLANNER_CUSTOM_IK)
        self.show_viewer = (
            _env_bool("CLOUDLAB_MUJOCO_VIEWER", True)
            if show_viewer is None
            else bool(show_viewer)
        )
        self.realtime = (
            _env_bool("CLOUDLAB_MUJOCO_REALTIME", True)
            if realtime is None
            else bool(realtime)
        )
        self._ctx = multiprocessing.get_context("spawn")
        self._commands: Any = None
        self._results: Any = None
        self._process: Any = None
        self._lock = threading.RLock()
        self._status_lock = threading.RLock()
        self.last_error: Optional[str] = None
        self.log_path: Optional[str] = None
        self.last_progress: Optional[Dict[str, Any]] = None

    @property
    def running(self) -> bool:
        return bool(self._process is not None and self._process.is_alive())

    @property
    def pid(self) -> Optional[int]:
        return int(self._process.pid) if self._process is not None and self._process.pid else None

    def start(self, *, timeout_s: float = 20.0) -> None:
        with self._lock:
            if self.running:
                return
            with self._status_lock:
                self.last_error = None
                self.last_progress = None
            self._commands = self._ctx.Queue()
            self._results = self._ctx.Queue()
            self._process = self._ctx.Process(
                target=simulation_process_main,
                args=(self.scene, self._commands, self._results),
                kwargs={
                    "show_viewer": self.show_viewer,
                    "realtime": self.realtime,
                    "planner_backend": self.planner_backend,
                },
                name="cloud-labs-mujoco",
                daemon=True,
            )
            self._process.start()
            message = self._wait_for_message(timeout_s=timeout_s)
            if message.get("type") != "ready":
                self.last_error = str(message.get("error") or "MuJoCo failed to start")
                self.stop()
                raise SimulatorProcessError(self.last_error, details=message)
            with self._status_lock:
                self.log_path = (
                    str(message.get("log_path")) if message.get("log_path") else None
                )

    def move_component(
        self,
        tag_id: str,
        *,
        target_x_mm: float,
        target_y_mm: float,
        target_rotation_deg: float,
        timeout_s: float = 240.0,
    ) -> Dict[str, Any]:
        with self._lock:
            if not self.running:
                raise SimulatorProcessError(
                    self.last_error or "MuJoCo process is not running"
                )
            request_id = uuid.uuid4().hex
            with self._status_lock:
                self.last_progress = None
            self._commands.put(
                {
                    "type": "move_component",
                    "request_id": request_id,
                    "tag_id": tag_id,
                    "target_x_mm": float(target_x_mm),
                    "target_y_mm": float(target_y_mm),
                    "target_rotation_deg": float(target_rotation_deg),
                }
            )
            while True:
                message = self._wait_for_message(timeout_s=timeout_s)
                message_type = message.get("type")
                if message_type in {"viewer_closed", "runtime_error", "startup_error"}:
                    with self._status_lock:
                        self.last_error = str(message.get("error") or message_type)
                    raise SimulatorProcessError(self.last_error, details=message)
                if message.get("request_id") != request_id:
                    continue
                if message_type == "progress":
                    with self._status_lock:
                        self.last_progress = dict(message)
                    continue
                if message_type == "error":
                    base_error = str(message.get("error") or "MuJoCo move failed")
                    log_path = message.get("log_path")
                    with self._status_lock:
                        if log_path:
                            self.log_path = str(log_path)
                        self.last_error = (
                            f"{base_error} (sim_request_id={request_id}, log={log_path})"
                            if log_path
                            else f"{base_error} (sim_request_id={request_id})"
                        )
                    raise SimulatorProcessError(self.last_error, details=message)
                if message_type == "move_complete":
                    with self._status_lock:
                        self.last_error = None
                        self.last_progress = None
                        if message.get("log_path"):
                            self.log_path = str(message["log_path"])
                    return dict(message.get("result") or {})

    def status(self) -> Dict[str, Any]:
        acquired = self._lock.acquire(blocking=False)
        try:
            if acquired:
                self._drain_status_events()
            running = self.running
            with self._status_lock:
                if not running and self._process is not None and self.last_error is None:
                    self.last_error = (
                        "MuJoCo process exited unexpectedly "
                        f"(exit code {self._process.exitcode})"
                    )
                return {
                    "running": running,
                    "pid": self.pid,
                    "viewer": self.show_viewer,
                    "realtime": self.realtime,
                    "planner": self.planner_backend,
                    "profile": self.scene.profile_id,
                    "log_path": self.log_path,
                    "last_error": self.last_error,
                    "progress": (
                        dict(self.last_progress)
                        if isinstance(self.last_progress, dict)
                        else None
                    ),
                }
        finally:
            if acquired:
                self._lock.release()

    def _drain_status_events(self) -> None:
        if self._results is None:
            return
        while True:
            try:
                message = dict(self._results.get_nowait())
            except queue.Empty:
                return
            message_type = message.get("type")
            if message_type == "progress":
                with self._status_lock:
                    self.last_progress = dict(message)
            elif message_type in {
                "viewer_closed",
                "runtime_error",
                "startup_error",
            }:
                with self._status_lock:
                    self.last_error = str(message.get("error") or message_type)

    def stop(self, *, timeout_s: float = 4.0) -> None:
        with self._lock:
            process = self._process
            if process is None:
                return
            if process.is_alive() and self._commands is not None:
                try:
                    self._commands.put({"type": "shutdown"})
                except Exception:
                    pass
                process.join(timeout=timeout_s)
            if process.is_alive():
                process.terminate()
                process.join(timeout=2.0)
            for q in (self._commands, self._results):
                try:
                    if q is not None:
                        q.close()
                except Exception:
                    pass
            self._process = None
            self._commands = None
            self._results = None
            with self._status_lock:
                self.log_path = None
                self.last_progress = None

    def _wait_for_message(self, *, timeout_s: float) -> Dict[str, Any]:
        if self._results is None:
            raise SimulatorProcessError("MuJoCo result queue is unavailable")
        try:
            return dict(self._results.get(timeout=timeout_s))
        except queue.Empty as exc:
            if not self.running:
                exitcode = self._process.exitcode if self._process is not None else None
                raise SimulatorProcessError(
                    f"MuJoCo process exited unexpectedly (exit code {exitcode})"
                ) from exc
            raise SimulatorProcessError(
                f"Timed out waiting for MuJoCo after {timeout_s:.1f}s"
            ) from exc
