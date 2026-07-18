"""Deterministic mock table-cam scan preview (preview matches apply)."""

from __future__ import annotations

import hashlib
from typing import Any, Dict, Iterable, Mapping, Optional

from lab_model.language.domain.component import (
    PRESENCE_BREADBOARD,
    PRESENCE_STORAGE,
    get_tunables,
    reported_pose,
    set_reported_pose,
)


def _jitter_from_tag(tag_id: str, x: float, y: float, rotation: float) -> tuple[float, float, float]:
    payload = f"{tag_id}|{x:.4f}|{y:.4f}|{rotation:.4f}".encode("utf-8")
    digest = hashlib.sha256(payload).digest()

    def unit(index: int) -> float:
        return (digest[index] / 255.0) * 2.0 - 1.0

    return x + unit(0) * 0.8, y + unit(1) * 0.8, rotation + unit(2) * 0.35


def build_mock_scan_proposed_poses(
    components: Mapping[str, Any],
    *,
    tag_ids: Optional[Iterable[str]] = None,
) -> Dict[str, Dict[str, float]]:
    """Simulate camera re-localisation poses for on-table components."""
    proposed: Dict[str, Dict[str, float]] = {}
    if not isinstance(components, dict):
        return proposed

    allowed = None
    if tag_ids is not None:
        allowed = {str(t).strip() for t in tag_ids if isinstance(t, str) and str(t).strip()}

    for tag_id, comp in components.items():
        if not isinstance(tag_id, str) or not isinstance(comp, dict):
            continue
        if allowed is not None and tag_id not in allowed:
            continue
        tun = get_tunables(comp)
        if tun.get("presence") not in (PRESENCE_BREADBOARD, PRESENCE_STORAGE):
            continue
        pose = reported_pose(dict(comp))
        np = tun.get("nominal_pose") if isinstance(tun.get("nominal_pose"), dict) else {}
        base_x = float(np.get("x", pose.get("x", 0.0)))
        base_y = float(np.get("y", pose.get("y", 0.0)))
        base_r = float(np.get("rotation", pose.get("rotation", 0.0)))
        sx, sy, sr = _jitter_from_tag(tag_id, base_x, base_y, base_r)
        proposed[tag_id] = {"x": sx, "y": sy, "rotation": sr}

    return proposed


def apply_mock_scan_to_component(comp: Dict[str, Any], proposed_pose: Mapping[str, float]) -> Dict[str, Any]:
    """Return a component copy with ``tunables.reported_pose`` set to ``proposed_pose``."""
    import json

    refreshed = json.loads(json.dumps(comp))
    set_reported_pose(
        refreshed,
        {
            "x": float(proposed_pose.get("x", 0.0)),
            "y": float(proposed_pose.get("y", 0.0)),
            "rotation": float(proposed_pose.get("rotation", 0.0)),
        },
    )
    return refreshed
