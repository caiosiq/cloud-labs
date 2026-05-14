"""Join ``component_library.json`` + ``active_catalog.json`` into UI / scan rows."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Tuple

from lab_communicator.shared.lab_view_config import LabViewPaths, get_lab_view_paths_optional


def _load_json_list(path: str, label: str) -> List[Dict[str, Any]]:
    if not os.path.isfile(path):
        raise FileNotFoundError(f"{label} not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"{label} must be a JSON array: {path}")
    return [x for x in data if isinstance(x, dict)]


def library_by_tag(library_path: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    lp = get_lab_view_paths_optional()
    catalog_path = library_path or (lp.component_library_json if lp else "")
    if not catalog_path:
        raise RuntimeError("lab_view not bootstrapped")
    rows = _load_json_list(catalog_path, "component_library.json")
    by_tag: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        tid = row.get("tag_id")
        if isinstance(tid, str) and tid.strip():
            by_tag[tid.strip()] = row
    return by_tag


def active_tag_ids(active_path: Optional[str] = None) -> List[str]:
    lp = get_lab_view_paths_optional()
    if lp is None and not active_path:
        raise RuntimeError("lab_view not bootstrapped")
    p = active_path or (lp.active_catalog_json if lp else "")
    if not os.path.isfile(p):
        raise FileNotFoundError(f"active_catalog.json not found: {p}")
    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"active_catalog.json must be a JSON object: {p}")
    raw = data.get("tag_ids")
    if raw is None:
        raise ValueError(f"active_catalog.json missing \"tag_ids\" array: {p}")
    if not isinstance(raw, list):
        raise ValueError(f'active_catalog.json "tag_ids" must be an array: {p}')
    out: List[str] = []
    for x in raw:
        if isinstance(x, str) and x.strip():
            out.append(x.strip())
        else:
            raise ValueError(f"active_catalog.json tag_ids entries must be non-empty strings: {p}")
    return out


def merged_catalog_rows(paths: Optional[LabViewPaths] = None) -> List[Dict[str, Any]]:
    lp = paths or get_lab_view_paths_optional()
    if lp is None:
        raise RuntimeError("lab_view not bootstrapped")
    by_tag = library_by_tag(lp.component_library_json)
    tag_ids = active_tag_ids(lp.active_catalog_json)
    rows: List[Dict[str, Any]] = []
    for tid in tag_ids:
        row = by_tag.get(tid)
        if row is None:
            raise ValueError(
                f"active_catalog.json lists \"{tid}\" but that tag_id is missing from component_library.json"
            )
        rows.append(dict(row))
    return rows


def merged_catalog_maps(
    paths: Optional[LabViewPaths] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    rows = merged_catalog_rows(paths)
    catalog_map = {r["tag_id"]: r for r in rows if isinstance(r.get("tag_id"), str)}
    return rows, catalog_map
