"""Diff configuration and observation documents."""

from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional

from lab_model.primitives.ids import PrimitiveId
from lab_model.state.projections import NON_RECONCILE_TUNABLE_KEYS


def _path(*parts: str) -> str:
    return ".".join(parts)


def configuration_diff(
    from_cfg: Mapping[str, Any],
    to_cfg: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    """Return a list of field-level changes between two configurations."""
    changes: List[Dict[str, Any]] = []

    def add(tag_id: str, path: str, old: Any, new: Any) -> None:
        if old == new:
            return
        changes.append(
            {
                "tag_id": tag_id,
                "path": path,
                "from": old,
                "to": new,
            }
        )

    fh_from = from_cfg.get("holding") or {}
    fh_to = to_cfg.get("holding") or {}
    if isinstance(fh_from, dict) and isinstance(fh_to, dict):
        add("<holding>", "holding.tag_id", fh_from.get("tag_id"), fh_to.get("tag_id"))
        add(
            "<holding>",
            "holding.nominal_pose",
            fh_from.get("nominal_pose"),
            fh_to.get("nominal_pose"),
        )

    comps_from = from_cfg.get("components") or {}
    comps_to = to_cfg.get("components") or {}
    if not isinstance(comps_from, dict):
        comps_from = {}
    if not isinstance(comps_to, dict):
        comps_to = {}

    all_tags = sorted(set(comps_from.keys()) | set(comps_to.keys()))
    for tag_id in all_tags:
        if tag_id not in comps_from:
            add(tag_id, _path("component"), None, "added")
            continue
        if tag_id not in comps_to:
            add(tag_id, _path("component"), "present", None)
            continue
        entry_from = comps_from.get(tag_id) or {}
        entry_to = comps_to.get(tag_id) or {}
        tun_from = ((entry_from.get("statecontrol") or {}).get("tunables") or {})
        tun_to = ((entry_to.get("statecontrol") or {}).get("tunables") or {})
        if not isinstance(tun_from, dict):
            tun_from = {}
        if not isinstance(tun_to, dict):
            tun_to = {}

        for key in sorted(set(tun_from.keys()) | set(tun_to.keys())):
            if key in NON_RECONCILE_TUNABLE_KEYS:
                continue
            add(tag_id, _path("tunables", key), tun_from.get(key), tun_to.get(key))

    # Versioned alignment overlays. Only diff a key when BOTH configs declare
    # it: legacy commits created before line-versioning omit the key and are
    # treated as wildcards so they never show up as spurious "dirty" before the
    # one-time backfill migration runs. Once backfilled, comparison is exact.
    if "alignment_guides" in from_cfg and "alignment_guides" in to_cfg:
        _diff_guides(add, from_cfg.get("alignment_guides"), to_cfg.get("alignment_guides"))
    if "laser_lines" in from_cfg and "laser_lines" in to_cfg:
        _diff_laser_lines(add, from_cfg.get("laser_lines"), to_cfg.get("laser_lines"))

    return changes


def _norm_point(p: Any) -> Optional[Dict[str, float]]:
    if not isinstance(p, dict):
        return None
    try:
        return {"x": round(float(p.get("x", 0.0)), 4), "y": round(float(p.get("y", 0.0)), 4)}
    except (TypeError, ValueError):
        return None


def _guide_map(guides: Any) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    if isinstance(guides, list):
        for g in guides:
            if isinstance(g, dict) and g.get("id"):
                out[str(g["id"])] = {
                    "p1": _norm_point(g.get("p1")),
                    "p2": _norm_point(g.get("p2")),
                }
    return out


def _diff_guides(add, from_guides: Any, to_guides: Any) -> None:
    a = _guide_map(from_guides)
    b = _guide_map(to_guides)
    for gid in sorted(set(a) | set(b)):
        if gid not in a:
            add("<guides>", _path("guide", gid), None, "added")
        elif gid not in b:
            add("<guides>", _path("guide", gid), "present", None)
        elif a[gid] != b[gid]:
            add("<guides>", _path("guide", gid), a[gid], b[gid])


def _laser_line_map(doc: Any) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    lines = doc.get("lines") if isinstance(doc, dict) else None
    if isinstance(lines, list):
        for ln in lines:
            if isinstance(ln, dict) and ln.get("id"):
                out[str(ln["id"])] = {
                    "enabled": bool(ln.get("enabled", True)),
                    "name": ln.get("name"),
                    "color": ln.get("color"),
                    "p1": _norm_point(ln.get("p1")),
                    "p2": _norm_point(ln.get("p2")),
                }
    return out


def _diff_laser_lines(add, from_doc: Any, to_doc: Any) -> None:
    fa = from_doc if isinstance(from_doc, dict) else {}
    ta = to_doc if isinstance(to_doc, dict) else {}
    add("<laser>", "laser.snap_line_id", fa.get("snap_line_id"), ta.get("snap_line_id"))
    a = _laser_line_map(fa)
    b = _laser_line_map(ta)
    for lid in sorted(set(a) | set(b)):
        if lid not in a:
            add("<laser>", _path("laser", lid), None, "added")
        elif lid not in b:
            add("<laser>", _path("laser", lid), "present", None)
        elif a[lid] != b[lid]:
            add("<laser>", _path("laser", lid), a[lid], b[lid])


def _pose_distance(a: Mapping[str, Any], b: Mapping[str, Any]) -> float:
    dx = float(a.get("x", 0.0)) - float(b.get("x", 0.0))
    dy = float(a.get("y", 0.0)) - float(b.get("y", 0.0))
    dz = 0.0
    if "z" in a and "z" in b:
        dz = float(a.get("z", 0.0)) - float(b.get("z", 0.0))
        return math.sqrt(dx * dx + dy * dy + dz * dz)
    return math.sqrt(dx * dx + dy * dy)


def observation_diff(
    from_obs: Mapping[str, Any],
    to_obs: Mapping[str, Any],
    *,
    position_mm: float = 0.1,
    yaw_deg: float = 0.1,
) -> List[Dict[str, Any]]:
    """Tolerance-aware diff for observation documents."""
    report: List[Dict[str, Any]] = []
    comps_from = from_obs.get("components") or {}
    comps_to = to_obs.get("components") or {}
    if not isinstance(comps_from, dict):
        comps_from = {}
    if not isinstance(comps_to, dict):
        comps_to = {}

    for tag_id in sorted(set(comps_from.keys()) | set(comps_to.keys())):
        entry: Dict[str, Any] = {"tag_id": tag_id, "status": "OK"}
        if tag_id not in comps_from:
            entry["status"] = "MISSING_IN_FROM"
            report.append(entry)
            continue
        if tag_id not in comps_to:
            entry["status"] = "MISSING_IN_TO"
            report.append(entry)
            continue

        meas_from = (
            ((comps_from.get(tag_id) or {}).get("statecontrol") or {}).get("measurables")
            or {}
        )
        meas_to = (
            ((comps_to.get(tag_id) or {}).get("statecontrol") or {}).get("measurables")
            or {}
        )
        if not isinstance(meas_from, dict):
            meas_from = {}
        if not isinstance(meas_to, dict):
            meas_to = {}

        pose_from = meas_from.get("pose")
        pose_to = meas_to.get("pose")
        if isinstance(pose_from, dict) and isinstance(pose_to, dict):
            dist = _pose_distance(pose_from, pose_to)
            entry["pose_drift_mm"] = round(dist, 4)
            rot_delta = abs(
                float(pose_from.get("rotation", 0.0)) - float(pose_to.get("rotation", 0.0))
            )
            entry["rotation_drift_deg"] = round(rot_delta, 4)
            if dist > position_mm or rot_delta > yaw_deg:
                entry["status"] = "DRIFT"

        img_from = meas_from.get("camera_image")
        img_to = meas_to.get("camera_image")
        if img_from != img_to:
            entry["camera_image_changed"] = True
            if entry["status"] == "OK":
                entry["status"] = "DRIFT"

        score_from = meas_from.get("last_optimization_score")
        score_to = meas_to.get("last_optimization_score")
        if score_from != score_to:
            entry["optimization_score_changed"] = True
            if entry["status"] == "OK":
                entry["status"] = "DRIFT"

        report.append(entry)
    return report
