"""Thin simulation host: soft pose bookkeeping + optional MuJoCo process.

v1 physically implements ``MOVE_COMPONENT`` when MuJoCo is enabled
(``SIMULATION_EDGE_MUJOCO=1``). Default soft mode keeps Edge Contract
certify / coordinator wiring runnable without mujoco or a viewer.
"""

from __future__ import annotations

import asyncio
import copy
import math
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _planner_backend_from_env() -> str:
    return (
        os.getenv("SIMULATION_EDGE_PLANNER")
        or os.getenv("SIMULATION_EDGE_MUJOCO_PLANNER")
        or os.getenv("CLOUDLAB_MUJOCO_PLANNER")
        or "radial"
    ).strip().lower()


def _pose_from_component(comp: Mapping[str, Any]) -> Dict[str, float]:
    pose = None
    for parent_key in ("statecontrol", "capabilities"):
        parent = comp.get(parent_key) if isinstance(comp.get(parent_key), dict) else {}
        sc = parent.get("statecontrol") if parent_key == "capabilities" else parent
        if isinstance(sc, dict):
            tun = sc.get("tunables") if isinstance(sc.get("tunables"), dict) else {}
            candidate = tun.get("nominal_pose") if isinstance(tun, dict) else None
            if isinstance(candidate, dict):
                pose = candidate
                break
    if not isinstance(pose, dict):
        tun = comp.get("tunables") if isinstance(comp.get("tunables"), dict) else {}
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
    supported_primitives = frozenset(
        {
            "MOVE_COMPONENT",
            "STORE_COMPONENT",
            "PLACE_FROM_STORAGE",
            "REPACK_STORAGE",
            "RECENTER_IN_STORAGE",
        }
    )

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
        self.edge_session_id = uuid.uuid4().hex
        self.layout = copy.deepcopy(dict(layout))
        self._lock = threading.RLock()
        # Alias for coordinator tunable commit helpers (``commit_nominal_pose``).
        self._state_lock = self._lock
        self._last_runtime_error: Optional[Dict[str, Any]] = None
        self._poses: Dict[str, Dict[str, float]] = {}
        self._runtime_sync: Dict[str, Any] = {
            "status": "pending",
            "errors": [],
            "completed_at": None,
        }
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
        self._maybe_boot_sync_runtime()

    def _persist_state(self) -> None:
        """No-op persist hook for shared tunable commit helpers."""

    def _edge_data_root(self) -> Optional[Path]:
        candidate = Path(__file__).resolve().parents[3] / "cloudlabs_edge"
        if (candidate / "data" / "library.json").is_file():
            return candidate
        return None

    def get_component_library(self) -> Dict[str, Any]:
        from cloudlabs_edge_dev.edge_data import load_library

        root = self._edge_data_root()
        if root is None:
            return {"schema_version": 1, "components": {}}
        return load_library(root, strict_recordable=False, validate=True)

    def get_inventory(self) -> Dict[str, Any]:
        from cloudlabs_edge_dev.edge_data import load_inventory

        root = self._edge_data_root()
        if root is None:
            return {"schema_version": 1, "entries": {}}
        return load_inventory(root)

    def set_runtime_sync_status(
        self,
        status: str,
        *,
        errors: Optional[List[Any]] = None,
    ) -> None:
        status_norm = str(status or "pending").strip().lower()
        self._runtime_sync = {
            "status": status_norm,
            "errors": list(errors or []),
            "completed_at": (
                datetime.now(timezone.utc).isoformat()
                if status_norm == "ready"
                else None
            ),
        }
        print(
            f"[lab_init] {self.log_prefix} runtime_sync status={status_norm} "
            f"errors={len(self._runtime_sync.get('errors') or [])}"
        )

    def runtime_sync_status(self) -> str:
        return str((self._runtime_sync or {}).get("status") or "pending")

    def is_runtime_ready(self) -> bool:
        return self.runtime_sync_status() == "ready"

    def _maybe_boot_sync_runtime(self) -> None:
        if _env_bool("CLOUDLABS_SKIP_RUNTIME_SYNC", False):
            print(f"{self.log_prefix} runtime_sync skipped (CLOUDLABS_SKIP_RUNTIME_SYNC)")
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(self._boot_sync_runtime())
            return
        print(
            f"[lab_init] {self.log_prefix} runtime_sync scheduled "
            "(on running event loop)"
        )
        self.set_runtime_sync_status("running")
        task = loop.create_task(self._boot_sync_runtime())

        def _boot_done(t: asyncio.Task) -> None:
            if t.cancelled():
                return
            exc = t.exception()
            if exc is not None:
                print(
                    f"[lab_init] {self.log_prefix} runtime_sync boot task "
                    f"failed: {exc}"
                )

        task.add_done_callback(_boot_done)
        self._boot_sync_task = task

    async def _boot_sync_runtime(self) -> None:
        from lab_model.language.primitives.macros.sync_runtime import run_sync_runtime
        from lab_model.language.primitives.schemas import SyncRuntimeBody

        self.set_runtime_sync_status("running")
        try:
            delay_s = float(os.getenv("CLOUDLABS_MOCK_INIT_DELAY_S", "2") or "2")
        except ValueError:
            delay_s = 2.0
        if delay_s > 0:
            print(
                f"[lab_init] {self.log_prefix} runtime_sync measuring "
                f"(delay={delay_s:g}s)"
            )
            await asyncio.sleep(delay_s)
        try:
            result = await run_sync_runtime(
                self, SyncRuntimeBody(action="SYNC_RUNTIME")
            )
            status = "ready"
            errors: List[Any] = []
            if isinstance(result, dict):
                errors = list(result.get("errors") or [])
                if errors or result.get("status") == "failed":
                    status = "failed"
            self.set_runtime_sync_status(status, errors=errors)
        except Exception as exc:  # noqa: BLE001
            self.set_runtime_sync_status("failed", errors=[str(exc)])
            print(f"{self.log_prefix} SYNC_RUNTIME boot failed: {exc}")

    def _start_mujoco(
        self,
        *,
        show_viewer: Optional[bool],
        realtime: Optional[bool],
    ) -> None:
        from simulation_edge.host.client import MuJoCoProcessClient
        from simulation_edge.host.runtime import (
            MUJOCO_PLANNER_CUSTOM_IK,
            MUJOCO_PLANNER_RADIAL,
        )
        from simulation_edge.host.scene import build_scene_spec

        planner_backend = _planner_backend_from_env()
        if planner_backend in {"custom", "custom_ik", "ik"}:
            planner_backend = MUJOCO_PLANNER_CUSTOM_IK
        elif planner_backend in {"radial", "radial_ik", "base_radial"}:
            planner_backend = MUJOCO_PLANNER_RADIAL

        self.scene = build_scene_spec(self.layout, self.catalog, self.current_state)
        self._apply_scene_spawn_adjustments()
        client = MuJoCoProcessClient(
            self.scene,
            show_viewer=show_viewer,
            realtime=realtime,
            planner_backend=planner_backend,
        )
        try:
            client.start()
        except Exception:
            client.stop()
            raise
        self._client = client

    def restart_mujoco(
        self,
        *,
        show_viewer: Optional[bool] = None,
        realtime: Optional[bool] = None,
        lab_state: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        client = self._client
        if client is not None:
            client.stop()
        self._client = None
        self.scene = None
        with self._lock:
            self._last_runtime_error = None
            if lab_state is not None:
                components = lab_state.get("components")
                if not isinstance(components, Mapping):
                    raise ValueError(
                        "MuJoCo restart lab_state must contain components"
                    )
                synced = copy.deepcopy(dict(lab_state))
                # These fields are generated live by this edge and must not be
                # imported from the coordinator's previous edge snapshot.
                for key in (
                    "active_backend_id",
                    "edge_agent",
                    "edge_attached",
                    "edge_offline",
                    "edge_session_id",
                    "edge_stale_after_s",
                    "edge_state_source",
                    "last_runtime_error",
                    "runtime_sync",
                    "session_lease",
                    "simulator",
                ):
                    synced.pop(key, None)
                self.current_state = synced
                self._poses = {
                    str(tag): _pose_from_component(component)
                    for tag, component in components.items()
                    if isinstance(component, Mapping)
                }
        self._start_mujoco(show_viewer=show_viewer, realtime=realtime)
        return self.simulator_status()

    @property
    def mujoco_enabled(self) -> bool:
        return self._client is not None

    def get_catalog(self) -> list[Dict[str, Any]]:
        return copy.deepcopy(self.catalog)

    def get_lab_state(self) -> Dict[str, Any]:
        with self._lock:
            state = copy.deepcopy(self.current_state)
            state["last_runtime_error"] = copy.deepcopy(self._last_runtime_error)
            sim_status = self._client.status() if self._client is not None else {}
            simulator = {
                "backend": "mujoco" if self._client is not None else "soft",
                "mujoco_running": bool(
                    self._client is not None and getattr(self._client, "running", False)
                ),
                **sim_status,
                "edge_session_id": self.edge_session_id,
            }
            spawn_adjustments = (
                getattr(self.scene, "spawn_adjustments_mm", {}) if self.scene else {}
            )
            if spawn_adjustments:
                simulator["spawn_adjustments"] = copy.deepcopy(spawn_adjustments)
            state["simulator"] = simulator
            state["edge_session_id"] = self.edge_session_id
            state["runtime_sync"] = dict(self._runtime_sync or {"status": "pending"})
            return state

    def supports_primitive(self, action: str) -> bool:
        return str(action).upper() in self.supported_primitives

    def simulator_status(self) -> Dict[str, Any]:
        if self._client is None:
            return {"mode": "soft", "running": False}
        return {"mode": "mujoco", **self._client.status()}

    def _apply_scene_spawn_adjustments(self) -> None:
        if self.scene is None:
            return
        for tag_id, pose in self.scene.spawn_adjustments_mm.items():
            self._set_pose(
                str(tag_id),
                float(pose["x"]),
                float(pose["y"]),
                float(pose.get("rotation", 0.0)),
            )

    def return_tunables_for_tag(self, tag_id: str) -> Dict[str, Any]:
        tag = str(tag_id or "")
        with self._lock:
            pose = copy.deepcopy(self._poses.get(tag) or {"x": 0.0, "y": 0.0, "rotation": 0.0})
        return {"nominal_pose": pose}

    async def set_exposure_time_ms(self, target_id: str, exposure_time_ms: float) -> None:
        with self._lock:
            tun = self._component_tunables(str(target_id))
            tun["exposure_time_ms"] = float(exposure_time_ms)

    async def set_output_power_mw(self, target_id: str, output_power_mw: float) -> None:
        with self._lock:
            tun = self._component_tunables(str(target_id))
            tun["output_power_mw"] = float(output_power_mw)

    async def set_motor_setpoint(
        self, target_id: str, motor_id: int, angle_deg: float
    ) -> None:
        with self._lock:
            tun = self._component_tunables(str(target_id))
            motors = tun.setdefault("nominal_motor_positions", {})
            if not isinstance(motors, dict):
                motors = {}
                tun["nominal_motor_positions"] = motors
            motors[str(int(motor_id))] = float(angle_deg)

    async def record_tunables(
        self,
        tag_ids: Optional[List[str]] = None,
        tunable_paths: Optional[List[str]] = None,
        force_rescan: bool = True,
    ) -> Dict[str, Any]:
        """RECORD_TUNABLES — copy soft world poses/values into tunables."""
        _ = force_rescan
        from cloudlabs_edge_dev.edge_data import default_localize_tag_ids

        paths = [str(p) for p in (tunable_paths or ["nominal_pose"]) if str(p).strip()]
        ids = [str(t).strip() for t in (tag_ids or []) if str(t).strip()]
        if not ids:
            ids = default_localize_tag_ids(self.get_inventory())

        values: Dict[str, Dict[str, Any]] = {}
        poses: Dict[str, Any] = {}
        for tid in ids:
            recorded: Dict[str, Any] = {}
            for path in paths:
                if path == "nominal_pose":
                    with self._lock:
                        pose = copy.deepcopy(
                            self._poses.get(tid)
                            or {"x": 0.0, "y": 0.0, "rotation": 0.0}
                        )
                    self._set_pose(
                        tid,
                        float(pose["x"]),
                        float(pose["y"]),
                        float(pose.get("rotation", 0.0)),
                    )
                    recorded[path] = pose
                    poses[tid] = pose
                else:
                    with self._lock:
                        try:
                            tun = self._component_tunables(tid)
                            value = copy.deepcopy(tun.get(path))
                        except ValueError:
                            value = None
                    if value is not None:
                        with self._lock:
                            tun = self._component_tunables(tid)
                            tun[path] = copy.deepcopy(value)
                    recorded[path] = value
            values[tid] = recorded
            poses.setdefault(tid, recorded.get("nominal_pose"))
            print(f"[lab_init] {self.log_prefix} RECORD_TUNABLES tag={tid} paths={paths} ok")
        return {
            "tag_ids": ids,
            "tunable_paths": paths,
            "values": values,
            "poses": poses,
        }

    async def localize_components(
        self,
        tag_ids: Optional[List[str]] = None,
        force_rescan: bool = True,
    ) -> Dict[str, Any]:
        return await self.record_tunables(
            tag_ids=tag_ids,
            tunable_paths=["nominal_pose"],
            force_rescan=force_rescan,
        )

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
            sc = entry.setdefault("statecontrol", {})
            if not isinstance(sc, dict):
                sc = {}
                entry["statecontrol"] = sc
            tun = sc.setdefault("tunables", {})
            if not isinstance(tun, dict):
                tun = {}
                sc["tunables"] = tun
            tun["nominal_pose"] = {"x": x, "y": y, "rotation": rotation}
            tun.pop("reported_pose", None)

            measurables = sc.setdefault("measurables", {})
            if isinstance(measurables, dict):
                measured_pose = measurables.setdefault("pose", {})
                if isinstance(measured_pose, dict):
                    measured_pose.update({"x": x, "y": y, "rotation": rotation})

    def _component_tunables(self, tag_id: str) -> Dict[str, Any]:
        components = self.current_state.setdefault("components", {})
        if not isinstance(components, dict):
            components = {}
            self.current_state["components"] = components
        entry = components.get(tag_id)
        if not isinstance(entry, dict):
            entry = {"tag_id": tag_id, "statecontrol": {"tunables": {}, "measurables": {}}}
            components[tag_id] = entry
        statecontrol = entry.setdefault("statecontrol", {})
        if not isinstance(statecontrol, dict):
            statecontrol = {}
            entry["statecontrol"] = statecontrol
        tunables = statecontrol.setdefault("tunables", {})
        if not isinstance(tunables, dict):
            tunables = {}
            statecontrol["tunables"] = tunables
        return tunables

    @staticmethod
    def _presence(tunables: Mapping[str, Any]) -> str:
        return str(tunables.get("presence") or "").strip().lower()

    def _storage_grid(self) -> tuple[float, float, float, float, int, int]:
        storage = self.layout.get("storage")
        if not isinstance(storage, Mapping):
            raise ValueError("layout.storage is required for storage primitives")
        bounds = storage.get("bounds_mm")
        if not isinstance(bounds, Mapping):
            raise ValueError("layout.storage.bounds_mm is required for storage primitives")
        x_min = float(bounds["x_min"])
        x_max = float(bounds["x_max"])
        y_min = float(bounds["y_min"])
        y_max = float(bounds["y_max"])
        nx = int(storage.get("grid_nx") or 0)
        ny = int(storage.get("grid_ny") or 0)
        if nx <= 0 or ny <= 0 or x_max <= x_min or y_max <= y_min:
            raise ValueError("layout storage grid must have positive dimensions")
        return x_min, x_max, y_min, y_max, nx, ny

    def _component_size_mm(self, tag_id: str) -> tuple[float, float]:
        row = self.catalog_map.get(tag_id) or {}
        size = row.get("size") if isinstance(row, Mapping) else None
        if isinstance(size, Mapping):
            return float(size.get("width") or 62.0), float(size.get("height") or 62.0)
        if isinstance(size, (int, float)):
            return float(size), float(size)
        return 62.0, 62.0

    def _storage_slot_center(
        self,
        slot_i: int,
        slot_j: int,
    ) -> tuple[float, float]:
        x_min, x_max, y_min, y_max, nx, ny = self._storage_grid()
        i = int(slot_i)
        j = int(slot_j)
        if not (0 <= i < nx and 0 <= j < ny):
            raise ValueError(f"storage slot ({i}, {j}) is outside the grid")
        cell_width = (x_max - x_min) / nx
        cell_height = (y_max - y_min) / ny
        return (
            x_min + (i + 0.5) * cell_width,
            y_min + (j + 0.5) * cell_height,
        )

    def _assigned_storage_slot(
        self,
        tag_id: str,
    ) -> Optional[tuple[int, int]]:
        tunables = self._component_tunables(tag_id)
        storage_state = tunables.get("storage")
        slot = (
            storage_state.get("slot")
            if isinstance(storage_state, Mapping)
            else None
        )
        if not isinstance(slot, Mapping):
            return None
        try:
            i = int(slot["i"])
            j = int(slot["j"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"{tag_id} has invalid storage slot metadata") from exc
        self._storage_slot_center(i, j)
        return i, j

    def _allocate_storage_slot(
        self,
        tag_id: str,
        *,
        excluded_slots: Iterable[tuple[int, int]] = (),
    ) -> tuple[float, float, int, int]:
        x_min, x_max, y_min, y_max, nx, ny = self._storage_grid()
        cell_width = (x_max - x_min) / nx
        cell_height = (y_max - y_min) / ny
        occupied: set[tuple[int, int]] = set()
        occupied.update((int(i), int(j)) for i, j in excluded_slots)
        components = self.current_state.get("components")
        if isinstance(components, dict):
            for other_tag, entry in components.items():
                if other_tag == tag_id or not isinstance(entry, dict):
                    continue
                statecontrol = entry.get("statecontrol")
                tunables = (
                    statecontrol.get("tunables")
                    if isinstance(statecontrol, dict)
                    else None
                )
                if not isinstance(tunables, dict) or self._presence(tunables) != "storage":
                    continue
                storage_state = tunables.get("storage")
                slot = (
                    storage_state.get("slot")
                    if isinstance(storage_state, dict)
                    else None
                )
                if isinstance(slot, dict):
                    occupied.add((int(slot["i"]), int(slot["j"])))

        width_mm, depth_mm = self._component_size_mm(tag_id)
        danger = self.layout.get("danger_zone")
        danger_radius = float(danger.get("radius_mm") or 0.0) if isinstance(danger, Mapping) else 0.0
        danger_padding = float(danger.get("padding_mm") or 0.0) if isinstance(danger, Mapping) else 0.0
        component_radius = math.hypot(width_mm, depth_mm) / 2.0
        if max(width_mm, depth_mm) > min(cell_width, cell_height) + 1e-6:
            raise ValueError(f"{tag_id} does not fit a storage cell")

        for j in range(ny):
            for i in range(nx):
                if (i, j) in occupied:
                    continue
                center_x = x_min + (i + 0.5) * cell_width
                center_y = y_min + (j + 0.5) * cell_height
                if math.hypot(center_x, center_y) < danger_radius + danger_padding + component_radius:
                    continue
                return center_x, center_y, i, j
        raise ValueError("no reachable free storage slot is available")

    def _set_storage_state(
        self,
        tag_id: str,
        *,
        in_storage: bool,
        slot: Optional[Dict[str, int]],
    ) -> None:
        with self._lock:
            tunables = self._component_tunables(tag_id)
            tunables["presence"] = "storage" if in_storage else "breadboard"
            tunables["storage"] = {
                "in_storage": bool(in_storage),
                "slot": copy.deepcopy(slot),
            }
            tunables["placement"] = {
                "mode": "STORAGE" if in_storage else "MANUAL"
            }
            self.current_state["last_updated"] = datetime.now().isoformat()

    async def move_component(
        self,
        tag_id: str,
        *,
        x: float,
        y: float,
        rotation: float = 0.0,
        grasp_policy: str = "short_edges",
        pickup_context: str = "table",
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
                    grasp_policy=grasp_policy,
                    pickup_context=pickup_context,
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

    async def store_component(self, tag_id: str) -> Dict[str, Any]:
        tag = str(tag_id or "").strip()
        with self._lock:
            tunables = self._component_tunables(tag)
            if self._presence(tunables) == "storage":
                raise ValueError(f"{tag} is already in storage")
            x, y, slot_i, slot_j = self._allocate_storage_slot(tag)
        result = await self.move_component(
            tag,
            x=x,
            y=y,
            rotation=0.0,
            grasp_policy="short_edges",
        )
        slot = {"i": slot_i, "j": slot_j}
        self._set_storage_state(tag, in_storage=True, slot=slot)
        return {**result, "presence": "storage", "storage": {"in_storage": True, "slot": slot}}

    async def place_from_storage(
        self,
        tag_id: str,
        *,
        x: float,
        y: float,
        rotation: float = 0.0,
    ) -> Dict[str, Any]:
        tag = str(tag_id or "").strip()
        with self._lock:
            tunables = self._component_tunables(tag)
            if self._presence(tunables) != "storage":
                raise ValueError(f"{tag} is not in storage")
            x_min, x_max, y_min, y_max, _, _ = self._storage_grid()
            if x_min <= float(x) < x_max and y_min <= float(y) < y_max:
                raise ValueError("place-from-storage target must be outside storage")
        result = await self.move_component(
            tag,
            x=float(x),
            y=float(y),
            rotation=float(rotation),
            grasp_policy="short_edges",
            pickup_context="storage",
        )
        self._set_storage_state(tag, in_storage=False, slot=None)
        return {
            **result,
            "presence": "breadboard",
            "storage": {"in_storage": False, "slot": None},
        }

    async def repack_storage_slot(self, tag_id: str) -> Dict[str, Any]:
        tag = str(tag_id or "").strip()
        with self._lock:
            tunables = self._component_tunables(tag)
            if self._presence(tunables) != "storage":
                raise ValueError(f"{tag} is not in storage")
            current_slot = self._assigned_storage_slot(tag)
            excluded = (current_slot,) if current_slot is not None else ()
            x, y, slot_i, slot_j = self._allocate_storage_slot(
                tag,
                excluded_slots=excluded,
            )
        result = await self.move_component(
            tag,
            x=x,
            y=y,
            rotation=0.0,
            grasp_policy="short_edges",
            pickup_context="storage",
        )
        slot = {"i": slot_i, "j": slot_j}
        self._set_storage_state(tag, in_storage=True, slot=slot)
        return {
            **result,
            "presence": "storage",
            "storage": {"in_storage": True, "slot": slot},
        }

    async def recenter_stored_in_inventory(
        self,
        tag_id: str,
    ) -> Dict[str, Any]:
        tag = str(tag_id or "").strip()
        with self._lock:
            tunables = self._component_tunables(tag)
            if self._presence(tunables) != "storage":
                raise ValueError(f"{tag} is not in storage")
            current_slot = self._assigned_storage_slot(tag)
            if current_slot is None:
                raise ValueError(f"{tag} has no assigned storage slot")
            slot_i, slot_j = current_slot
            x, y = self._storage_slot_center(slot_i, slot_j)
        result = await self.move_component(
            tag,
            x=x,
            y=y,
            rotation=0.0,
            grasp_policy="short_edges",
            pickup_context="storage",
        )
        slot = {"i": slot_i, "j": slot_j}
        self._set_storage_state(tag, in_storage=True, slot=slot)
        return {
            **result,
            "presence": "storage",
            "storage": {"in_storage": True, "slot": slot},
        }

    def shutdown_lab_processes(self) -> None:
        if self._client is not None:
            self._client.stop()
            self._client = None
