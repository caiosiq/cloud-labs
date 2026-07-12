"""Backend registry — discover, probe, and lazy-init communicators."""
from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from lab_communicator.shared.communicator_factory import create_communicator
from lab_communicator.shared.lab_view_config import (
    LabViewManifest,
    LabViewPaths,
    load_lab_view_bundle,
)
from lab_model.backends.context import backend_context
from lab_model.catalog.pins_store import CatalogPinsStore


@dataclass(frozen=True)
class BackendSpec:
    backend_id: str
    label: str
    lab_view_path: str
    enabled: bool = True
    notes: str = ""


@dataclass
class BackendRuntime:
    spec: BackendSpec
    paths: LabViewPaths
    manifest: LabViewManifest
    lab: Any = None
    runtime_manager: Any = None
    catalog_pins_store: Optional[CatalogPinsStore] = None
    control_managers: Dict[str, Any] = field(default_factory=dict)
    availability: str = "unknown"  # ready | unavailable | error
    unavailable_reason: Optional[str] = None
    init_error: Optional[str] = None

    @property
    def backend_id(self) -> str:
        return self.spec.backend_id

    @property
    def communicator(self) -> str:
        return self.manifest.communicator

    @property
    def lab_mode(self) -> str:
        return self.manifest.lab_mode

    def control_dir(self) -> str:
        return self.paths.control_dir

    def recipes_dir(self) -> str:
        return self.paths.recipes_dir

    def list_control_repo_ids(self) -> List[str]:
        root = self.paths.control_dir
        if not os.path.isdir(root):
            return []
        out: List[str] = []
        for name in sorted(os.listdir(root)):
            path = os.path.join(root, name)
            if os.path.isdir(path):
                out.append(name)
        return out


def _default_config_path(project_root: str) -> str:
    override = (os.environ.get("CLOUDLABS_BACKENDS_CONFIG") or "").strip()
    if override:
        if os.path.isabs(override):
            return override
        return os.path.join(project_root, override)
    return os.path.join(project_root, "schemas", "backends.json")


def _probe_real_availability(manifest: LabViewManifest) -> Optional[str]:
    if manifest.communicator != "real":
        return None
    path = (manifest.lab_automation_path or "").strip()
    if not path:
        return "lab_automation_path not set in lab_manifest.json"
    if not os.path.isdir(path):
        return f"lab_automation not found at {path!r} on this host"
    return None


