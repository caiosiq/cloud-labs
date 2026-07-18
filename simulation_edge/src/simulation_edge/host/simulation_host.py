"""Thin simulation host: soft pose bookkeeping + optional MuJoCo process.

v1 physically implements ``MOVE_COMPONENT`` when MuJoCo is enabled
(``SIMULATION_EDGE_MUJOCO=1``). Default soft mode keeps Edge Contract
certify / coordinator wiring runnable without mujoco or a viewer.
"""

from __future__ import annotations

import asyncio
import copy
import os
import threading
from datetime import datetime
from typing import Any, Dict, Iterable, Mapping, Optional


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _pose_from_component(comp: Mapping[str, Any]) -> Dict[str, float]:
    tun = {}
    caps = comp.get("capabilities") if isinstance(comp.get("capabilities"), dict) else {}
    sc = caps.get("statecontrol") if isinstance(caps, dict) else {}
    if isinstance(sc, dict):
        tun = sc.get("tunables") if isinstance(sc.get("tunables"), dict) else {}
    pose = tun.get("nominal_pose") if isinstance(tun, dict) else None
    if not isinstance(pose, dict):
        pose = comp.get("pose") if isinstance(comp.get("pose"), dict) else {}
    return {
        "x": float(pose.get("x") or 0.0),
        "y": float(pose.get("y") or 0.0),
        "rotation": float(pose.get("rotation") or pose.get("yaw") or 0.0),
    }


class SimulationHost:
    """Process-local sim lab used by ``simulation_edge/cloudlabs_edge`` adapters."""

    log_prefix = "[SIM EDGE]"
    supported_primitives = frozenset({"MOVE_COMPONENT"})

    def __init__(
        self,
        state: Mapping[str, Any],
        catalog_rows: Iterable[Mapping[str, Any]],
        layout: Mapping[str, Any],
        *,
        enable_mujoco: Optional[bool] = None,
        show_viewer: Optional[bool] = None,
        realtime: Optional[bool] = None,
    ) -> None:
        self.catalog = [copy.deepcopy(dict(row)) for row in catalog_rows]
        self.catalog_map = {
            str(row["tag_id"]): row for row in self.catalog if row.get("tag_id")
        }
        self.current_state = copy.deepcopy(dict(state))
        self.layout = copy.deepcopy(dict(layout))
        self._lock = threading.RLock()
        self._last_runtime_error: Optional[Dict[str, Any]] = None
        self._poses: Dict[str, Dict[str, float]] = {}
        components = self.current_state.get("components")
        if isinstance(components, dict):
            for tag, comp in components.items():
                if isinstance(comp, dict):
                    self._poses[str(tag)] = _pose_from_component(comp)
        for tag, row in self.catalog_map.items():
            self._poses.setdefault(tag, _pose_from_component(row))

        self._client = None
        self.scene = None
        want = (
            _env_bool("SIMULATION_EDGE_MUJOCO", False)
            if enable_mujoco is None
            else bool(enable_mujoco)
        )
        if want:
            self._start_mujoco(show_viewer=show_viewer, realtime=realtime)

    def _start_mujoco(
        self,
        *,
        show_viewer: Optional[bool],
        realtime: Optional[bool],
    ) -> None:
        from simulation_edge.host.client import MuJoCoProcessClient
        from simulation_edge.host.scene import build_scene_spec

        self.scene = build_scene_spec(self.layout, self.catalog, self.current_state)
        client = MuJoCoProcessClient(
            self.scene,
            show_viewer=show_viewer,
            realtime=realtime,
        )
        try:
            client.start()
        except Exception:
            client.stop()
            raise
        self._client = client

    @property
    def mujoco_enabled(self) -> bool:
        return self._client is not None

    def get_catalog(self) -> list[Dict[str, Any]]:
        return copy.deepcopy(self.catalog)

    def get_lab_state(self) -> Dict[str, Any]:
        with self._lock:
            state = copy.deepcopy(self.current_state)
            state["last_runtime_error"] = copy.deepcopy(self._last_runtime_error)
            state["simulator"] = {
                "backend": "mujoco" if self._client is not None else "soft",
                "mujoco_running": bool(
                    self._client is not None and getattr(self._client, "running", False)
                ),
            }
            return state

    def supports_primitive(self, action: str) -> bool:
        return str(action).upper() in self.supported_primitives

    def simulator_status(self) -> Dict[str, Any]:
        if self._client is None:
            return {"mode": "soft", "running": False}
        return {"mode": "mujoco", **self._client.status()}

    def return_tunables_for_tag(self, tag_id: str) -> Dict[str, Any]:
        tag = str(tag_id or "")
        with self._lock:
            pose = copy.deepcopy(self._poses.get(tag) or {"x": 0.0, "y": 0.0, "rotation": 0.0})
        return {"nominal_pose": pose}

    def _set_pose(self, tag_id: str, x: float, y: float, rotation: float) -> None:
        with self._lock:
            self._poses[tag_id] = {"x": x, "y": y, "rotation": rotation}
            components = self.current_state.setdefault("components", {})
            if not isinstance(components, dict):
                components = {}
                self.current_state["components"] = components
            entry = components.setdefault(tag_id, {})
            if not isinstance(entry, dict):
                entry = {}
                components[tag_id] = entry
            caps = entry.setdefault("capabilities", {})
            if not isinstance(caps, dict):
                caps = {}
                entry["capabilities"] = caps
            sc = caps.setdefault("statecontrol", {})
            if not isinstance(sc, dict):
                sc = {}
                caps["statecontrol"] = sc
            tun = sc.setdefault("tunables", {})
            if not isinstance(tun, dict):
                tun = {}
                sc["tunables"] = tun
            tun["nominal_pose"] = {"x": x, "y": y, "rotation": rotation}

    async def move_component(
        self,
        tag_id: str,
        *,
        x: float,
        y: float,
        rotation: float = 0.0,
    ) -> Dict[str, Any]:
        tag = str(tag_id or "").strip()
        if not tag:
            raise ValueError("tag_id required for MOVE_COMPONENT")
        with self._lock:
            self._last_runtime_error = None
        if self._client is not None:
            try:
                result = await asyncio.to_thread(
                    self._client.move_component,
                    tag,
                    target_x_mm=float(x),
                    target_y_mm=float(y),
                    target_rotation_deg=float(rotation),
                )
            except Exception as exc:
                with self._lock:
                    self._last_runtime_error = {
                        "target_id": tag,
                        "message": str(exc),
                        "timestamp": datetime.now().isoformat(),
                    }
                raise
            out_x = float(result["x_mm"])
            out_y = float(result["y_mm"])
            out_r = float(result["rotation_deg"])
            self._set_pose(tag, out_x, out_y, out_r)
            return {
                "tag_id": tag,
                "x": out_x,
                "y": out_y,
                "rotation": out_r,
                "backend": "mujoco",
            }

        self._set_pose(tag, float(x), float(y), float(rotation))
        return {
            "tag_id": tag,
            "x": float(x),
            "y": float(y),
            "rotation": float(rotation),
            "backend": "soft",
        }

    def shutdown_lab_processes(self) -> None:
        if self._client is not None:
            self._client.stop()
            self._client = None
