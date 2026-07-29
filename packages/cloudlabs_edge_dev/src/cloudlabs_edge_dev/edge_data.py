"""Load edge-owned ``data/library.json`` and ``data/inventory.json``.

Shared by scaffold stubs, mock/sim/deathray HTTP apps, and (optionally) the
coordinator when reading an in-tree edge data folder.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set


def edge_data_dir(edge_root: Path) -> Path:
    return edge_root.resolve() / "data"


def load_json(path: Path) -> Dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top-level must be an object")
    return data


def load_library(edge_root: Path) -> Dict[str, Any]:
    path = edge_data_dir(edge_root) / "library.json"
    if not path.is_file():
        raise FileNotFoundError(f"edge library not found: {path}")
    doc = load_json(path)
    if int(doc.get("schema_version") or 0) != 1:
        raise ValueError(f"{path}: schema_version must be 1")
    comps = doc.get("components")
    if not isinstance(comps, dict) or not comps:
        raise ValueError(f"{path}: components must be a non-empty object")
    return doc


def load_inventory(edge_root: Path) -> Dict[str, Any]:
    path = edge_data_dir(edge_root) / "inventory.json"
    if not path.is_file():
        raise FileNotFoundError(f"edge inventory not found: {path}")
    doc = load_json(path)
    if int(doc.get("schema_version") or 0) != 1:
        raise ValueError(f"{path}: schema_version must be 1")
    entries = doc.get("entries")
    if not isinstance(entries, dict):
        raise ValueError(f"{path}: entries must be an object")
    return doc


def library_tag_ids(library: Dict[str, Any]) -> List[str]:
    comps = library.get("components") or {}
    return sorted(str(k) for k in comps.keys() if isinstance(k, str) and k.strip())


def inventory_tag_ids(inventory: Dict[str, Any]) -> List[str]:
    entries = inventory.get("entries") or {}
    return [str(k) for k in entries.keys() if isinstance(k, str) and k.strip()]


def validate_inventory_against_library(
    library: Dict[str, Any],
    inventory: Dict[str, Any],
) -> None:
    lib_keys = set(library_tag_ids(library))
    missing = [t for t in inventory_tag_ids(inventory) if t not in lib_keys]
    if missing:
        raise ValueError(
            "inventory tags missing from library: " + ", ".join(sorted(missing))
        )


def default_localize_tag_ids(inventory: Dict[str, Any]) -> List[str]:
    """Tags LOCALIZE_COMPONENTS should scan when args.tag_ids is omitted."""
    out: List[str] = []
    entries = inventory.get("entries") or {}
    for tag_id, entry in entries.items():
        if not isinstance(tag_id, str) or not tag_id.strip():
            continue
        if not isinstance(entry, dict):
            continue
        if entry.get("localize") is False:
            continue
        placement = str(entry.get("placement") or "")
        if placement in ("table", "storage"):
            out.append(tag_id.strip())
    return out


def library_rows(library: Dict[str, Any]) -> List[Dict[str, Any]]:
    comps = library.get("components") or {}
    rows: List[Dict[str, Any]] = []
    for key, entry in comps.items():
        if not isinstance(entry, dict):
            continue
        row = dict(entry)
        row.setdefault("tag_id", key)
        rows.append(row)
    return rows


def merged_active_rows(
    library: Dict[str, Any],
    inventory: Dict[str, Any],
) -> List[Dict[str, Any]]:
    active: Set[str] = set(inventory_tag_ids(inventory))
    return [r for r in library_rows(library) if str(r.get("tag_id") or "") in active]


def stamp_backend(doc: Dict[str, Any], backend_id: str) -> Dict[str, Any]:
    out = dict(doc)
    out["backend_id"] = backend_id
    return out


def resolve_edge_root_beside_lab_view(lab_view_root: Path) -> Optional[Path]:
    """``mock_edge/lab_view`` → ``mock_edge/cloudlabs_edge`` when that tree exists."""
    candidate = lab_view_root.resolve().parent / "cloudlabs_edge"
    if (candidate / "data" / "library.json").is_file():
        return candidate
    return None
