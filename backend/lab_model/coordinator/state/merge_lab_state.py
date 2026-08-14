"""Merge coordinator working lab-state with edge overlays for Twin reads.

See ``docs/BACKEND_ISOLATION.md`` §2.2.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, Mapping, Optional, Set

#: Top-level keys the edge owns when present.
EDGE_TOP_LEVEL_KEYS: Set[str] = {
    "runtime_sync",
}

#: Top-level keys the coordinator always wins (after edge overlays).
COORDINATOR_TOP_LEVEL_KEYS: Set[str] = {
    "system_status",
    "holding",
    "last_updated",
    "optimization_step",
    "optimization_run_dir",
    "optimization_target_id",
    "alignment_guides",
}


def _merge_component_telemetry(
    working_tel: Optional[Mapping[str, Any]],
    edge_tel: Mapping[str, Any],
) -> Dict[str, Any]:
    """Overlay edge telemetry without clobbering Twin session flags.

    Coordinator owns ``telemetry.teleop`` and ``telemetry.live_feed`` session
    fields after remote ``START_*`` / ``END_*`` commits. Real edges often
    hardcode ``teleop.active=false`` / ``live_feed.stream.live=false`` in
    ``/lab-state``; replacing those blobs would hide an active Twin session.
    """
    if not isinstance(working_tel, Mapping):
        return copy.deepcopy(dict(edge_tel))
    out: Dict[str, Any] = copy.deepcopy(dict(working_tel))
    for key, value in edge_tel.items():
        if key in ("teleop", "live_feed"):
            # Seed structure from edge only when coordinator has none yet.
            if key not in out or not out[key]:
                out[key] = copy.deepcopy(value)
            continue
        out[key] = copy.deepcopy(value)
    return out


def merge_lab_state_for_twin(
    working: Mapping[str, Any],
    edge: Optional[Mapping[str, Any]],
    *,
    backend_id: str = "",
) -> Dict[str, Any]:
    """Deep-copy ``working``, then overlay edge-owned slices.

    - **Coordinator wins:** ``system_status``, ``holding``, presence / placement /
      commanded tunables (``statecontrol``), guides, optimization fields,
      ``telemetry.teleop`` and ``telemetry.live_feed`` session flags.
    - **Edge wins:** ``runtime_sync``, and tags that exist only on the edge
      (read-time inventory).
    """
    out: Dict[str, Any] = copy.deepcopy(dict(working))
    if not isinstance(edge, Mapping):
        return out

    for key in EDGE_TOP_LEVEL_KEYS:
        if key in edge:
            out[key] = copy.deepcopy(edge[key])

    w_comps = out.get("components")
    if not isinstance(w_comps, dict):
        w_comps = {}
        out["components"] = w_comps
    e_comps = edge.get("components")
    if isinstance(e_comps, dict):
        for tag, e_comp in e_comps.items():
            tag_id = str(tag)
            if not isinstance(e_comp, dict):
                continue
            w_comp = w_comps.get(tag_id)
            if not isinstance(w_comp, dict):
                # Edge-only tag: expose for Twin inventory until coordinator seeds.
                w_comps[tag_id] = copy.deepcopy(e_comp)
                continue
            e_tel = e_comp.get("telemetry")
            if isinstance(e_tel, dict):
                w_tel = w_comp.get("telemetry")
                w_comp["telemetry"] = _merge_component_telemetry(
                    w_tel if isinstance(w_tel, dict) else None,
                    e_tel,
                )

    for key in COORDINATOR_TOP_LEVEL_KEYS:
        if key in working:
            out[key] = copy.deepcopy(working[key])

    # backend_id reserved for future debug tracing (avoid per-poll spam).
    _ = backend_id
    return out


__all__ = [
    "COORDINATOR_TOP_LEVEL_KEYS",
    "EDGE_TOP_LEVEL_KEYS",
    "merge_lab_state_for_twin",
]
