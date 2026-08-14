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
        # Default fail-closed: shared teaching paths break isolation.
        # Opt out with CLOUDLABS_STRICT_LAB_VIEW=0.
        raw = os.environ.get("CLOUDLABS_STRICT_LAB_VIEW", "1").strip().lower()
        strict = raw not in ("0", "false", "no", "off")
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
            "backends share lab_view_path (set unique paths or "
            f"CLOUDLABS_STRICT_LAB_VIEW=0): {'; '.join(messages)}"
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
        raw = os.environ.get("CLOUDLABS_STRICT_LAB_VIEW", "1").strip().lower()
        strict = raw not in ("0", "false", "no", "off")
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
            "backends share coordinator_data (set unique paths or "
            f"CLOUDLABS_STRICT_LAB_VIEW=0): {'; '.join(messages)}"
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
    #: Tracked Twin overlay seeds (laser lines) when there is no lab_view_path.
    coordinator_seed_path: str = ""
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
    #: Cached flat edge bench layout (``lab_bounds_mm`` at top) for storage_region.
    edge_layout_cache: Optional[Dict[str, Any]] = field(default=None, repr=False)
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
            csp = str(raw.get("coordinator_seed_path") or "").strip()
            specs.append(
                BackendSpec(
                    backend_id=bid,
                    label=str(raw.get("label") or bid).strip() or bid,
                    lab_view_path=lvp,
                    coordinator_data_path=cdp,
                    coordinator_seed_path=csp,
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
        # Seed process geometry from the first ready backend's edge bench
        # (HTTP /bench or teaching disk). Per-request Twin still resolves
        # layout via resolve_edge_bench for the selected backend_id.
        for bid in self.known_backend_ids():
            rt = self._runtimes.get(bid)
            if rt is None or rt.availability != "ready":
                continue
            try:
                from lab_model.coordinator.catalog.resolve_edge_bench import (
                    resolve_edge_bench,
                )
                from lab_model.language.domain.storage_region import (
                    configure_from_layout_document,
                )

                configure_from_layout_document(resolve_edge_bench(rt).layout)
            except Exception:
                continue
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
                coordinator_seed_path=spec.coordinator_seed_path,
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
            # Session checkpoint is Twin-owned under coordinator_data (restore
            # software tunables after noisy re-localize — no robot motion).
            paths = coordinator_only_paths(coord)
            comm = (spec.communicator or "real").strip().lower() or "real"
            mode = (spec.lab_mode or comm.upper()).strip().upper() or "REAL"
            session_checkpoint = True
            pos_mm, yaw_deg = 8.0, 10.0
            stale_h = 168.0
            if paths.lab_manifest_json and os.path.isfile(paths.lab_manifest_json):
                try:
                    with open(paths.lab_manifest_json, "r", encoding="utf-8-sig") as fh:
                        raw_m = json.load(fh)
                    if isinstance(raw_m, dict):
                        if "session_checkpoint" in raw_m:
                            session_checkpoint = bool(raw_m.get("session_checkpoint"))
                        block = raw_m.get("session_reconciliation")
                        if isinstance(block, dict):
                            if block.get("position_mm") is not None:
                                pos_mm = float(block["position_mm"])
                            if block.get("yaw_deg") is not None:
                                yaw_deg = float(block["yaw_deg"])
                            if block.get("stale_warning_hours") is not None:
                                stale_h = float(block["stale_warning_hours"])
                except (OSError, TypeError, ValueError, json.JSONDecodeError):
                    pass
            manifest = LabViewManifest(
                communicator=comm,
                lab_mode=mode,
                session_checkpoint=session_checkpoint,
                reconciliation_position_mm=pos_mm,
                reconciliation_yaw_deg=yaw_deg,
                reconciliation_stale_warning_hours=stale_h,
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
        # In-process teaching host: fail probe if the package is not importable
        # (common when PYTHONPATH omits mock_backend/src). Otherwise Twin shows
        # ready, then hangs on connect with a silent ImportError.
        import_err = _probe_inprocess_host_import(spec, manifest)
        if import_err:
            print(
                f"[backend] backend_id={spec.backend_id!r} probe FAILED: {import_err}",
                flush=True,
            )
            return BackendRuntime(
                spec=spec,
                paths=paths,
                manifest=manifest,
                availability="error",
                unavailable_reason=import_err,
                init_error=import_err,
            )
        store_dir = os.path.join(coord.root_dir, "catalog_store")
        os.makedirs(store_dir, exist_ok=True)
        lab_state_store = LabStateStore.from_disk(spec.backend_id, paths.lab_state_json)
        lab_state_store.ensure_laser_lines_from_bundle(paths.laser_lines_json)
        return BackendRuntime(
            spec=spec,
            paths=paths,
            manifest=manifest,
            catalog_pins_store=CatalogPinsStore(store_dir),
            lab_state_store=lab_state_store,
            availability="ready",
        )

    def _init_communicator(self, rt: BackendRuntime) -> None:
        """Lazy-init mock backend host in-process (HTTP edge skips local lab)."""
        if rt.lab is not None or rt.availability != "ready":
            return
        with self._lock:
            if rt.lab is not None:
                return
            # External Edge Contract URL — HTTP OptimizeHost over Twin lab-state store.
            if rt.spec.edge.configured:
                from lab_model.coordinator.state.lab_state_store import LabStateStore
                from lab_model.execution.edge.ensemble_host import HttpEdgeEnsembleHost

                if rt.lab_state_store is None:
                    rt.lab_state_store = LabStateStore.from_disk(
                        rt.backend_id, rt.paths.lab_state_json
                    )
                base = rt.spec.edge.normalized_base_url() or ""
                catalog: dict = {}
                try:
                    from lab_model.coordinator.catalog.resolve_edge_catalog import (
                        resolve_edge_catalog,
                    )

                    cat = resolve_edge_catalog(rt)
                    from lab_model.coordinator.catalog.resolve_edge_catalog import (
                        catalog_map_from_resolved,
                    )

                    catalog = catalog_map_from_resolved(cat)
                    print(
                        f"[backend] edge catalog for ensemble host "
                        f"backend={rt.backend_id!r} source={cat.source} "
                        f"active_tags={len(catalog)}",
                        flush=True,
                    )
                except Exception as exc:  # noqa: BLE001
                    print(
                        f"[backend] edge catalog for ensemble host skipped: {exc}",
                        flush=True,
                    )
                rt.lab = HttpEdgeEnsembleHost(
                    backend_id=rt.backend_id,
                    base_url=base,
                    state_store=rt.lab_state_store,
                    contract_version=getattr(rt.spec.edge, "contract_version", None)
                    or "1.1.0",
                    catalog_map=catalog,
                )
                rt.runtime_manager = None
                rt.init_error = None
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
                    from mock_backend.host.communicator import MockLabCommunicator
                    from mock_backend.host.runtime_mode import RuntimeLabProxy

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
                msg = _format_host_init_error(exc)
                rt.availability = "error"
                rt.init_error = msg
                rt.unavailable_reason = msg
                print(
                    f"[backend] backend_id={rt.backend_id!r} init FAILED: {msg}",
                    flush=True,
                )

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
        elif rt.availability == "error":
            row["health"] = "error"
            row["system_status"] = None
            if rt.init_error:
                row["init_error"] = rt.init_error
        else:
            row["health"] = "uninitialized" if rt.availability == "ready" else "unavailable"
            row["system_status"] = None
        return row


def _format_host_init_error(exc: BaseException) -> str:
    msg = str(exc).strip() or type(exc).__name__
    if isinstance(exc, ModuleNotFoundError) or (
        isinstance(exc, ImportError) and "mock_backend" in msg
    ):
        return (
            f"{msg}. Teaching mock needs mock_backend on PYTHONPATH "
            "(e.g. mock_backend/src), or restart via scripts/ops/run_cloud_labs_backend.ps1 "
            "/ python backend/main.py after the path bootstrap fix."
        )
    if isinstance(exc, ImportError) and "simulation_edge" in msg:
        return (
            f"{msg}. Simulation host needs simulation_edge/src on PYTHONPATH."
        )
    return msg


def _probe_inprocess_host_import(
    spec: BackendSpec, manifest: LabViewManifest
) -> Optional[str]:
    """Return an error string if the in-process teaching host cannot be imported."""
    if spec.edge.configured:
        return None
    comm = (spec.communicator or manifest.communicator or "").strip().lower()
    if comm == "real" or (spec.backend_id or "").startswith("real."):
        return None
    # mock / sim teaching (and default communicator=mock)
    try:
        if "sim" in (spec.backend_id or "").lower() or comm in ("sim", "simulation", "mujoco"):
            import simulation_edge  # noqa: F401
        else:
            import mock_backend.host.communicator  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        return _format_host_init_error(exc)
    return None


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
