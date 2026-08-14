"""Per-backend coordinator working lab-state (Phase 2 isolation).

Durable copy lives under that backend's ``lab_view/lab_state.json``.
In-process mock aliases the host ``RuntimeManager`` (single writer).
"""

from __future__ import annotations

import copy
import json
import os
import threading
from typing import Any, Callable, Dict, Mapping, Optional

from lab_model.coordinator.backends.lab_view_config import atomic_write_json
from lab_model.coordinator.state.runtime_manager import (
    MutationKind,
    RuntimeManager,
    default_runtime_state,
)


class LabStateStore:
    """Mutable working lab-state for one ``backend_id``."""

    def __init__(
        self,
        backend_id: str,
        path: str,
        *,
        runtime: Optional[RuntimeManager] = None,
        host: Any = None,
    ) -> None:
        self.backend_id = (backend_id or "").strip()
        self.path = path
        self._runtime = runtime or RuntimeManager(default_runtime_state())
        self._host = host
        # When a host is aliased, that host owns durability (mock _persist_state).
        self._host_owns_disk = host is not None
        self._seeded = False
        self._last_edge_session_id: Optional[str] = None
        self._persist_lock = threading.RLock()

    @classmethod
    def from_disk(cls, backend_id: str, path: str) -> "LabStateStore":
        store = cls(backend_id, path)
        store.hydrate_from_disk()
        return store

    @classmethod
    def alias_host(cls, backend_id: str, path: str, host: Any) -> "LabStateStore":
        """Share the in-process host RuntimeManager (mock single-writer)."""
        runtime = getattr(host, "_lab_runtime", None)
        if not isinstance(runtime, RuntimeManager):
            runtime = getattr(host, "_lab_runtime_manager", None)
        if not isinstance(runtime, RuntimeManager):
            raise TypeError(
                f"host for backend {backend_id!r} has no RuntimeManager "
                f"(got {type(host)!r})"
            )
        store = cls(backend_id, path, runtime=runtime, host=host)
        store._seeded = True
        return store

    @property
    def runtime(self) -> RuntimeManager:
        return self._runtime

    @property
    def seeded(self) -> bool:
        return self._seeded

    def hydrate_from_disk(self) -> bool:
        """Load ``lab_state.json`` if present. Returns True when a file was read."""
        if not self.path or not os.path.isfile(self.path):
            return False
        try:
            with open(self.path, "r", encoding="utf-8-sig") as fh:
                doc = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            print(
                f"[lab_state] backend={self.backend_id!r} disk hydrate failed: {exc}",
                flush=True,
            )
            return False
        if not isinstance(doc, dict):
            return False
        self._runtime.replace_state(
            doc,
            kind=MutationKind.BOOT_HYDRATE,
            source="disk",
        )
        comps = doc.get("components")
        self._seeded = isinstance(comps, dict) and bool(comps)
        print(
            f"[lab_state] backend={self.backend_id!r} source=disk "
            f"components={len(comps) if isinstance(comps, dict) else 0} "
            f"path={self.path!r}",
            flush=True,
        )
        return True

    def ensure_laser_lines_from_bundle(self, laser_lines_json: str) -> bool:
        """Seed Twin laser overlay from ``laser_lines.json`` when needed.

        Seeds when the overlay is absent/invalid, or when the working copy is
        still the auto-created empty placeholder (``lines: []``, null snap)
        while the bundle file has lines. Once an operator sets a snap id or
        any line entry, emptiness is preserved.
        """
        if self._host_owns_disk or not (laser_lines_json or "").strip():
            return False
        if not os.path.isfile(laser_lines_json):
            return False
        try:
            with open(laser_lines_json, "r", encoding="utf-8-sig") as fh:
                seed = json.load(fh)
        except (OSError, json.JSONDecodeError):
            return False
        if not isinstance(seed, dict):
            return False
        seed_lines = seed.get("lines")
        if not isinstance(seed_lines, list) or not seed_lines:
            return False

        with self._runtime.lock:
            ll = self._runtime.state.get("laser_lines")
            lines = ll.get("lines") if isinstance(ll, dict) else None
            missing = not isinstance(ll, dict) or not isinstance(lines, list)
            empty_placeholder = (
                isinstance(ll, dict)
                and isinstance(lines, list)
                and len(lines) == 0
                and ll.get("snap_line_id") in (None, "")
            )
            if not missing and not empty_placeholder:
                return False

        payload = {
            "version": int(seed.get("version") or 1),
            "snap_line_id": seed.get("snap_line_id"),
            "lines": copy.deepcopy(seed_lines),
        }

        def _mut(state: Dict[str, Any]) -> None:
            state["laser_lines"] = copy.deepcopy(payload)

        self.mutate(
            _mut,
            kind=MutationKind.BOOT_HYDRATE,
            source="laser_lines_bundle",
            persist=True,
        )
        print(
            f"[lab_state] backend={self.backend_id!r} "
            f"source=laser_lines_bundle lines={len(seed_lines)} "
            f"path={laser_lines_json!r}",
            flush=True,
        )
        return True

    def ensure_seeded_from_edge(self, edge: Mapping[str, Any]) -> bool:
        """Seed once from an edge snapshot when working has no components.

        Returns True when a seed write happened.
        """
        if self._host_owns_disk:
            return False
        if not isinstance(edge, Mapping):
            return False
        edge_comps = edge.get("components")
        if not isinstance(edge_comps, dict) or not edge_comps:
            return False
        with self._runtime.lock:
            comps = self._runtime.state.get("components")
            if isinstance(comps, dict) and comps:
                self._seeded = True
                return False
            seed = copy.deepcopy(dict(edge))
            # Drop edge-only overlays that must stay live from the edge.
            seed.pop("runtime_sync", None)
            self._runtime.replace_state(
                seed,
                kind=MutationKind.BOOT_HYDRATE,
                source="edge_seed",
            )
            self._seeded = True
        self.persist()
        print(
            f"[lab_state] backend={self.backend_id!r} source=edge_seed "
            f"components={len(edge_comps)}",
            flush=True,
        )
        return True

    def reconcile_membership_from_edge(self, edge: Mapping[str, Any]) -> bool:
        """Align working-copy component keys with edge inventory membership.

        Edge ``components`` are built from ``inventory.json`` (plus live poses).
        The library may still list parts that are not on the table; those must
        not linger in the Twin working state after inventory edits / edge boot.

        - Drop working tags absent from the edge snapshot.
        - Insert missing edge tags (deep copy).
        - Leave existing shared tags' coordinator FSM / commanded poses intact
          (Twin merge still overlays edge telemetry at read time).

        Returns True when membership changed.
        """
        if self._host_owns_disk or not isinstance(edge, Mapping):
            return False
        edge_comps = edge.get("components")
        if not isinstance(edge_comps, dict):
            return False
        edge_tags = {str(t) for t in edge_comps.keys()}
        removed: list[str] = []
        added: list[str] = []

        with self._runtime.lock:
            comps_now = self._runtime.state.get("components")
            if not isinstance(comps_now, dict):
                comps_now = {}
            stale = [str(t) for t in comps_now.keys() if str(t) not in edge_tags]
            missing = [
                str(t)
                for t, e in edge_comps.items()
                if str(t) not in comps_now and isinstance(e, dict)
            ]
            if not stale and not missing:
                if comps_now or edge_tags:
                    self._seeded = True
                return False

        def _mut(state: Dict[str, Any]) -> None:
            comps = state.get("components")
            if not isinstance(comps, dict):
                comps = {}
                state["components"] = comps
            for tag in list(comps.keys()):
                tid = str(tag)
                if tid not in edge_tags:
                    comps.pop(tag, None)
                    removed.append(tid)
            for tag, e_comp in edge_comps.items():
                tid = str(tag)
                if tid in comps:
                    continue
                if isinstance(e_comp, dict):
                    comps[tid] = copy.deepcopy(e_comp)
                    added.append(tid)

        self.mutate(
            _mut,
            kind=MutationKind.BOOT_HYDRATE,
            source="edge_inventory_reconcile",
            persist=True,
        )
        self._seeded = True
        print(
            f"[lab_state] backend={self.backend_id!r} "
            f"source=edge_inventory_reconcile "
            f"removed={removed} added={added} "
            f"components={len(edge_tags)}",
            flush=True,
        )
        return True

    def reset_from_new_edge_session(self, edge: Mapping[str, Any]) -> bool:
        """Replace working lab-state once per edge process session.

        Used for simulation *and* real HTTP edges: when Terminal 1 (edge)
        restarts it advertises a new ``edge_session_id``. After
        ``runtime_sync.status == ready`` (SYNC_RUNTIME finished — including
        RECORD of physical poses), Twin hydrates ``coordinator_data`` from
        that edge snapshot exactly once so ghosts match the table.

        Twin-only overlays (``alignment_guides``, ``laser_lines``) on the
        prior working copy are preserved across the replace.
        """
        if self._host_owns_disk or not isinstance(edge, Mapping):
            return False
        edge_comps = edge.get("components")
        if not isinstance(edge_comps, dict) or not edge_comps:
            return False

        # Wait for SYNC when the edge advertises runtime_sync — otherwise we
        # would hydrate null/stale poses from a still-booting real edge.
        rs = edge.get("runtime_sync")
        if isinstance(rs, Mapping):
            status = str(rs.get("status") or "").strip().lower()
            if status and status != "ready":
                return False

        simulator = edge.get("simulator")
        session_id = str(
            edge.get("edge_session_id")
            or (
                simulator.get("edge_session_id")
                if isinstance(simulator, Mapping)
                else ""
            )
            or "legacy-edge-session"
        )
        with self._runtime.lock:
            if session_id == self._last_edge_session_id:
                return False
            prior = self._runtime.snapshot_raw()
            seed = copy.deepcopy(dict(edge))
            seed.pop("runtime_sync", None)
            # Keep Twin-authored overlays that the edge does not own.
            for key in ("alignment_guides", "laser_lines"):
                if key in prior and prior[key] is not None:
                    seed[key] = copy.deepcopy(prior[key])
            self._runtime.replace_state(
                seed,
                kind=MutationKind.BOOT_HYDRATE,
                source="edge_session_reset",
            )
            self._seeded = True
            self._last_edge_session_id = session_id
        self.persist()
        print(
            f"[lab_state] backend={self.backend_id!r} "
            f"source=edge_session_reset session_id={session_id!r} "
            f"components={len(edge_comps)}",
            flush=True,
        )
        return True

    def snapshot(self) -> Dict[str, Any]:
        if self._host is not None and hasattr(self._host, "get_lab_state"):
            snap = self._host.get_lab_state()
            if isinstance(snap, dict):
                return snap
        return self._runtime.snapshot_raw()

    def mutate(
        self,
        fn: Callable[[Dict[str, Any]], None],
        *,
        kind: MutationKind = MutationKind.PRIMITIVE_COMMIT,
        source: str = "",
        persist: bool = True,
    ) -> None:
        self._runtime.mutate(fn, kind=kind, source=source or self.backend_id)
        if persist and not self._host_owns_disk:
            self.persist()

    def replace_state(
        self,
        new_state: Dict[str, Any],
        *,
        kind: MutationKind = MutationKind.ADMINISTRATIVE_LOAD,
        source: str = "",
        persist: bool = True,
    ) -> None:
        self._runtime.replace_state(
            new_state,
            kind=kind,
            source=source or self.backend_id,
        )
        self._seeded = True
        if persist and not self._host_owns_disk:
            self.persist()

    def persist(self) -> None:
        """Write working state to this backend's lab_state.json."""
        if not self.path:
            return
        if self._host_owns_disk:
            return
        with self._persist_lock:
            atomic_write_json(self.path, self._runtime.snapshot_raw())


def ensure_lab_state_store(rt: Any) -> LabStateStore:
    """Return (and lazily create) the store on a ``BackendRuntime``."""
    existing = getattr(rt, "lab_state_store", None)
    if isinstance(existing, LabStateStore):
        return existing
    path = ""
    paths = getattr(rt, "paths", None)
    if paths is not None:
        path = getattr(paths, "lab_state_json", "") or ""
    store = LabStateStore.from_disk(getattr(rt, "backend_id", ""), path)
    rt.lab_state_store = store
    return store


__all__ = ["LabStateStore", "ensure_lab_state_store"]
