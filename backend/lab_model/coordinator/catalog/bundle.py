"""Join ``component_library.json`` + ``active_catalog.json`` into UI / scan rows.

As of Phase 5 (universal_component_architecture.md Â§13), ``component_library.json``
may be either:

- the **legacy** top-level array of component dicts (pre-migration), or
- the **v1** ``{schema_version: 1, components: {tag_id: {...}}}`` object
  with a per-component ``capabilities`` block.

This module hides the shape difference: callers always receive a list of
row dicts. The dual-shape parsing + Â§15.1 validation lives in
:mod:`lab_model.coordinator.catalog.schema`.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Iterable, List, Optional, Tuple

from lab_model.coordinator.catalog.schema import load_component_library_rows
from lab_model.coordinator.backends.lab_view_config import LabViewPaths, get_lab_view_paths_optional


def library_by_tag(library_path: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    lp = get_lab_view_paths_optional()
    catalog_path = library_path or (lp.component_library_json if lp else "")
    if not catalog_path:
        raise RuntimeError("lab_view not bootstrapped")
    rows = load_component_library_rows(catalog_path)
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

    # Prefer edge inventory keys when library is data/library.json (or sibling inventory).
    if lp is not None:
        lib = lp.component_library_json
        inv_candidate = os.path.join(os.path.dirname(lib), "inventory.json")
        if os.path.basename(lib) == "library.json" and os.path.isfile(inv_candidate):
            with open(inv_candidate, "r", encoding="utf-8") as f:
                data = json.load(f)
            entries = data.get("entries") if isinstance(data, dict) else None
            if not isinstance(entries, dict):
                raise ValueError(f"inventory.json missing entries object: {inv_candidate}")
            out: List[str] = []
            for key in entries.keys():
                if isinstance(key, str) and key.strip():
                    out.append(key.strip())
                else:
                    raise ValueError(
                        f"inventory.json entry keys must be non-empty strings: {inv_candidate}"
                    )
            return out

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
    out = []
    for x in raw:
        if isinstance(x, str) and x.strip():
            out.append(x.strip())
        else:
            raise ValueError(f"active_catalog.json tag_ids entries must be non-empty strings: {p}")
    return out


def filter_v1_catalog_to_active_tags(
    doc: Dict[str, Any],
    active_ids: Iterable[str],
) -> Dict[str, Any]:
    """Return a shallow copy of a v1 catalog doc with only ``active_ids`` in ``components``."""
    allowed = {str(t).strip() for t in active_ids if isinstance(t, str) and str(t).strip()}
    components = doc.get("components")
    if not isinstance(components, dict):
        return dict(doc)
    filtered = {
        k: v for k, v in components.items() if isinstance(k, str) and k in allowed
    }
    out = dict(doc)
    out["components"] = filtered
    return out


def active_catalog_v1_document(paths: Optional[LabViewPaths] = None) -> Optional[Dict[str, Any]]:
    """v1 ``{schema_version, components}`` for ``lab_automation``, restricted to ``active_catalog.json``."""
    from lab_model.coordinator.catalog.schema import is_v1_object_shape  # noqa: PLC0415

    lp = paths or get_lab_view_paths_optional()
    if lp is None:
        return None
    with open(lp.component_library_json, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not is_v1_object_shape(data):
        return None
    active = active_tag_ids(lp.active_catalog_json)
    return filter_v1_catalog_to_active_tags(data, active)


def _merged_tag_id_list(
    by_tag: Dict[str, Dict[str, Any]],
    active_path: str,
    *,
    runtime_tag_ids: Optional[Iterable[str]] = None,
) -> List[str]:
    """Union of active-catalog tags plus runtime tags that exist in the library."""
    tag_ids = list(active_tag_ids(active_path))
    seen = set(tag_ids)
    if runtime_tag_ids:
        for raw in runtime_tag_ids:
            tid = str(raw).strip() if raw is not None else ""
            if not tid or tid in seen:
                continue
            if tid not in by_tag:
                continue
            tag_ids.append(tid)
            seen.add(tid)
    return tag_ids


def merged_catalog_rows(
    paths: Optional[LabViewPaths] = None,
    *,
    runtime_tag_ids: Optional[Iterable[str]] = None,
) -> List[Dict[str, Any]]:
    lp = paths or get_lab_view_paths_optional()
    if lp is None:
        raise RuntimeError("lab_view not bootstrapped")
    by_tag = library_by_tag(lp.component_library_json)
    tag_ids = _merged_tag_id_list(
        by_tag,
        lp.active_catalog_json,
        runtime_tag_ids=runtime_tag_ids,
    )
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
    *,
    runtime_tag_ids: Optional[Iterable[str]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    rows = merged_catalog_rows(paths, runtime_tag_ids=runtime_tag_ids)
    catalog_map = {r["tag_id"]: r for r in rows if isinstance(r.get("tag_id"), str)}
    return rows, catalog_map