class BackendRegistry:
    """Catalog of backends on this server; lazy communicator init per backend."""

    def __init__(self, project_root: str, specs: List[BackendSpec]) -> None:
        self.project_root = os.path.abspath(project_root)
        self._specs = {s.backend_id: s for s in specs}
        self._runtimes: Dict[str, BackendRuntime] = {}
        self._lock = threading.RLock()

    @classmethod
    def from_project(cls, project_root: str) -> "BackendRegistry":
        config_path = _default_config_path(project_root)
        if not os.path.isfile(config_path):
            raise FileNotFoundError(f"backends config not found: {config_path}")
        with open(config_path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        if not isinstance(doc, dict):
            raise ValueError(f"backends config must be a JSON object: {config_path}")
        rows = doc.get("backends")
        if not isinstance(rows, list):
            raise ValueError(f"backends config missing 'backends' list: {config_path}")
        specs: List[BackendSpec] = []
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            bid = str(raw.get("backend_id") or "").strip()
            lvp = str(raw.get("lab_view_path") or "").strip()
            if not bid or not lvp:
                continue
            specs.append(
                BackendSpec(
                    backend_id=bid,
                    label=str(raw.get("label") or bid).strip() or bid,
                    lab_view_path=lvp,
                    enabled=bool(raw.get("enabled", True)),
                    notes=str(raw.get("notes") or "").strip(),
                )
            )
        if not specs:
            raise ValueError(f"no backends defined in {config_path}")
        reg = cls(project_root, specs)
        reg.probe_all()
        return reg

    def list_specs(self) -> List[BackendSpec]:
        return list(self._specs.values())

    def known_backend_ids(self) -> List[str]:
        return sorted(self._specs.keys())

    def get_runtime(self, backend_id: str, *, init: bool = True) -> BackendRuntime:
        key = backend_id.strip()
        with self._lock:
            rt = self._runtimes.get(key)
            if rt is None:
                spec = self._specs.get(key)
                if spec is None:
                    raise KeyError(f"unknown backend_id {key!r}")
                rt = self._probe_spec(spec)
                self._runtimes[key] = rt
            if init and rt.availability == "ready" and rt.lab is None:
                self._init_communicator(rt)
            return rt

    def first_available(self) -> Optional[BackendRuntime]:
        for bid in self.known_backend_ids():
            rt = self.get_runtime(bid, init=False)
            if rt.availability == "ready":
                self._init_communicator(rt)
                return rt
        return None

    def probe_all(self) -> None:
        for spec in self._specs.values():
            with self._lock:
                self._runtimes[spec.backend_id] = self._probe_spec(spec)
        # Restore geometry for the first ready backend so probing a real
        # layout.json does not leave the process on the wrong storage grid.
        for bid in self.known_backend_ids():
            rt = self._runtimes.get(bid)
            if rt is None or rt.availability != "ready" or not rt.paths.root_dir:
                continue
            try:
                import json

                from lab_model.domain.storage_region import configure_from_layout_document

                with open(rt.paths.layout_json, "r", encoding="utf-8") as fh:
                    doc = json.load(fh)
                if isinstance(doc, dict):
                    configure_from_layout_document(doc)
            except Exception:
                pass
            break

    def _probe_spec(self, spec: BackendSpec) -> BackendRuntime:
        if not spec.enabled:
            return BackendRuntime(
                spec=spec,
                paths=_empty_paths(),
                manifest=_empty_manifest(),
                availability="unavailable",
                unavailable_reason="disabled in backends.json",
            )
        try:
            paths, manifest = load_lab_view_bundle(self.project_root, spec.lab_view_path)
        except BaseException as exc:
            return BackendRuntime(
                spec=spec,
                paths=_empty_paths(),
                manifest=_empty_manifest(),
                availability="unavailable",
                unavailable_reason=str(exc),
            )
        reason = _probe_real_availability(manifest)
        if reason:
            return BackendRuntime(
                spec=spec,
                paths=paths,
                manifest=manifest,
                availability="unavailable",
                unavailable_reason=reason,
            )
        store_dir = os.path.join(paths.root_dir, "catalog_store")
        return BackendRuntime(
            spec=spec,
            paths=paths,
            manifest=manifest,
            catalog_pins_store=CatalogPinsStore(store_dir),
            availability="ready",
        )

    def _init_communicator(self, rt: BackendRuntime) -> None:
        if rt.lab is not None or rt.availability != "ready":
            return
        with self._lock:
            if rt.lab is not None:
                return
            try:
                with backend_context(rt.paths, rt.manifest):
                    from lab_model import motor_rotation_store as motor_rot

                    motor_rot.configure(rt.paths.motor_rotations_json)
                    lab = create_communicator(rt.manifest.communicator)
                    runtime_manager = None
                    if rt.manifest.communicator == "mock":
                        from lab_communicator.runtime_mode import RuntimeLabProxy

                        runtime_manager = RuntimeLabProxy(lab)
                        lab = runtime_manager
                    rt.lab = lab
                    rt.runtime_manager = runtime_manager
                    rt.init_error = None
            except Exception as exc:
                rt.availability = "error"
                rt.init_error = str(exc)
                rt.unavailable_reason = str(exc)

    def to_api_row(
        self,
        rt: BackendRuntime,
        *,
        lease_manager: Any,
        job_hub: Any,
    ) -> Dict[str, Any]:
        row: Dict[str, Any] = {
            "backend_id": rt.backend_id,
            "label": rt.spec.label,
            "communicator": rt.manifest.communicator if rt.availability != "unavailable" else None,
            "lab_mode": rt.manifest.lab_mode if rt.availability != "unavailable" else None,
            "lab_view_path": rt.spec.lab_view_path,
            "availability": rt.availability,
            "unavailable_reason": rt.unavailable_reason,
            "enabled": rt.spec.enabled,
            "notes": rt.spec.notes,
            "control_repos": rt.list_control_repo_ids() if rt.availability != "unavailable" else [],
            "queued_jobs": job_hub.queued_count(rt.backend_id),
        }
        lease = lease_manager.active_lease(rt.backend_id)
        row["session_lease"] = (
            lease.to_api_dict() if lease is not None and hasattr(lease, "to_api_dict") else None
        )
        active_job = job_hub.active_job_id(rt.backend_id)
        row["active_job_id"] = active_job
        if rt.lab is not None:
            try:
                with backend_context(rt.paths, rt.manifest):
                    state = rt.lab.get_lab_state() or {}
                if isinstance(state, dict):
                    components = state.get("components")
                    row["system_status"] = state.get("system_status")
                    row["component_count"] = (
                        len(components) if isinstance(components, dict) else 0
                    )
                    row["health"] = "ok"
                else:
                    row["health"] = "ok"
            except Exception as exc:
                row["health"] = "degraded"
                row["system_status"] = None
                row["init_error"] = str(exc)
        else:
            row["health"] = "uninitialized" if rt.availability == "ready" else "unavailable"
            row["system_status"] = None
        return row


def _empty_paths() -> LabViewPaths:
    return LabViewPaths(
        root_dir="",
        layout_json="",
        laser_lines_json="",
        component_library_json="",
        active_catalog_json="",
        motor_rotations_json="",
        lab_state_json="",
        stored_intent_json="",
        session_checkpoint_json="",
        recipes_dir="",
        states_dir="",
        control_dir="",
        camera_captures_dir="",
        table_cam_preview_json="",
        lab_manifest_json="",
    )


def _empty_manifest() -> LabViewManifest:
    return LabViewManifest(communicator="mock", lab_mode="MOCK")


__all__ = ["BackendRegistry", "BackendRuntime", "BackendSpec"]
