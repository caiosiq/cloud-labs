"""Resolve edge-owned kernel catalog for Twin ``GET /api/kernels``.

Remote backends: HTTP ``GET /kernels`` (or sibling edge ``kernels/`` on disk).
Never treat coordinator ``schemas/kernels/`` as the live catalog for HTTP edges.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from cloudlabs_edge_dev.edge_data import resolve_edge_root_beside_lab_view
from cloudlabs_edge_dev.optimization import list_kernels as list_edge_manifest_kernels
from lab_model.execution.edge.client import CONTRACT_VERSION, HttpEdgeClient


class EdgeKernelsUnavailable(RuntimeError):
    """Kernel catalog could not be loaded from the edge (or teaching disk)."""


@dataclass(frozen=True)
class ResolvedEdgeKernels:
    backend_id: str
    source: str  # edge_http | edge_data_disk | coordinator_fallback
    kernels: List[Dict[str, Any]]


def _normalize_row(raw: Dict[str, Any], *, backend: str = "any") -> Dict[str, Any]:
    kid = str(raw.get("id") or "").strip()
    scope = str(raw.get("scope") or ("session" if kid.startswith("session.") else "catalog"))
    row: Dict[str, Any] = {
        "id": kid,
        "label": str(raw.get("label") or kid),
        "description": str(raw.get("description") or ""),
        "backend": backend,
        "phase": str(raw.get("phase") or "edge"),
        "hooks": list(raw.get("hooks") or ()),
        "scope": scope,
        "runtime": str(raw.get("runtime") or "torchscript"),
    }
    for key in (
        "artifact",
        "artifact_path",
        "artifact_present",
        "output_kind",
        "feature_names",
        "digest",
        "inputs",
        "outputs",
    ):
        if key in raw and raw[key] is not None:
            row[key] = raw[key]
    return row


def _rows_from_manifest(kernels_dir: Path, *, backend_id: str) -> List[Dict[str, Any]]:
    tag = (backend_id.split(".", 1)[0] if backend_id else "any") or "any"
    out: List[Dict[str, Any]] = []
    for raw in list_edge_manifest_kernels(kernels_dir):
        if not isinstance(raw, dict) or not raw.get("id"):
            continue
        out.append(_normalize_row(dict(raw), backend=tag))
    return sorted(out, key=lambda r: r["id"])


def resolve_edge_kernels(rt: Any) -> ResolvedEdgeKernels:
    """Load kernel catalog for ``rt`` (BackendRuntime).

    Order:
    1. HTTP ``edge.base_url`` ``GET /kernels``
    2. Sibling ``cloudlabs_edge/kernels/`` next to teaching ``lab_view_path``
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
            payload = client.get_kernels()
            if isinstance(payload, dict) and isinstance(payload.get("kernels"), list):
                rows = [
                    _normalize_row(dict(r), backend=bid.split(".", 1)[0] or "any")
                    for r in payload["kernels"]
                    if isinstance(r, dict) and r.get("id")
                ]
                return ResolvedEdgeKernels(
                    backend_id=bid,
                    source="edge_http",
                    kernels=sorted(rows, key=lambda r: r["id"]),
                )
            raise EdgeKernelsUnavailable(
                f"edge HTTP GET /kernels unavailable for {bid!r} at {base!r}"
            )

    lab_view = getattr(spec, "lab_view_path", None) if spec is not None else None
    if lab_view:
        try:
            edge_root = resolve_edge_root_beside_lab_view(Path(lab_view))
        except Exception:  # noqa: BLE001
            edge_root = None
        if edge_root is not None:
            kdir = Path(edge_root) / "kernels"
            if (kdir / "manifest.json").is_file():
                return ResolvedEdgeKernels(
                    backend_id=bid,
                    source="edge_data_disk",
                    kernels=_rows_from_manifest(kdir, backend_id=bid),
                )

    raise EdgeKernelsUnavailable(f"no edge kernel catalog for {bid!r}")


def edge_kernel_ids_for_remote(rt: Any) -> Optional[Set[str]]:
    """Return edge ``GET /kernels`` ids when the backend is HTTP-edge remote.

    ``None`` means use coordinator TorchScript preflight (in-process mock/sim
    without ``edge.base_url``). A non-``None`` set means skip coordinator
    ``ensure_torchscript_ready`` and validate against the edge catalog.
    """
    spec = getattr(rt, "spec", None)
    edge = getattr(spec, "edge", None) if spec is not None else None
    if edge is None or not getattr(edge, "configured", False):
        return None
    resolved = resolve_edge_kernels(rt)
    return {
        str(row.get("id") or "").strip()
        for row in resolved.kernels
        if row.get("id")
    }


__all__ = [
    "EdgeKernelsUnavailable",
    "ResolvedEdgeKernels",
    "edge_kernel_ids_for_remote",
    "resolve_edge_kernels",
]
