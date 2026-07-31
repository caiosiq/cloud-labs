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
