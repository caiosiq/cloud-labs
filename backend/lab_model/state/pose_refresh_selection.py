"""Resolve scoped pose-refresh plans (apply vs preserve vs scan scope)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Set

from lab_model.domain.component import PRESENCE_BREADBOARD, PRESENCE_STORAGE, get_tunables


def normalize_tag_id_list(raw: Optional[Iterable[str]]) -> List[str]:
    out: List[str] = []
    seen: Set[str] = set()
    for item in raw or ():
        if not isinstance(item, str):
            continue
        tid = item.strip()
        if not tid or tid in seen:
            continue
        seen.add(tid)
        out.append(tid)
    return out


def eligible_pose_refresh_tag_ids(components: Mapping[str, Any]) -> Set[str]:
    eligible: Set[str] = set()
    if not isinstance(components, dict):
        return eligible
    for tag_id, comp in components.items():
        if not isinstance(tag_id, str) or not isinstance(comp, dict):
            continue
        presence = get_tunables(comp).get("presence")
        if presence in (PRESENCE_BREADBOARD, PRESENCE_STORAGE):
            eligible.add(tag_id.strip())
    return eligible


@dataclass(frozen=True)
class PoseRefreshPlan:
    """Tags to scan/apply vs leave untouched."""

    scan_tag_ids: List[str]
    preserve_tag_ids: List[str]


def resolve_pose_refresh_plan(
    components: Mapping[str, Any],
    *,
    tag_ids: Optional[Iterable[str]] = None,
    apply_tag_ids: Optional[Iterable[str]] = None,
    preserve_tag_ids: Optional[Iterable[str]] = None,
) -> PoseRefreshPlan:
    """Build scan + preserve lists for a scoped pose refresh.

    - ``tag_ids`` limits which on-table components participate.
    - ``apply_tag_ids`` (preferred) lists tags to update from the scan.
    - ``preserve_tag_ids`` freezes tags inside the scope; ignored when
      ``apply_tag_ids`` is non-empty.
    - Components outside the scope are always preserved.
    """
    eligible = eligible_pose_refresh_tag_ids(components)
    all_known = set(components.keys()) if isinstance(components, dict) else set()

    scope = set(normalize_tag_id_list(tag_ids)) & eligible if tag_ids else set(eligible)
    if not scope and tag_ids:
        scope = set(normalize_tag_id_list(tag_ids)) & all_known

    apply_norm = normalize_tag_id_list(apply_tag_ids)
    if apply_norm:
        apply_set = set(apply_norm) & scope
    else:
        preserve_in_scope = set(normalize_tag_id_list(preserve_tag_ids)) & scope
        apply_set = scope - preserve_in_scope

    preserve_set = (all_known - apply_set) if isinstance(components, dict) else set()
    preserve_set |= scope - apply_set

    return PoseRefreshPlan(
        scan_tag_ids=sorted(apply_set),
        preserve_tag_ids=sorted(preserve_set),
    )


def filter_proposed_poses(
    proposed: Mapping[str, Mapping[str, Any]],
    scan_tag_ids: Iterable[str],
) -> Dict[str, Dict[str, Any]]:
    allowed = set(normalize_tag_id_list(scan_tag_ids))
    if not allowed:
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for tag_id, pose in proposed.items():
        if tag_id in allowed and isinstance(pose, dict):
            out[tag_id] = dict(pose)
    return out
