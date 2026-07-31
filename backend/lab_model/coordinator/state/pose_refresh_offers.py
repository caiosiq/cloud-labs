"""Build pose-refresh offers by comparing current reported pose to scan preview."""

from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping

from mock_backend.shared.session_checkpoint import (
    ReconciliationThresholds,
    _pose_xy_yaw,
    poses_close,
    yaw_diff_deg,
)
from lab_model.language.domain.component import PRESENCE_BREADBOARD, PRESENCE_STORAGE, get_tunables, reported_pose


def _eligible_for_pose_refresh(comp: Mapping[str, Any]) -> bool:
    if not isinstance(comp, dict):
        return False
    presence = get_tunables(dict(comp)).get("presence")
    return presence in (PRESENCE_BREADBOARD, PRESENCE_STORAGE)


def build_pose_refresh_offers(
    components: Mapping[str, Any],
    proposed_poses: Mapping[str, Mapping[str, Any]],
    thresholds: ReconciliationThresholds,
) -> List[Dict[str, Any]]:
    """Return per-tag scan deltas vs current ``tunables.reported_pose``.

    ``default_apply`` is ``True`` when the delta exceeds reconciliation thresholds
    (operator should refresh); ``False`` when within tolerance (skip by default).
    """
    offers: List[Dict[str, Any]] = []
    if not isinstance(components, dict):
        return offers

    for tag_id in sorted(proposed_poses.keys()):
        comp = components.get(tag_id)
        if not isinstance(comp, dict) or not _eligible_for_pose_refresh(comp):
            continue
        proposed = proposed_poses.get(tag_id)
        if not isinstance(proposed, dict):
            continue

        current_pose = reported_pose(dict(comp))
        if not isinstance(current_pose, dict):
            current_pose = {}

        cx, cy, cr = _pose_xy_yaw(current_pose)
        px, py, pr = _pose_xy_yaw(proposed)
        delta_mm = math.hypot(cx - px, cy - py)
        delta_yaw = yaw_diff_deg(cr, pr)
        within = poses_close(current_pose, proposed, thresholds)

        offers.append(
            {
                "tag_id": tag_id,
                "current_pose": {
                    "x": round(cx, 3),
                    "y": round(cy, 3),
                    "rotation": round(cr, 3),
                },
                "proposed_pose": {
                    "x": round(px, 3),
                    "y": round(py, 3),
                    "rotation": round(pr, 3),
                },
                "delta_mm": round(delta_mm, 3),
                "delta_yaw_deg": round(delta_yaw, 3),
                "within_tolerance": within,
                "default_apply": not within,
            }
        )

    return offers
