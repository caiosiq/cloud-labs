"""Per-component TELEOP orchestration (lease, goto, stale-lease sweeper)."""
from __future__ import annotations

import asyncio
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

from lab_model.domain.component import (
    is_teleop_active,
    is_teleop_ready,
    meas_pose,
    nominal_pose,
)
from lab_model.domain.holding import (
    SYSTEM_STATUS_BUSY,
    SYSTEM_STATUS_HOLDING,
    SYSTEM_STATUS_IDLE,
    SYSTEM_STATUS_OPTIMIZING,
    DEFAULT_HOVER_Z_MM,
    get_holding,
    held_tag,
)
from lab_model.state.commits import (
    commit_teleop_command_idle,
    commit_teleop_end,
    commit_teleop_goto,
    commit_teleop_jog,
    commit_teleop_ready,
    commit_teleop_session_pose,
    commit_teleop_start,
    commit_teleop_start_failed,
    sweep_stale_teleop_leases,
)
from lab_model.state.state_machine import (
    refuse_if_any_teleop_active,
    refuse_if_not_in_state,
    refuse_if_status_not_idle,
    refuse_if_stored,
)

if TYPE_CHECKING:
    from lab_communicator.base import LabCommunicator


def read_teleop_safety() -> Tuple[bool, int]:
    """``(require_lab_idle, teleop_ttl_ms)`` from lab manifest (defaults if unbootstrapped)."""
    try:
        from lab_communicator.shared.lab_view_config import get_lab_manifest

        m = get_lab_manifest()
        return (bool(m.teleop_require_lab_idle), int(m.teleop_ttl_ms))
    except Exception:
        return (False, 300_000)


def refuse_teleop_start(state: Dict[str, Any], target_id: str) -> Optional[str]:
    """START_TELEOP refusal ladder; returns reason or None."""
    r = refuse_if_not_in_state(state, target_id, primitive_name="start_teleop")
    if r:
        return r.reason
    r = refuse_if_stored(state, target_id, primitive_name="start_teleop")
    if r:
        return r.reason
    r = refuse_if_any_teleop_active(
        state, primitive_name="start_teleop", exclude_tag=target_id
    )
    if r:
        return r.reason
    require_idle, _ = read_teleop_safety()
    if require_idle:
        status = state.get("system_status") or SYSTEM_STATUS_IDLE
        if status == SYSTEM_STATUS_HOLDING and held_tag(state) == target_id:
            pass
        else:
            r = refuse_if_status_not_idle(state, primitive_name="start_teleop")
            if r:
                return r.reason
    else:
        status = state.get("system_status") or SYSTEM_STATUS_IDLE
        if status in (SYSTEM_STATUS_BUSY, SYSTEM_STATUS_OPTIMIZING):
            return (
                f"system_status={status!r}; cannot start_teleop while the "
                f"lab is busy. Wait for the current operation to finish."
            )
    return None


def _initial_live_pose(state: Dict[str, Any], tag_id: str) -> Dict[str, float]:
    if held_tag(state) == tag_id:
        hp = get_holding(state).get("nominal_pose") or {}
        return {
            "x": float(hp.get("x", 0.0)),
            "y": float(hp.get("y", 0.0)),
            "rotation": float(hp.get("rotation", 0.0)),
            "z": float(hp.get("z", DEFAULT_HOVER_Z_MM)),
        }
    components = state.get("components") or {}
    entry = components.get(tag_id) if isinstance(components, dict) else None
    if not isinstance(entry, dict):
        return {"x": 0.0, "y": 0.0, "rotation": 0.0, "z": DEFAULT_HOVER_Z_MM}
    mp = meas_pose(entry)
    if mp.get("x") is not None or mp.get("y") is not None:
        return {
            "x": float(mp.get("x", 0.0)),
            "y": float(mp.get("y", 0.0)),
            "rotation": float(mp.get("rotation", 0.0)),
            "z": float(mp.get("z", DEFAULT_HOVER_Z_MM)),
        }
    np = nominal_pose(entry)
    return {
        "x": float(np.get("x", 0.0)),
        "y": float(np.get("y", 0.0)),
        "rotation": float(np.get("rotation", 0.0)),
        "z": float(np.get("z", DEFAULT_HOVER_Z_MM)),
    }


