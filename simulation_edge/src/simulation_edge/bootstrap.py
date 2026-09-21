"""Bootstrap simulation host against ``simulation_edge/lab_view``."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, List, Mapping, Optional, Tuple

_PKG_ROOT = Path(__file__).resolve().parents[2]  # simulation_edge/
_DEFAULT_LAB_VIEW = _PKG_ROOT / "lab_view"


def default_lab_view_path() -> Path:
    env = (os.environ.get("SIMULATION_EDGE_LAB_VIEW") or "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return _DEFAULT_LAB_VIEW.resolve()


def _load_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _resolve_catalog_rows(root: Path) -> List[Mapping[str, Any]]:
    """Build catalog rows from ``active_catalog.json`` + ``component_library.json``."""
    active = _load_json(root / "active_catalog.json", {})
    library = _load_json(root / "component_library.json", {})
    edge_root = root.resolve().parent / "cloudlabs_edge"
    if (edge_root / "data" / "library.json").is_file():
        try:
            from simulation_edge.component_registry import merged_library

            library = merged_library(edge_root)
        except Exception:
            # Startup validation below still produces explicit UNKNOWN rows for
            # active tags when the authored simulation registry is invalid.
            pass

    tag_ids: list[str] = []
    if isinstance(active, dict):
        raw_ids = active.get("tag_ids")
        if isinstance(raw_ids, list):
            tag_ids = [str(t) for t in raw_ids if t]
        elif isinstance(active.get("components"), list):
            return [dict(row) for row in active["components"] if isinstance(row, dict)]
    elif isinstance(active, list):
        return [dict(row) for row in active if isinstance(row, dict)]

    components = {}
    if isinstance(library, dict):
        raw = library.get("components")
        if isinstance(raw, dict):
            components = raw
        elif isinstance(raw, list):
            components = {
                str(row.get("tag_id")): row
                for row in raw
                if isinstance(row, dict) and row.get("tag_id")
            }

    rows: list[Mapping[str, Any]] = []
    for tag in tag_ids or list(components.keys()):
        row = components.get(tag)
        if isinstance(row, dict):
            out = dict(row)
            out.setdefault("tag_id", tag)
            rows.append(out)
        else:
            rows.append({"tag_id": tag, "type": "UNKNOWN", "capabilities": {}})
    return rows


def bootstrap_host(*, lab_view: Optional[Path] = None) -> Tuple[Any, Any]:
    """Return ``(host, host)`` — soft MuJoCo host bound to lab_view assets."""
    from simulation_edge.host.simulation_host import SimulationHost

    root = (lab_view or default_lab_view_path()).resolve()
    os.environ.setdefault("LAB_VIEW_PATH", str(root))

    layout = _load_json(root / "layout.json", {})
    if not isinstance(layout, dict):
        layout = {}
    state = _load_json(root / "lab_state.json", {"components": {}})
    if not isinstance(state, dict):
        state = {"components": {}}
    catalog = _resolve_catalog_rows(root)

    host = SimulationHost(state, catalog, layout)
    return host, host
