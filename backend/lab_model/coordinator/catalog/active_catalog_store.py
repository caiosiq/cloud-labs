"""Read/write ``active_catalog.json`` (global tag allow-list for the bench).

When sibling edge ``data/inventory.json`` is present, writes also update that
file so inventory remains the edge-owned source of truth.
"""

from __future__ import annotations

import json
import os
from typing import Optional

from lab_model.coordinator.backends.lab_view_config import LabViewPaths, get_lab_view_paths_optional

from .bundle import active_tag_ids


def _inventory_path(lp: LabViewPaths) -> Optional[str]:
    inv = os.path.join(os.path.dirname(lp.component_library_json), "inventory.json")
    if os.path.basename(lp.component_library_json) == "library.json" and os.path.isfile(inv):
        return inv
    edge_inv = os.path.join(
        os.path.dirname(lp.root_dir), "cloudlabs_edge", "data", "inventory.json"
    )
    if os.path.isfile(edge_inv):
        return edge_inv
    return None


def _sync_inventory_tag(lp: LabViewPaths, tag_id: str, *, present: bool) -> None:
    inv_path = _inventory_path(lp)
    if not inv_path:
        return
    with open(inv_path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        data = {"schema_version": 1, "entries": {}}
    entries = data.get("entries")
    if not isinstance(entries, dict):
        entries = {}
        data["entries"] = entries
    data.setdefault("schema_version", 1)
    if present:
        if tag_id not in entries:
            entries[tag_id] = {
                "placement": "table",
                "storage_slot": None,
                "localize": True,
            }
    else:
        entries.pop(tag_id, None)
    with open(inv_path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
        handle.write("\n")


def ensure_tag_in_active_catalog(
    tag_id: str,
    *,
    paths: Optional[LabViewPaths] = None,
) -> bool:
    """Append ``tag_id`` to ``active_catalog.json`` when missing.

    Returns ``True`` when the file was updated on disk.
    """
    tid = (tag_id or "").strip()
    if not tid:
        raise ValueError("tag_id must be a non-empty string")

    lp = paths or get_lab_view_paths_optional()
    if lp is None:
        raise RuntimeError("lab_view not bootstrapped")

    active_path = lp.active_catalog_json
    if not os.path.isfile(active_path):
        raise FileNotFoundError(f"active_catalog.json not found: {active_path}")

    with open(active_path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"active_catalog.json must be a JSON object: {active_path}")

    raw = data.get("tag_ids")
    if raw is None:
        raise ValueError(f'active_catalog.json missing "tag_ids" array: {active_path}')
    if not isinstance(raw, list):
        raise ValueError(f'active_catalog.json "tag_ids" must be an array: {active_path}')

    tag_ids = [str(x).strip() for x in raw if isinstance(x, str) and str(x).strip()]
    updated = False
    if tid not in tag_ids:
        tag_ids.append(tid)
        data["tag_ids"] = tag_ids
        with open(active_path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
            handle.write("\n")
        updated = True
    _sync_inventory_tag(lp, tid, present=True)
    return updated


def remove_tag_from_active_catalog(
    tag_id: str,
    *,
    paths: Optional[LabViewPaths] = None,
) -> bool:
    """Remove ``tag_id`` from ``active_catalog.json`` when present.

    Returns ``True`` when the file was updated on disk.
    """
    tid = (tag_id or "").strip()
    if not tid:
        raise ValueError("tag_id must be a non-empty string")

    lp = paths or get_lab_view_paths_optional()
    if lp is None:
        raise RuntimeError("lab_view not bootstrapped")

    active_path = lp.active_catalog_json
    if not os.path.isfile(active_path):
        raise FileNotFoundError(f"active_catalog.json not found: {active_path}")

    with open(active_path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"active_catalog.json must be a JSON object: {active_path}")

    raw = data.get("tag_ids")
    if not isinstance(raw, list):
        raise ValueError(f'active_catalog.json "tag_ids" must be an array: {active_path}')

    tag_ids = [str(x).strip() for x in raw if isinstance(x, str) and str(x).strip()]
    updated = False
    if tid in tag_ids:
        data["tag_ids"] = [x for x in tag_ids if x != tid]
        with open(active_path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
            handle.write("\n")
        updated = True
    _sync_inventory_tag(lp, tid, present=False)
    return updated


def list_active_catalog_tags(paths: Optional[LabViewPaths] = None) -> list[str]:
    """Return tracked tag ids (inventory keys when present, else active_catalog)."""
    lp = paths or get_lab_view_paths_optional()
    if lp is None:
        raise RuntimeError("lab_view not bootstrapped")
    # Omit path so active_tag_ids can prefer edge inventory keys.
    if paths is None:
        return list(active_tag_ids())
    return list(active_tag_ids(lp.active_catalog_json))
