"""Parent-process client for the dedicated MuJoCo viewer process."""

from __future__ import annotations

import multiprocessing
import os
import queue
import threading
import uuid
from typing import Any, Dict, Optional

from lab_communicator.mujoco.runtime import simulation_process_main
from lab_communicator.mujoco.scene import SceneSpec


class SimulatorProcessError(RuntimeError):
    pass


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
    ) -> None:
        self.scene = scene
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
        self.last_error: Optional[str] = None

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
            self.last_error = None
            self._commands = self._ctx.Queue()
            self._results = self._ctx.Queue()
            self._process = self._ctx.Process(
                target=simulation_process_main,
                args=(self.scene, self._commands, self._results),
                kwargs={
                    "show_viewer": self.show_viewer,
                    "realtime": self.realtime,
                },
                name="cloud-labs-mujoco",
                daemon=True,
            )
            self._process.start()
            message = self._wait_for_message(timeout_s=timeout_s)
            if message.get("type") != "ready":
                self.last_error = str(message.get("error") or "MuJoCo failed to start")
                self.stop()
                raise SimulatorProcessError(self.last_error)

    def move_component(
        self,
        tag_id: str,
        *,
        target_x_mm: float,
        target_y_mm: float,
        target_rotation_deg: float,
        timeout_s: float = 90.0,
    ) -> Dict[str, Any]:
        with self._lock:
            if not self.running:
                raise SimulatorProcessError(
                    self.last_error or "MuJoCo process is not running"
                )
            request_id = uuid.uuid4().hex
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
                    self.last_error = str(message.get("error") or message_type)
                    raise SimulatorProcessError(self.last_error)
                if message.get("request_id") != request_id:
                    continue
                if message_type == "error":
                    self.last_error = str(message.get("error") or "MuJoCo move failed")
                    raise SimulatorProcessError(self.last_error)
                if message_type == "move_complete":
                    self.last_error = None
                    return dict(message.get("result") or {})

    def status(self) -> Dict[str, Any]:
        with self._lock:
            self._drain_status_events()
            running = self.running
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
                "profile": self.scene.profile_id,
                "last_error": self.last_error,
            }

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

    def _drain_status_events(self) -> None:
        if self._results is None:
            return
        while True:
            try:
                message = dict(self._results.get_nowait())
            except queue.Empty:
                return
            message_type = message.get("type")
            if message_type in {
                "viewer_closed",
                "runtime_error",
                "startup_error",
            }:
                self.last_error = str(message.get("error") or message_type)
