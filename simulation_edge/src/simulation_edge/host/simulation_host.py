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
        from simulation_edge.component_registry import merged_library

        root = self._edge_data_root()
        if root is None:
            return {"schema_version": 1, "components": {}}
        return merged_library(root)

    def get_inventory(self) -> Dict[str, Any]:
        """Return runtime inventory derived from the current simulation state.

        The on-disk inventory seeds startup.  Runtime additions/removals are
        intentionally session state (save a preset to make an arrangement
        reusable) while custom component definitions persist separately.
        """

        entries: Dict[str, Any] = {}
        with self._lock:
            components = self.current_state.get("components") or {}
            for tag_id, component in components.items():
                if not isinstance(component, Mapping):
                    continue
                statecontrol = component.get("statecontrol")
                tunables = (
                    statecontrol.get("tunables")
                    if isinstance(statecontrol, Mapping)
                    else {}
                )
                if not isinstance(tunables, Mapping):
                    tunables = {}
                presence = str(tunables.get("presence") or "breadboard").lower()
                placement = {
                    "breadboard": "table",
                    "storage": "storage",
                    "off_table": "off",
                }.get(presence, presence)
                storage = tunables.get("storage")
                slot = storage.get("slot") if isinstance(storage, Mapping) else None
                entries[str(tag_id)] = {
                    "placement": placement,
                    "storage_slot": copy.deepcopy(slot),
                    "localize": placement != "off",
                }
        return {"schema_version": 1, "entries": entries}

    def _replace_catalog(self, rows: Iterable[Mapping[str, Any]]) -> None:
        catalog = [copy.deepcopy(dict(row)) for row in rows if isinstance(row, Mapping)]
        catalog_map = {
            str(row["tag_id"]): row for row in catalog if row.get("tag_id")
        }
        with self._lock:
            self.catalog = catalog
            self.catalog_map = catalog_map

    def _all_simulation_catalog_rows(
        self,
        *,
        registry: Optional[Mapping[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        from simulation_edge.component_registry import merged_library

        root = self._edge_data_root()
        if root is None:
            raise RuntimeError("simulation edge data directory is unavailable")
        library = merged_library(root, registry=registry)
        components = library.get("components") or {}
        return [
            copy.deepcopy(dict(row))
            for row in components.values()
            if isinstance(row, Mapping)
        ]

    def _assert_component_admin_idle(self) -> None:
        with self._lock:
            status = str(self.current_state.get("system_status") or "").upper()
        if status != "IDLE":
            raise RuntimeError(
                f"simulation component administration requires IDLE; system is {status or 'UNKNOWN'}"
            )

    def _pair_clearance_margin_mm(self) -> float:
        danger = self.layout.get("danger_zone")
        if isinstance(danger, Mapping):
            try:
                margin = float(danger.get("padding_mm", 5.0))
                if math.isfinite(margin) and margin >= 0:
                    return margin
            except (TypeError, ValueError):
                pass
        return 5.0

    def _component_agent_record(
        self,
        tag_id: str,
        row: Mapping[str, Any],
        *,
        active: bool,
        placement: Optional[str],
    ) -> Dict[str, Any]:
        size = row.get("size")
        if isinstance(size, Mapping):
            try:
                width = float(size.get("width") or 62.0)
                depth = float(size.get("height") or 62.0)
            except (TypeError, ValueError):
                width = depth = 62.0
        elif isinstance(size, (int, float)):
            width = depth = float(size)
        else:
            width = depth = 62.0
        try:
            height = float(row.get("height_mm") or 60.0)
        except (TypeError, ValueError):
            height = 60.0
        radius = math.hypot(width, depth) / 2.0
        return {
            "tag_id": tag_id,
            "name": row.get("name"),
            "type": row.get("type"),
            "parameters": copy.deepcopy(row.get("parameters") or {}),
            "housing": {
                "footprint_mm": {"width": width, "depth": depth},
                "height_mm": height,
                "clearance_radius_mm": round(radius, 3),
            },
            "active": active,
            "placement": placement if active else None,
        }

    def list_simulation_components(self) -> Dict[str, Any]:
        from simulation_edge.component_registry import merged_library, tag_number

        root = self._edge_data_root()
        if root is None:
            raise RuntimeError("simulation edge data directory is unavailable")
        library = merged_library(root)
        inventory = self.get_inventory().get("entries") or {}
        rows: List[Dict[str, Any]] = []
        for tag_id in sorted(library["components"], key=tag_number):
            row = library["components"][tag_id]
            active = tag_id in inventory
            rows.append(
                self._component_agent_record(
                    tag_id,
                    row,
                    active=active,
                    placement=(
                        inventory.get(tag_id, {}).get("placement") if active else None
                    ),
                )
            )
        return {
            "schema_version": 1,
            "pair_clearance_margin_mm": self._pair_clearance_margin_mm(),
            "components": rows,
        }

    def next_simulation_component_tag(self) -> Dict[str, Any]:
        from simulation_edge.component_registry import merged_library, next_tag_id

        root = self._edge_data_root()
        if root is None:
            raise RuntimeError("simulation edge data directory is unavailable")
        known = set(merged_library(root)["components"])
        with self._lock:
            known.update(str(tag) for tag in (self.current_state.get("components") or {}))
        return {"tag_id": next_tag_id(known)}

    def get_simulation_component(self, tag_id: str) -> Dict[str, Any]:
        from simulation_edge.component_registry import merged_library, validate_tag_id

        root = self._edge_data_root()
        if root is None:
            raise RuntimeError("simulation edge data directory is unavailable")
        tag = validate_tag_id(tag_id)
        components = merged_library(root)["components"]
        row = components.get(tag)
        if not isinstance(row, Mapping):
            raise ValueError(f"unknown component tag: {tag}")
        inventory = self.get_inventory().get("entries") or {}
        result = self._component_agent_record(
            tag,
            row,
            active=tag in inventory,
            placement=(
                inventory.get(tag, {}).get("placement") if tag in inventory else None
            ),
        )
        result["pair_clearance_margin_mm"] = self._pair_clearance_margin_mm()
        return result

    def _commit_registry_change(
        self,
        registry: Mapping[str, Any],
        *,
        restart_if_active: Optional[str] = None,
    ) -> Dict[str, Any]:
        from simulation_edge.component_registry import save_registry

        root = self._edge_data_root()
        if root is None:
            raise RuntimeError("simulation edge data directory is unavailable")
        rows = self._all_simulation_catalog_rows(registry=registry)
        with self._lock:
            active = restart_if_active in (self.current_state.get("components") or {})
            state = copy.deepcopy(self.current_state)
        previous_rows = self.get_catalog()
        if active:
            self.restart_mujoco(lab_state=state, catalog_rows=rows)
        else:
            self._replace_catalog(rows)
        try:
            save_registry(root, registry)
        except Exception:
            if active:
                self.restart_mujoco(lab_state=state, catalog_rows=previous_rows)
            else:
                self._replace_catalog(previous_rows)
            raise
        result: Dict[str, Any] = {"runtime_restarted": bool(active)}
        if active:
            result["lab_state"] = self.get_lab_state()
        return result

    def define_simulation_component(
        self,
        tag_id: str,
        definition: Mapping[str, Any],
    ) -> Dict[str, Any]:
        from simulation_edge.component_registry import define_component

        self._assert_component_admin_idle()
        root = self._edge_data_root()
        if root is None:
            raise RuntimeError("simulation edge data directory is unavailable")
        registry, _ = define_component(root, tag_id, definition)
        result = self._commit_registry_change(registry)
        return {**result, "component": self.get_simulation_component(tag_id)}

    def configure_simulation_component(
        self,
        tag_id: str,
        patch: Mapping[str, Any],
    ) -> Dict[str, Any]:
        from simulation_edge.component_registry import configure_component

        self._assert_component_admin_idle()
        root = self._edge_data_root()
        if root is None:
            raise RuntimeError("simulation edge data directory is unavailable")
        registry, _ = configure_component(root, tag_id, patch)
        result = self._commit_registry_change(registry, restart_if_active=tag_id)
        return {**result, "component": self.get_simulation_component(tag_id)}

    def reset_simulation_component(self, tag_id: str) -> Dict[str, Any]:
        from simulation_edge.component_registry import reset_component_override

        self._assert_component_admin_idle()
        root = self._edge_data_root()
        if root is None:
            raise RuntimeError("simulation edge data directory is unavailable")
        registry, _ = reset_component_override(root, tag_id)
        result = self._commit_registry_change(registry, restart_if_active=tag_id)
        return {**result, "component": self.get_simulation_component(tag_id)}

    def delete_simulation_component(self, tag_id: str) -> Dict[str, Any]:
        from simulation_edge.component_registry import delete_custom_component, validate_tag_id

        self._assert_component_admin_idle()
        tag = validate_tag_id(tag_id)
        with self._lock:
            if tag in (self.current_state.get("components") or {}):
                raise RuntimeError(
                    f"{tag} is active; remove it from the simulation before deleting its definition"
                )
        root = self._edge_data_root()
        if root is None:
            raise RuntimeError("simulation edge data directory is unavailable")
        registry = delete_custom_component(root, tag)
        self._commit_registry_change(registry)
        return {"deleted": tag, "runtime_restarted": False}

    def _storage_pose(self, slot_i: int, slot_j: int) -> Dict[str, float]:
        storage = self.layout.get("storage")
        bounds = storage.get("bounds_mm") if isinstance(storage, Mapping) else None
        if not isinstance(storage, Mapping) or not isinstance(bounds, Mapping):
            raise ValueError("simulation layout does not define storage")
        nx = int(storage.get("grid_nx") or 0)
        ny = int(storage.get("grid_ny") or 0)
        if nx <= 0 or ny <= 0 or not (0 <= slot_i < nx and 0 <= slot_j < ny):
            raise ValueError(f"storage slot ({slot_i}, {slot_j}) is outside the grid")
        width = (float(bounds["x_max"]) - float(bounds["x_min"])) / nx
        height = (float(bounds["y_max"]) - float(bounds["y_min"])) / ny
        return {
            "x": float(bounds["x_min"]) + (slot_i + 0.5) * width,
            "y": float(bounds["y_min"]) + (slot_j + 0.5) * height,
            "rotation": 0.0,
        }

    @staticmethod
    def _component_state_entry(
        tag_id: str,
        row: Mapping[str, Any],
        *,
        pose: Mapping[str, Any],
        presence: str,
        slot: Optional[Mapping[str, int]] = None,
    ) -> Dict[str, Any]:
        nominal = {
            "x": float(pose["x"]),
            "y": float(pose["y"]),
            "rotation": float(pose.get("rotation") or 0.0),
        }
        in_storage = presence == "storage"
        return {
            "id": tag_id,
            "type": str(row.get("type") or "GENERIC_COMPONENT"),
            "statecontrol": {
                "tunables": {
                    "presence": presence,
                    "nominal_pose": copy.deepcopy(nominal),
                    "reported_pose": copy.deepcopy(nominal),
                    "storage": {
                        "in_storage": in_storage,
                        "slot": copy.deepcopy(dict(slot)) if slot else None,
                    },
                    "placement": {
                        "mode": "STORAGE" if in_storage else "MANUAL"
                    },
                },
                "measurables": {"pose": copy.deepcopy(nominal)},
            },
            "telemetry": {"teleop": {}, "live_feed": {}},
        }

    def insert_simulation_component(
        self,
        tag_id: str,
        *,
        x: Optional[float] = None,
        y: Optional[float] = None,
        rotation: float = 0.0,
        storage_slot: Optional[Mapping[str, int]] = None,
    ) -> Dict[str, Any]:
        from simulation_edge.component_registry import merged_library, validate_tag_id

        self._assert_component_admin_idle()
        tag = validate_tag_id(tag_id)
        root = self._edge_data_root()
        if root is None:
            raise RuntimeError("simulation edge data directory is unavailable")
        library = merged_library(root)
        row = library["components"].get(tag)
        if not isinstance(row, Mapping):
            raise ValueError(f"unknown component tag: {tag}")
        with self._lock:
            state = copy.deepcopy(self.current_state)
        components = state.setdefault("components", {})
        if tag in components:
            raise ValueError(f"{tag} is already active in the simulation")
        if storage_slot is not None:
            slot = {"i": int(storage_slot["i"]), "j": int(storage_slot["j"])}
            pose = self._storage_pose(slot["i"], slot["j"])
            presence = "storage"
        else:
            if x is None or y is None:
                raise ValueError("table insertion requires x and y")
            pose = {"x": float(x), "y": float(y), "rotation": float(rotation)}
            slot = None
            presence = "breadboard"
        components[tag] = self._component_state_entry(
            tag, row, pose=pose, presence=presence, slot=slot
        )
        state["system_status"] = "IDLE"
        rows = [copy.deepcopy(dict(value)) for value in library["components"].values()]
        self.restart_mujoco(lab_state=state, catalog_rows=rows)
        return {
            "runtime_restarted": True,
            "tag_id": tag,
            "presence": presence,
            "pose": pose,
            "lab_state": self.get_lab_state(),
        }

    def remove_simulation_component(self, tag_id: str) -> Dict[str, Any]:
        from simulation_edge.component_registry import validate_tag_id

        self._assert_component_admin_idle()
        tag = validate_tag_id(tag_id)
        with self._lock:
            state = copy.deepcopy(self.current_state)
        components = state.get("components") or {}
        if tag not in components:
            raise ValueError(f"{tag} is not active in the simulation")
        components.pop(tag)
        state["components"] = components
        self.restart_mujoco(
            lab_state=state,
            catalog_rows=self._all_simulation_catalog_rows(),
        )
        return {
            "runtime_restarted": True,
            "removed": tag,
            "lab_state": self.get_lab_state(),
        }

    def clear_simulation_components(self, *, scope: str = "table") -> Dict[str, Any]:
        self._assert_component_admin_idle()
        selected = str(scope or "table").strip().lower()
        if selected not in {"table", "all"}:
            raise ValueError("clear scope must be 'table' or 'all'")
        with self._lock:
            state = copy.deepcopy(self.current_state)
        components = state.get("components") or {}
        removed: List[str] = []
        for tag_id, component in list(components.items()):
            statecontrol = component.get("statecontrol") if isinstance(component, Mapping) else None
            tunables = statecontrol.get("tunables") if isinstance(statecontrol, Mapping) else None
            presence = str(
                (tunables.get("presence") if isinstance(tunables, Mapping) else None)
                or "breadboard"
            )
            if selected == "all" or presence == "breadboard":
                components.pop(tag_id, None)
                removed.append(str(tag_id))
        state["components"] = components
        self.restart_mujoco(
            lab_state=state,
            catalog_rows=self._all_simulation_catalog_rows(),
        )
        return {
            "runtime_restarted": True,
            "scope": selected,
            "removed": sorted(removed),
            "lab_state": self.get_lab_state(),
        }

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
        catalog_rows: Optional[Iterable[Mapping[str, Any]]] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            previous_state = copy.deepcopy(self.current_state)
            previous_poses = copy.deepcopy(self._poses)
            previous_catalog = copy.deepcopy(self.catalog)
            candidate_state = copy.deepcopy(self.current_state)
            candidate_catalog = (
                [copy.deepcopy(dict(row)) for row in catalog_rows]
                if catalog_rows is not None
                else copy.deepcopy(self.catalog)
            )
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
                candidate_state = synced
            candidate_components = candidate_state.get("components") or {}
            candidate_poses = {
                str(tag): _pose_from_component(component)
                for tag, component in candidate_components.items()
                if isinstance(component, Mapping)
            }

        # Validate the complete scene before closing the working viewer.  Scene
        # construction catches unknown/invalid geometry and out-of-bounds poses.
        from simulation_edge.host.scene import build_scene_spec

        candidate_scene = build_scene_spec(self.layout, candidate_catalog, candidate_state)
        if candidate_scene.spawn_adjustments_mm:
            adjusted = ", ".join(sorted(candidate_scene.spawn_adjustments_mm))
            raise ValueError(
                "simulation state contains overlapping startup components "
                f"({adjusted}); edit their nominal poses before loading it"
            )

        client = self._client
        if client is not None:
            client.stop()
        self._client = None
        self.scene = None
        with self._lock:
            self._last_runtime_error = None
            self.current_state = candidate_state
            self._poses = candidate_poses
            self.catalog = candidate_catalog
            self.catalog_map = {
                str(row["tag_id"]): row
                for row in self.catalog
                if row.get("tag_id")
            }
        try:
            self._start_mujoco(show_viewer=show_viewer, realtime=realtime)
        except Exception as restart_exc:
            # Best-effort rollback keeps a bad preset from permanently replacing
            # the last working simulation state.
            if self._client is not None:
                self._client.stop()
            self._client = None
            self.scene = None
            with self._lock:
                self.current_state = previous_state
                self._poses = previous_poses
                self.catalog = previous_catalog
                self.catalog_map = {
                    str(row["tag_id"]): row
                    for row in self.catalog
                    if row.get("tag_id")
                }
            try:
                self._start_mujoco(show_viewer=show_viewer, realtime=realtime)
            except Exception as rollback_exc:
                raise RuntimeError(
                    f"MuJoCo restart failed ({restart_exc}); rollback also failed ({rollback_exc})"
                ) from restart_exc
            raise
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

    def _allocate_explicit_storage_slot(
        self,
        tag_id: str,
        slot_i: int,
        slot_j: int,
    ) -> tuple[float, float, int, int]:
        x_min, x_max, y_min, y_max, nx, ny = self._storage_grid()
        i = int(slot_i)
        j = int(slot_j)
        if not (0 <= i < nx and 0 <= j < ny):
            raise ValueError(f"storage slot ({i}, {j}) is outside the {nx}x{ny} grid")

        occupied: set[tuple[int, int]] = set()
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
                slot = storage_state.get("slot") if isinstance(storage_state, dict) else None
                if isinstance(slot, dict):
                    occupied.add((int(slot["i"]), int(slot["j"])))
        if (i, j) in occupied:
            raise ValueError(f"storage slot ({i}, {j}) is already occupied")

        cell_width = (x_max - x_min) / nx
        cell_height = (y_max - y_min) / ny
        width_mm, depth_mm = self._component_size_mm(tag_id)
        if max(width_mm, depth_mm) > min(cell_width, cell_height) + 1e-6:
            raise ValueError(
                f"component footprint {width_mm:g}x{depth_mm:g} mm does not fit "
                f"cell {cell_width:.1f}x{cell_height:.1f} mm"
            )

        center_x, center_y = self._storage_slot_center(i, j)
        danger = self.layout.get("danger_zone")
        danger_radius = float(danger.get("radius_mm") or 0.0) if isinstance(danger, Mapping) else 0.0
        danger_padding = float(danger.get("padding_mm") or 0.0) if isinstance(danger, Mapping) else 0.0
        component_radius = math.hypot(width_mm, depth_mm) / 2.0
        if math.hypot(center_x, center_y) < danger_radius + danger_padding + component_radius:
            raise ValueError(
                f"storage slot ({i}, {j}) center ({center_x:.1f},{center_y:.1f}) "
                "is inside the danger zone"
            )
        return center_x, center_y, i, j

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
            pose = {"x": out_x, "y": out_y, "rotation": out_r}
            return {
                "tag_id": tag,
                "x": out_x,
                "y": out_y,
                "rotation": out_r,
                "pose": pose,
                "backend": "mujoco",
            }

        self._set_pose(tag, float(x), float(y), float(rotation))
        pose = {"x": float(x), "y": float(y), "rotation": float(rotation)}
        return {
            "tag_id": tag,
            "x": float(x),
            "y": float(y),
            "rotation": float(rotation),
            "pose": pose,
            "backend": "soft",
        }

    async def store_component(
        self,
        tag_id: str,
        *,
        slot_i: Optional[int] = None,
        slot_j: Optional[int] = None,
    ) -> Dict[str, Any]:
        tag = str(tag_id or "").strip()
        if not tag:
            raise ValueError("tag_id required for STORE_COMPONENT")
        explicit = slot_i is not None or slot_j is not None
        if (slot_i is None) != (slot_j is None):
            raise ValueError("slot_i and slot_j must be provided together")
        with self._lock:
            tunables = self._component_tunables(tag)
            presence = self._presence(tunables)
            if presence == "storage" and not explicit:
                raise ValueError(f"{tag} is already in storage")
            if presence not in {"breadboard", "storage"}:
                raise ValueError(f"{tag} must be on the table or in storage")
            if explicit:
                x, y, selected_i, selected_j = self._allocate_explicit_storage_slot(
                    tag,
                    int(slot_i),
                    int(slot_j),
                )
            else:
                x, y, selected_i, selected_j = self._allocate_storage_slot(tag)
        result = await self.move_component(
            tag,
            x=x,
            y=y,
            rotation=0.0,
            grasp_policy="short_edges",
            pickup_context="storage" if presence == "storage" else "table",
        )
        slot = {"i": selected_i, "j": selected_j}
        self._set_storage_state(tag, in_storage=True, slot=slot)
        return {
            **result,
            "presence": "storage",
            "slot_i": selected_i,
            "slot_j": selected_j,
            "storage": {"in_storage": True, "slot": slot},
            "mode": "explicit" if explicit else "autopack",
        }

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
