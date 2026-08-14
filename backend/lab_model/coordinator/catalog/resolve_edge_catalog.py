"""Resolve library + inventory for Twin APIs — edge SoT only.

Never reads ``coordinator_data/`` (that store is VC + working FSM).
See ``docs/BACKEND_ISOLATION.md``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from cloudlabs_edge_dev.edge_data import (
    inventory_tag_ids,
    library_rows,
    library_tag_ids,
    load_json,
    merged_active_rows,
    resolve_edge_root_beside_lab_view,
)
from lab_model.execution.edge.client import CONTRACT_VERSION, HttpEdgeClient


class EdgeCatalogUnavailable(RuntimeError):
    """Library/inventory could not be loaded from the edge (or teaching disk)."""


@dataclass(frozen=True)
class ResolvedEdgeCatalog:
    backend_id: str
    source: str  # edge_http | edge_data_disk
    library: Dict[str, Any]
    inventory: Dict[str, Any]

    def active_rows(self) -> List[Dict[str, Any]]:
        return merged_active_rows(self.library, self.inventory)

    def all_library_rows(self) -> List[Dict[str, Any]]:
        return library_rows(self.library)

    def active_tag_ids(self) -> List[str]:
        return inventory_tag_ids(self.inventory)

    def library_tag_id_list(self) -> List[str]:
        return library_tag_ids(self.library)


def resolve_edge_catalog(rt: Any) -> ResolvedEdgeCatalog:
    """Load library + inventory for ``rt`` (BackendRuntime).

    Order:
    1. HTTP ``edge.base_url`` ``GET /library`` + ``GET /inventory`` (even when a
       poll agent is attached for execute — catalog is not on the poll queue).
    2. Sibling ``cloudlabs_edge/data/`` next to teaching ``lab_view_path``.
    """
    bid = str(getattr(rt, "backend_id", "") or "").strip()
    spec = getattr(rt, "spec", None)
    edge = getattr(spec, "edge", None) if spec is not None else None

    if edge is not None and getattr(edge, "configured", False):
        base = edge.normalized_base_url()
        if base:
            client = HttpEdgeClient(
                base_url=base,
                contract_version=getattr(edge, "contract_version", None)
                or CONTRACT_VERSION,
                lan_direct_ok=bool(getattr(edge, "lan_direct_ok", False)),
            )
            lib = client.get_library()
            inv = client.get_inventory()
            if isinstance(lib, dict) and isinstance(inv, dict):
                return ResolvedEdgeCatalog(
                    backend_id=bid,
                    source="edge_http",
                    library=lib,
                    inventory=inv,
                )
            raise EdgeCatalogUnavailable(
                f"edge HTTP library/inventory unavailable for {bid!r} "
                f"(base_url={base!r}; lib={type(lib).__name__}, inv={type(inv).__name__})"
            )

    lvp = str(getattr(spec, "lab_view_path", "") or "").strip()
    if lvp:
        paths = getattr(rt, "paths", None)
        root_dir = getattr(paths, "root_dir", "") if paths is not None else ""
        if root_dir and os.path.isdir(root_dir):
            lab_view_root = Path(root_dir)
        elif os.path.isabs(lvp):
            lab_view_root = Path(lvp).resolve()
        else:
            lab_view_root = Path(os.path.abspath(lvp))

        edge_root = resolve_edge_root_beside_lab_view(lab_view_root)
        if edge_root is not None:
            lib_path = edge_root / "data" / "library.json"
            inv_path = edge_root / "data" / "inventory.json"
            if lib_path.is_file() and inv_path.is_file():
                return ResolvedEdgeCatalog(
                    backend_id=bid,
                    source="edge_data_disk",
                    library=load_json(lib_path),
                    inventory=load_json(inv_path),
                )

        # Teaching fallback: LabViewPaths may already point at edge library.json
        lib_json = getattr(paths, "component_library_json", "") if paths else ""
        if lib_json and os.path.isfile(lib_json):
            lib = load_json(Path(lib_json))
            inv_path = Path(lib_json).parent / "inventory.json"
            if inv_path.is_file():
                inv = load_json(inv_path)
            else:
                inv = {"schema_version": 1, "entries": {}}
            if isinstance(lib, dict):
                return ResolvedEdgeCatalog(
                    backend_id=bid,
                    source="edge_data_disk",
                    library=lib,
                    inventory=inv if isinstance(inv, dict) else {"schema_version": 1, "entries": {}},
                )

    raise EdgeCatalogUnavailable(
        f"no edge library/inventory for {bid!r} "
        f"(configure edge.base_url or teaching cloudlabs_edge/data/)"
    )


def catalog_map_from_resolved(cat: ResolvedEdgeCatalog) -> Dict[str, Any]:
    """``tag_id`` → library row for preflight / ensemble (active inventory only)."""
    out: Dict[str, Any] = {}
    for row in cat.active_rows():
        if not isinstance(row, dict):
            continue
        tid = str(row.get("tag_id") or row.get("id") or "").strip()
        if tid:
            out[tid] = dict(row)
    return out


def refresh_catalog_map(rt: Any, host: Any = None) -> Dict[str, Any]:
    """Live edge library/inventory → catalog_map; optionally stamp onto ``host``.

    HttpEdgeEnsembleHost snapshots catalog once at communicator init; if the edge
    was down then, ``catalog_map`` stays ``{}`` and real-bench TorchScript preflight
    falsely reports ``no camera-capable catalog row``. Always prefer a fresh resolve
    before ``strict_real_objectives`` checks.
    """
    cat = resolve_edge_catalog(rt)
    cmap = catalog_map_from_resolved(cat)
    if host is not None and hasattr(host, "catalog_map"):
        try:
            host.catalog_map = dict(cmap)
        except Exception:  # noqa: BLE001
            pass
    return cmap


__all__ = [
    "EdgeCatalogUnavailable",
    "ResolvedEdgeCatalog",
    "catalog_map_from_resolved",
    "refresh_catalog_map",
    "resolve_edge_catalog",
]
