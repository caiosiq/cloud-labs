"""Merge table-scan poses with previous component entries (selective preserve)."""
from __future__ import annotations

import json
from typing import Any, Dict, Iterable, Optional, Set


def merge_scan_into_components(
    previous: Dict[str, Any],
    candidate: Dict[str, Any],
    preserve_tag_ids: Optional[Iterable[str]],
) -> Dict[str, Any]:
    """Combine scan output with untouched preserved tags.

    - Tags in ``preserve_tag_ids`` whose previous entry exists: keep deep copy of
      ``previous[tag_id]`` (measurables, tunables, id, type, etc.).
    - Other tags present in ``candidate``: use deep copy from ``candidate``.

    Preserve tags absent from ``candidate`` (e.g. not in catalog) but present in
    ``previous`` are carried forward unchanged.
    """
    prev_in = previous if isinstance(previous, dict) else {}
    cand_in = candidate if isinstance(candidate, dict) else {}

    preserve: Set[str] = set()
    for raw in preserve_tag_ids or []:
        if isinstance(raw, str) and raw.strip():
            preserve.add(raw.strip())

    merged: Dict[str, Any] = {}

    for tid, entry in cand_in.items():
        if tid in preserve and tid in prev_in:
            merged[tid] = json.loads(json.dumps(prev_in[tid]))
        elif tid not in preserve:
            merged[tid] = json.loads(json.dumps(entry))
        else:
            merged[tid] = json.loads(json.dumps(entry))

    for tid in preserve:
        if tid in prev_in and tid not in merged:
            merged[tid] = json.loads(json.dumps(prev_in[tid]))

    return merged
