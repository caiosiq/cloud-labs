"""Backend registry — discover, probe, and lazy-init edge hosts."""
from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from lab_model.coordinator.backends.lab_view_config import (
    LabViewManifest,
    LabViewPaths,
    load_lab_view_bundle,
)
from lab_model.coordinator.backends.context import backend_context
from lab_model.coordinator.catalog.pins_store import CatalogPinsStore
from lab_model.execution.edge.endpoint import EdgeEndpointConfig, parse_edge_endpoint


def _resolve_lab_view_root(project_root: str, lab_view_path: str) -> str:
    root = lab_view_path
    if not os.path.isabs(root):
        root = os.path.join(project_root, root)
    return os.path.abspath(root)


def warn_shared_lab_view_paths(
    project_root: str,
    specs: List[BackendSpec],
    *,
    strict: Optional[bool] = None,
) -> List[str]:
    """Warn (or fail) when two enabled backends share one lab_view root.

    Returns human-readable warning lines. When ``strict`` is true (or env
    ``CLOUDLABS_STRICT_LAB_VIEW=1``), raises ``ValueError`` instead of only warning.
    """
    if strict is None:
        strict = os.environ.get("CLOUDLABS_STRICT_LAB_VIEW", "").strip().lower() in (
            "1",
            "true",
            "yes",
        )
    by_root: Dict[str, List[str]] = {}
    for spec in specs:
        if not spec.enabled or not (spec.lab_view_path or "").strip():
            continue
        root = _resolve_lab_view_root(project_root, spec.lab_view_path)
        by_root.setdefault(root, []).append(spec.backend_id)
    messages: List[str] = []
    for root, ids in sorted(by_root.items(), key=lambda item: item[0]):
        if len(ids) < 2:
            continue
        joined = ", ".join(ids)
        msg = f"lab_view_path shared by {joined} -> {root}"
        messages.append(msg)
        print(f"[backend] {msg}", flush=True)
    if messages and strict:
        raise ValueError(
            "backends share lab_view_path (set unique paths or unset "
            f"CLOUDLABS_STRICT_LAB_VIEW): {'; '.join(messages)}"
        )
    return messages


def warn_shared_coordinator_data_paths(
    project_root: str,
    specs: List[BackendSpec],
    *,
    strict: Optional[bool] = None,
) -> List[str]:
    """Warn (or fail) when two enabled backends share one coordinator_data root."""
    from lab_model.coordinator.backends.coordinator_data import (
        default_coordinator_data_path,
    )

    if strict is None:
        strict = os.environ.get("CLOUDLABS_STRICT_LAB_VIEW", "").strip().lower() in (
            "1",
            "true",
            "yes",
        )
    by_root: Dict[str, List[str]] = {}
    for spec in specs:
        if not spec.enabled:
            continue
        rel = (spec.coordinator_data_path or default_coordinator_data_path(spec.backend_id)).strip()
        root = _resolve_lab_view_root(project_root, rel)
        by_root.setdefault(root, []).append(spec.backend_id)
    messages: List[str] = []
    for root, ids in sorted(by_root.items(), key=lambda item: item[0]):
        if len(ids) < 2:
            continue
        joined = ", ".join(ids)
        msg = f"coordinator_data shared by {joined} -> {root}"
        messages.append(msg)
        print(f"[backend] {msg}", flush=True)
    if messages and strict:
        raise ValueError(
            "backends share coordinator_data (set unique paths or unset "
            f"CLOUDLABS_STRICT_LAB_VIEW): {'; '.join(messages)}"
        )
    return messages


@dataclass(frozen=True)
class BackendSpec:
    backend_id: str
    label: str
    #: Optional local edge host bundle (mock/sim teaching). Empty for HTTP-only real.
    lab_view_path: str = ""
    #: Thin coordinator store (VC + working FSM + Twin overlays). Auto-created.
    coordinator_data_path: str = ""
    communicator: str = ""
    lab_mode: str = ""
    enabled: bool = True
    notes: str = ""
    description: str = ""
    image: str = ""
    #: Optional Edge Contract HTTP endpoint. Poll attach still wins.
    edge: EdgeEndpointConfig = field(default_factory=EdgeEndpointConfig)


@dataclass
class BackendRuntime:
    spec: BackendSpec
    paths: LabViewPaths
    manifest: LabViewManifest
    lab: Any = None
    runtime_manager: Any = None
    catalog_pins_store: Optional[CatalogPinsStore] = None
    control_managers: Dict[str, Any] = field(default_factory=dict)
    #: Per-backend coordinator working lab-state (Phase 2). None until probe/init.
    lab_state_store: Any = None
    availability: str = "unknown"  # ready | unavailable | error
    unavailable_reason: Optional[str] = None
    init_error: Optional[str] = None

    @property
    def backend_id(self) -> str:
        return self.spec.backend_id

    @property
    def communicator(self) -> str:
        return self.spec.communicator or self.manifest.communicator

    @property
    def lab_mode(self) -> str:
        return self.spec.lab_mode or self.manifest.lab_mode

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


