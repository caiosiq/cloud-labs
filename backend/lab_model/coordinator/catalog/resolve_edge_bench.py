"""Resolve bench layout for Twin APIs — prefer edge GET /bench.

Never treats ``coordinator_data/`` as layout SoT.
See ``docs/BACKEND_ISOLATION.md``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from cloudlabs_edge_dev.edge_data import load_json, resolve_edge_root_beside_lab_view
from lab_model.execution.edge.client import CONTRACT_VERSION, HttpEdgeClient


class EdgeBenchUnavailable(RuntimeError):
    """Bench layout could not be loaded from the edge (or teaching disk)."""


@dataclass(frozen=True)
class ResolvedEdgeBench:
    backend_id: str
    source: str  # edge_http | edge_bench_disk | teaching_lab_view
    #: Flat Twin-facing layout document (``lab_bounds_mm`` at top level).
    layout: Dict[str, Any]
    #: Raw edge ``GET /bench`` body when available (may wrap ``layout``).
    bench: Optional[Dict[str, Any]] = None


def unwrap_bench_layout(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Return a flat layout dict for Twin (``lab_bounds_mm`` at top).

    Edge Contract ``GET /bench`` wraps geometry under ``layout``; teaching
    ``lab_view/layout.json`` is already flat.
    """
    if not isinstance(doc, dict):
        raise EdgeBenchUnavailable("bench document must be a JSON object")
    nested = doc.get("layout")
    if isinstance(nested, dict) and isinstance(nested.get("lab_bounds_mm"), dict):
        return dict(nested)
    if isinstance(doc.get("lab_bounds_mm"), dict):
        return dict(doc)
    raise EdgeBenchUnavailable(
        "bench/layout missing lab_bounds_mm (flat or under layout{})"
    )


def resolve_edge_bench(rt: Any) -> ResolvedEdgeBench:
    """Load layout geometry for ``rt`` (BackendRuntime).

    Order:
    1. HTTP ``edge.base_url`` ``GET /bench``.
    2. Sibling ``cloudlabs_edge/bench/layout.json`` next to teaching ``lab_view_path``.
    3. Teaching ``lab_view/layout.json`` (legacy flat file).
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
            bench = client.get_bench()
            if isinstance(bench, dict):
                return ResolvedEdgeBench(
                    backend_id=bid,
                    source="edge_http",
                    layout=unwrap_bench_layout(bench),
                    bench=bench,
                )
            raise EdgeBenchUnavailable(
                f"edge HTTP GET /bench unavailable for {bid!r} (base_url={base!r})"
            )

    paths = getattr(rt, "paths", None)
    lvp = str(getattr(spec, "lab_view_path", "") or "").strip()
    layout_json = getattr(paths, "layout_json", "") if paths else ""

    # Teaching lab_view (not coordinator_data root) — edge sits beside lab_view/.
    lab_view_root: Optional[Path] = None
    if layout_json and os.path.isfile(layout_json):
        lab_view_root = Path(layout_json).resolve().parent
    elif lvp:
        lab_view_root = (
            Path(lvp).resolve()
            if os.path.isabs(lvp)
            else Path(os.path.abspath(lvp))
        )

    if lab_view_root is not None and lab_view_root.is_dir():
        edge_root = resolve_edge_root_beside_lab_view(lab_view_root)
        if edge_root is not None:
            bench_path = edge_root / "bench" / "layout.json"
            if bench_path.is_file():
                bench = load_json(bench_path)
                if isinstance(bench, dict):
                    return ResolvedEdgeBench(
                        backend_id=bid,
                        source="edge_bench_disk",
                        layout=unwrap_bench_layout(bench),
                        bench=bench,
                    )

        if layout_json and os.path.isfile(layout_json):
            doc = load_json(Path(layout_json))
            if isinstance(doc, dict):
                return ResolvedEdgeBench(
                    backend_id=bid,
                    source="teaching_lab_view",
                    layout=unwrap_bench_layout(doc),
                    bench=None,
                )

    raise EdgeBenchUnavailable(
        f"no edge bench layout for {bid!r} "
        f"(configure edge.base_url or teaching cloudlabs_edge/bench/layout.json)"
    )


__all__ = [
    "EdgeBenchUnavailable",
    "ResolvedEdgeBench",
    "resolve_edge_bench",
    "unwrap_bench_layout",
]
