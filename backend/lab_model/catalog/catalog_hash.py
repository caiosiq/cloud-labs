"""Stable hash of the global active catalog tag list."""

from __future__ import annotations

import hashlib
import json
from typing import Optional

from lab_communicator.shared.lab_view_config import LabViewPaths, get_lab_view_paths_optional

from .bundle import active_tag_ids


def compute_active_catalog_hash(paths: Optional[LabViewPaths] = None) -> str:
    """SHA-256 hex digest of sorted ``active_catalog.json`` tag ids."""
    lp = paths or get_lab_view_paths_optional()
    if lp is None:
        raise RuntimeError("lab_view not bootstrapped")
    tag_ids = sorted(active_tag_ids(lp.active_catalog_json))
    payload = json.dumps(tag_ids, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