def _probe_real_availability(spec: "BackendSpec", manifest: LabViewManifest) -> Optional[str]:
    """Real backends require an external edge URL (no in-tree RealLabCommunicator)."""
    communicator = spec.communicator or manifest.communicator
    if communicator != "real" and not (spec.backend_id or "").startswith("real."):
        return None
    if spec.edge.configured:
        return None
    return (
        "real backend has no edge.base_url; start lab_automation/cloudlabs_edge "
        "and set schemas/backends.json edge.base_url "
        "(in-tree RealLabCommunicator removed)"
    )


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
        from lab_model.coordinator.backends.coordinator_data import (
            default_coordinator_data_path,
        )

        specs: List[BackendSpec] = []
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            bid = str(raw.get("backend_id") or "").strip()
            if not bid:
                continue
            lvp = str(raw.get("lab_view_path") or "").strip()
            edge = parse_edge_endpoint(raw.get("edge"))
            communicator = str(raw.get("communicator") or "").strip().lower()
            # HTTP-edge real backends need no local edge lab_view; mock/sim do.
            if not lvp and not edge.configured and communicator != "real":
                continue
            cdp = str(raw.get("coordinator_data_path") or "").strip()
            if not cdp:
                cdp = default_coordinator_data_path(bid)
            specs.append(
                BackendSpec(
                    backend_id=bid,
                    label=str(raw.get("label") or bid).strip() or bid,
                    lab_view_path=lvp,
                    coordinator_data_path=cdp,
                    communicator=communicator,
                    lab_mode=str(raw.get("lab_mode") or "").strip().upper(),
                    enabled=bool(raw.get("enabled", True)),
                    notes=str(raw.get("notes") or "").strip(),
                    description=str(raw.get("description") or "").strip(),
                    image=str(raw.get("image") or "").strip(),
                    edge=edge,
                )
            )
        if not specs:
            raise ValueError(f"no backends defined in {config_path}")
        warn_shared_lab_view_paths(os.path.abspath(project_root), specs)
        warn_shared_coordinator_data_paths(os.path.abspath(project_root), specs)
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
        for bid in self.known_backend_ids():
            rt = self._runtimes.get(bid)
            if rt is None:
                continue
            edge_url = ""
            if rt.spec.edge.configured:
                edge_url = rt.spec.edge.base_url or ""
            print(
                f"[backend] backend_id={bid!r} "
                f"coordinator_data={rt.paths.control_dir!r} "
                f"lab_view={rt.spec.lab_view_path or '(none)'} "
                f"edge={edge_url or 'in-process'} "
                f"availability={rt.availability!r}",
                flush=True,
            )
        # Restore geometry for the first ready backend that has a local layout.
        for bid in self.known_backend_ids():
            rt = self._runtimes.get(bid)
            if (
                rt is None
                or rt.availability != "ready"
                or not rt.paths.layout_json
                or not os.path.isfile(rt.paths.layout_json)
            ):
                continue
            try:
                import json

                from lab_model.language.domain.storage_region import configure_from_layout_document

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
        from lab_model.coordinator.backends.coordinator_data import (
            apply_coordinator_overrides,
            coordinator_only_paths,
            ensure_coordinator_data,
        )
        from lab_model.coordinator.state.lab_state_store import LabStateStore

        try:
            coord = ensure_coordinator_data(
                self.project_root,
                spec.backend_id,
                coordinator_data_path=spec.coordinator_data_path,
                migrate_from_lab_view=spec.lab_view_path,
            )
        except BaseException as exc:
            return BackendRuntime(
                spec=spec,
                paths=_empty_paths(),
                manifest=_empty_manifest(),
                availability="unavailable",
                unavailable_reason=f"coordinator_data: {exc}",
            )

        lvp = (spec.lab_view_path or "").strip()
        if lvp:
            try:
                paths, manifest = load_lab_view_bundle(self.project_root, lvp)
            except BaseException as exc:
                return BackendRuntime(
                    spec=spec,
                    paths=_empty_paths(),
                    manifest=_empty_manifest(),
                    availability="unavailable",
                    unavailable_reason=str(exc),
                )
            paths = apply_coordinator_overrides(paths, coord)
        else:
            # HTTP-edge only: no local edge lab_view (library/layout on edge).
            paths = coordinator_only_paths(coord)
            comm = (spec.communicator or "real").strip().lower() or "real"
            mode = (spec.lab_mode or comm.upper()).strip().upper() or "REAL"
            manifest = LabViewManifest(
                communicator=comm,
                lab_mode=mode,
                session_checkpoint=False,
            )
            os.makedirs(paths.states_dir, exist_ok=True)
            os.makedirs(paths.camera_captures_dir, exist_ok=True)

        reason = _probe_real_availability(spec, manifest)
        if reason:
            return BackendRuntime(
                spec=spec,
                paths=paths,
                manifest=manifest,
                availability="unavailable",
                unavailable_reason=reason,
            )
        store_dir = os.path.join(coord.root_dir, "catalog_store")
        os.makedirs(store_dir, exist_ok=True)
        lab_state_store = LabStateStore.from_disk(spec.backend_id, paths.lab_state_json)
        return BackendRuntime(
            spec=spec,
            paths=paths,
            manifest=manifest,
            catalog_pins_store=CatalogPinsStore(store_dir),
            lab_state_store=lab_state_store,
            availability="ready",
        )

    def _init_communicator(self, rt: BackendRuntime) -> None:
        """Lazy-init mock edge host in-process (HTTP edge skips local lab)."""
        if rt.lab is not None or rt.availability != "ready":
            return
        with self._lock:
            if rt.lab is not None:
                return
            # External Edge Contract URL — no in-process host.
            if rt.spec.edge.configured:
                rt.lab = None
                rt.runtime_manager = None
                rt.init_error = None
                if rt.lab_state_store is None:
                    from lab_model.coordinator.state.lab_state_store import LabStateStore

                    rt.lab_state_store = LabStateStore.from_disk(
                        rt.backend_id, rt.paths.lab_state_json
                    )
                return
            # real.* without edge URL already marked unavailable in probe.
            if (rt.manifest.communicator or "").lower() == "real":
                rt.availability = "unavailable"
                rt.unavailable_reason = "real backend requires edge.base_url"
                return
            try:
                with backend_context(rt.paths, rt.manifest):
                    from lab_model.language.domain import motor_rotation_store as motor_rot
                    from lab_model.coordinator.state.lab_state_store import LabStateStore
                    from mock_edge.host.communicator import MockLabCommunicator
                    from mock_edge.host.runtime_mode import RuntimeLabProxy

                    motor_rot.configure(rt.paths.motor_rotations_json)
                    lab = MockLabCommunicator()
                    runtime_manager = RuntimeLabProxy(lab)
                    rt.lab = runtime_manager
                    rt.runtime_manager = runtime_manager
                    # Single writer: Twin store aliases the mock host RuntimeManager.
                    rt.lab_state_store = LabStateStore.alias_host(
                        rt.backend_id, rt.paths.lab_state_json, lab
                    )
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
            "description": rt.spec.description or None,
            "image": rt.spec.image or None,
            "communicator": rt.communicator if rt.availability != "unavailable" else None,
            "lab_mode": rt.lab_mode if rt.availability != "unavailable" else None,
            "lab_view_path": rt.spec.lab_view_path or None,
            "coordinator_data_path": rt.spec.coordinator_data_path or None,
            "availability": rt.availability,
            "unavailable_reason": rt.unavailable_reason,
            "enabled": rt.spec.enabled,
            "notes": rt.spec.notes,
            "edge": rt.spec.edge.to_api_dict(),
            "control_repos": rt.list_control_repo_ids() if rt.availability != "unavailable" else [],
            "queued_jobs": job_hub.queued_count(rt.backend_id),
        }
        lease = lease_manager.active_lease(rt.backend_id)
        row["session_lease"] = (
            lease.to_api_dict() if lease is not None and hasattr(lease, "to_api_dict") else None
        )
        active_job = job_hub.active_job_id(rt.backend_id)
        row["active_job_id"] = active_job
        edge = None
        disconnect = None
        stale_after = 5.0
        try:
            from lab_model.execution.edge import edge_agent_registry as _edge_reg

            stale_after = float(_edge_reg.stale_after_s)
            edge = _edge_reg.get_for_backend(rt.backend_id)
            disconnect = _edge_reg.last_disconnect(rt.backend_id)
        except Exception:
            edge = None
            disconnect = None
        row["edge_attached"] = edge is not None
        row["edge_agent"] = edge.to_api_dict() if edge is not None else None
        row["edge_offline"] = disconnect
        row["edge_stale_after_s"] = (
            stale_after if edge is not None or disconnect else None
        )
        if edge is not None:
            row["health"] = "edge_attached"
            if isinstance(edge.lab_state, dict):
                row["system_status"] = edge.lab_state.get("system_status")
                components = edge.lab_state.get("components")
                row["component_count"] = (
                    len(components) if isinstance(components, dict) else 0
                )
        elif disconnect is not None:
            row["health"] = "edge_offline"
            row["system_status"] = None
        elif rt.lab is not None:
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


__all__ = [
    "BackendRegistry",
    "BackendRuntime",
    "BackendSpec",
    "warn_shared_coordinator_data_paths",
    "warn_shared_lab_view_paths",
]