class TeleopController:
    """TELEOP session logic bound to a :class:`~lab_communicator.base.LabCommunicator`."""

    def __init__(self, host: LabCommunicator) -> None:
        self._host = host
        self._sweeper_thread: Optional[threading.Thread] = None
        self._sweeper_stop: threading.Event = threading.Event()
        self._sweeper_ttl_ms: int = 3000
        self._sweeper_tick_ms: int = 500

    def now_ms(self) -> float:
        return time.time() * 1000.0

    async def start(self, target_id: str) -> None:
        host = self._host
        with host._state_lock:
            entry = (host.current_state.get("components") or {}).get(target_id)
            if isinstance(entry, dict) and is_teleop_ready(entry):
                return
            if isinstance(entry, dict):
                already = is_teleop_active(entry)
            else:
                already = False

            if not already:
                reason = refuse_teleop_start(host.current_state, target_id)
                if reason:
                    raise RuntimeError(reason)

            ok = commit_teleop_start(
                host.current_state, target_id, now_ms=self.now_ms()
            )
            if not ok:
                raise RuntimeError(
                    f"start_teleop: component {target_id!r} disappeared between "
                    f"refusal check and commit (race)"
                )
            host.current_state["last_updated"] = datetime.now().isoformat()

        host._null_measurables_for_targets([target_id], persist=False)
        host._persist_state()
        self._maybe_start_sweeper()
        asyncio.create_task(self._finish_start(target_id))

    async def _finish_start(self, target_id: str) -> None:
        host = self._host
        prep_ok = False
        prep_msg = "teleop setup failed"
        try:
            prep_ok, prep_msg = await host._primitive_prepare_teleop(target_id)
        except Exception as e:  # noqa: BLE001
            prep_ok = False
            prep_msg = str(e)
            print(f"{host.log_prefix} teleop prepare failed for {target_id}: {e}")

        initial_pose: Optional[Dict[str, float]] = None
        with host._state_lock:
            entry = (host.current_state.get("components") or {}).get(target_id)
            if not isinstance(entry, dict) or not is_teleop_active(entry):
                return
            if is_teleop_ready(entry):
                return
            if prep_ok:
                initial_pose = _initial_live_pose(host.current_state, target_id)
                hw_tags = getattr(host, "_hardware_teleop_tags", None)
                if hw_tags and target_id in hw_tags:
                    hw_pose = host._teleop_live_get_pose(target_id)
                    if isinstance(hw_pose, dict) and hw_pose.get("rotation") is not None:
                        initial_pose = {
                            "x": float(hw_pose.get("x", initial_pose["x"])),
                            "y": float(hw_pose.get("y", initial_pose["y"])),
                            "z": float(hw_pose.get("z", initial_pose.get("z", DEFAULT_HOVER_Z_MM))),
                            "rotation": float(hw_pose["rotation"]),
                        }
                commit_teleop_ready(
                    host.current_state, target_id, now_ms=self.now_ms()
                )
            else:
                commit_teleop_start_failed(
                    host.current_state, target_id, error=prep_msg
                )
            host.current_state["last_updated"] = datetime.now().isoformat()

        if prep_ok and initial_pose is not None:
            host._teleop_live_start(target_id, initial_pose)
        host._persist_state()
        if not prep_ok:
            print(f"{host.log_prefix} TELEOP setup refused for {target_id}: {prep_msg}")

    async def end(self, target_id: str) -> None:
        host = self._host
        final_pose = host._teleop_live_get_pose(target_id)
        await host._teleop_live_stop(target_id)
        with host._state_lock:
            if final_pose:
                commit_teleop_session_pose(host.current_state, target_id, final_pose)
            commit_teleop_end(host.current_state, target_id)
            host.current_state["last_updated"] = datetime.now().isoformat()
        host._persist_state()

    async def goto(self, target_id: str, body: Dict[str, Any]) -> None:
        host = self._host
        target_pose = body.get("target_pose") or body.get("nominal_pose") or {}
        speed = body.get("speed") or {}
        with host._state_lock:
            entry = (host.current_state.get("components") or {}).get(target_id)
            if not isinstance(entry, dict):
                raise RuntimeError(
                    f"teleop_goto: {target_id!r} not found in lab state."
                )
            if not is_teleop_ready(entry):
                raise RuntimeError(
                    f"teleop_goto: {target_id!r} is not ready for TELEOP; "
                    f"wait for the lab to finish setup or call START_TELEOP first."
                )
            ok = commit_teleop_goto(
                host.current_state,
                target_id,
                now_ms=self.now_ms(),
                target_pose=target_pose,
                speed=speed,
            )
            if not ok:
                raise RuntimeError(
                    f"teleop_goto: commit failed for {target_id!r} "
                    f"(invalid target or state changed under us?)"
                )
            host.current_state["last_updated"] = datetime.now().isoformat()
            cmd = (entry.get("telemetry") or {}).get("teleop", {}).get("command") or {}
            goto_target = cmd.get("target") or target_pose
            goto_speed = cmd.get("speed") or speed
        host._teleop_live_set_goto(target_id, goto_target, goto_speed)
        host._persist_state()

    async def jog(self, target_id: str, jog: Dict[str, Any]) -> None:
        """Backward-compatible alias for :meth:`goto`."""
        await self.goto(target_id, jog)

    def stop_sweeper(self, *, join_timeout_s: float = 1.0) -> None:
        self._sweeper_stop.set()
        t = self._sweeper_thread
        if t is not None and t.is_alive():
            t.join(timeout=join_timeout_s)
        self._sweeper_thread = None

    def _maybe_start_sweeper(self) -> None:
        if self._sweeper_thread is not None and self._sweeper_thread.is_alive():
            return
        _, ttl_ms = read_teleop_safety()
        if ttl_ms <= 0:
            return
        self._sweeper_ttl_ms = int(ttl_ms)
        self._sweeper_stop.clear()
        t = threading.Thread(
            target=self._sweeper_loop,
            name="teleop-sweeper",
            daemon=True,
        )
        self._sweeper_thread = t
        t.start()

    def _sweeper_loop(self) -> None:
        host = self._host
        tick_s = max(0.05, float(self._sweeper_tick_ms) / 1000.0)
        while not self._sweeper_stop.is_set():
            try:
                with host._state_lock:
                    cleared = sweep_stale_teleop_leases(
                        host.current_state,
                        now_ms=self.now_ms(),
                        ttl_ms=float(self._sweeper_ttl_ms),
                    )
                    if cleared:
                        host.current_state["last_updated"] = datetime.now().isoformat()
                if cleared:
                    for tag_id in cleared:
                        final_pose = host._teleop_live_get_pose(tag_id)
                        if final_pose:
                            commit_teleop_session_pose(
                                host.current_state, tag_id, final_pose
                            )
                        host._teleop_live_stop_sync(tag_id)
                    print(
                        f"{host.log_prefix} teleop sweeper cleared stale leases: "
                        f"{cleared}"
                    )
                    try:
                        host._persist_state()
                    except Exception as e:  # noqa: BLE001
                        print(
                            f"{host.log_prefix} teleop sweeper persist failed: {e}"
                        )
            except Exception as e:  # noqa: BLE001
                print(f"{host.log_prefix} teleop sweeper tick failed: {e}")
            self._sweeper_stop.wait(timeout=tick_s)

