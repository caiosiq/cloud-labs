"""Last-session checkpoint and UI reconciliation helpers.

Writes ``get_lab_state()``-shaped snapshots next to lab_view JSON so a restart
can offer to restore tunables/measurables when measured poses still match within
noise. Thresholds and enable flag come from ``lab_manifest.json``.
"""

from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from lab_model.coordinator.backends.lab_view_config import atomic_write_json, get_lab_view_paths_optional
from lab_model.language.domain.component import get_measurables, get_tunables, reported_pose

logger = logging.getLogger(__name__)

CHECKPOINT_VERSION = 1


@dataclass(frozen=True)
class ReconciliationThresholds:
    """Planar XY distance (mm) and yaw magnitude difference (degrees)."""

    position_mm: float
    yaw_deg: float


def reconciliation_thresholds_from_manifest() -> ReconciliationThresholds:
    """Load tolerances from ``lab_manifest.json`` (env vars override if set)."""

    def _env_override(name: str) -> Optional[float]:
        raw = (os.getenv(name) or "").strip()
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError:
            return None

    try:
        from lab_model.coordinator.backends.lab_view_config import get_lab_manifest

        m = get_lab_manifest()
        pos = _env_override("SESSION_REC_THRESH_MM")
        yaw = _env_override("SESSION_REC_THRESH_DEG")
        return ReconciliationThresholds(
            position_mm=pos if pos is not None else float(m.reconciliation_position_mm),
            yaw_deg=yaw if yaw is not None else float(m.reconciliation_yaw_deg),
        )
    except Exception:
        return ReconciliationThresholds(
            position_mm=_env_override("SESSION_REC_THRESH_MM") or 2.0,
            yaw_deg=_env_override("SESSION_REC_THRESH_DEG") or 5.0,
        )


def stale_warning_hours_from_manifest() -> float:
    raw = (os.getenv("SESSION_CHECKPOINT_WARN_HOURS") or "").strip()
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    try:
        from lab_model.coordinator.backends.lab_view_config import get_lab_manifest

        return float(get_lab_manifest().reconciliation_stale_warning_hours)
    except Exception:
        return 168.0


def default_thresholds_from_env() -> ReconciliationThresholds:
    """Backward-compatible alias â€” prefer :func:`reconciliation_thresholds_from_manifest`."""

    return reconciliation_thresholds_from_manifest()


def yaw_diff_deg(a: Any, b: Any) -> float:
    """Smallest angular difference in degrees."""

    try:
        fa = float(a)
        fb = float(b)
    except (TypeError, ValueError):
        return 180.0
    d = abs(fa - fb) % 360.0
    return min(d, 360.0 - d)


def _pose_xy_yaw(pose: Dict[str, Any]) -> Tuple[float, float, float]:
    try:
        x = float((pose or {}).get("x", 0.0))
        y = float((pose or {}).get("y", 0.0))
        rot = float((pose or {}).get("rotation", 0.0))
    except (TypeError, ValueError):
        return 0.0, 0.0, 0.0
    return x, y, rot


def poses_close(
    a: Dict[str, Any],
    b: Dict[str, Any],
    th: ReconciliationThresholds,
) -> bool:
    x1, y1, r1 = _pose_xy_yaw(a)
    x2, y2, r2 = _pose_xy_yaw(b)
    dist = math.hypot(x1 - x2, y1 - y2)
    if dist > float(th.position_mm):
        return False
    return yaw_diff_deg(r1, r2) <= float(th.yaw_deg)


def checkpoint_age_hours(saved_at: Optional[str], now: Optional[datetime] = None) -> Optional[float]:
    if not saved_at or not isinstance(saved_at, str):
        return None
    now = now or datetime.now(timezone.utc)
    try:
        normalized = saved_at.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (now - dt).total_seconds() / 3600.0)
    except ValueError:
        return None


def round_float_tree(obj: Any, digits: int = 4) -> Any:
    if isinstance(obj, float):
        return round(obj, digits)
    if isinstance(obj, dict):
        return {str(k): round_float_tree(v, digits) for k, v in obj.items()}
    if isinstance(obj, list):
        return [round_float_tree(v, digits) for v in obj]
    return obj


def _canonical_blob(part: Dict[str, Any]) -> str:
    """Stable compare blob for tunables + measurables."""

    slug = {
        "tunables": get_tunables(part),
        "measurables": get_measurables(part),
    }
    return json.dumps(round_float_tree(slug), sort_keys=True)


def merge_offers_tag_ids(
    *,
    current_state: Dict[str, Any],
    checkpoint_state: Dict[str, Any],
    thresholds: ReconciliationThresholds,
) -> List[str]:
    """Tags in both snapshots whose meas poses match but tunables/meas differ."""

    offers, _ = merge_offers_with_debug(
        current_state=current_state,
        checkpoint_state=checkpoint_state,
        thresholds=thresholds,
    )
    return offers


def merge_offers_with_debug(
    *,
    current_state: Dict[str, Any],
    checkpoint_state: Dict[str, Any],
    thresholds: ReconciliationThresholds,
) -> Tuple[List[str], Dict[str, Any]]:
    cc = current_state.get("components") or {}
    ck = checkpoint_state.get("components") or {}
    debug: Dict[str, Any] = {
        "shared_tag_count": 0,
        "pose_mismatch_tags": [],
        "already_synced_tags": [],
        "offered_tags": [],
        "only_in_current": [],
        "only_in_checkpoint": [],
    }
    if not isinstance(cc, dict) or not isinstance(ck, dict):
        return [], debug

    only_cur = sorted(set(cc.keys()) - set(ck.keys()))
    only_ck = sorted(set(ck.keys()) - set(cc.keys()))
    debug["only_in_current"] = only_cur[:20]
    debug["only_in_checkpoint"] = only_ck[:20]
    shared = sorted(set(cc.keys()) & set(ck.keys()))
    debug["shared_tag_count"] = len(shared)

    out: List[str] = []
    for tid in shared:
        a = cc[tid]
        b = ck[tid]
        if not isinstance(a, dict) or not isinstance(b, dict):
            continue
        pa = reported_pose(a) if isinstance(a, dict) else {}
        pb = reported_pose(b) if isinstance(b, dict) else {}
        if not isinstance(pa, dict) or not isinstance(pb, dict):
            continue
        if not poses_close(pa, pb, thresholds):
            if len(debug["pose_mismatch_tags"]) < 20:
                x1, y1, _ = _pose_xy_yaw(pa)
                x2, y2, _ = _pose_xy_yaw(pb)
                debug["pose_mismatch_tags"].append(
                    {
                        "tag_id": tid,
                        "delta_mm": round(math.hypot(x1 - x2, y1 - y2), 3),
                        "delta_yaw_deg": round(yaw_diff_deg(pa.get("rotation"), pb.get("rotation")), 3),
                    }
                )
            continue
        if _canonical_blob(a) == _canonical_blob(b):
            if len(debug["already_synced_tags"]) < 20:
                debug["already_synced_tags"].append(tid)
            continue
        out.append(tid)
        if len(debug["offered_tags"]) < 20:
            debug["offered_tags"].append(tid)
    return out, debug


def read_checkpoint_document(path: str) -> Optional[Dict[str, Any]]:
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    return doc if isinstance(doc, dict) else None


def checkpoint_lab_state(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not doc:
        return None
    ls = doc.get("lab_state")
    if isinstance(ls, dict):
        return ls
    return None


def build_checkpoint_document(lab_mode: str, lab_state_snapshot: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "version": CHECKPOINT_VERSION,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "lab_mode": str(lab_mode or "").upper(),
        "lab_state": json.loads(json.dumps(lab_state_snapshot)),
    }


def persist_checkpoint(lab_mode: str, lab_state_snapshot: Dict[str, Any]) -> Optional[str]:
    paths = get_lab_view_paths_optional()
    if paths is None:
        logger.warning("[session-checkpoint] persist skipped: lab_view paths not bootstrapped")
        return None
    path = getattr(paths, "session_checkpoint_json", None)
    if not path:
        logger.warning("[session-checkpoint] persist skipped: no session_checkpoint_json path")
        return None
    doc = build_checkpoint_document(lab_mode, lab_state_snapshot)
    atomic_write_json(path, doc)
    n_comp = len((doc.get("lab_state") or {}).get("components") or {})
    print(
        f"[session-checkpoint] saved {path} "
        f"(lab_mode={doc.get('lab_mode')}, components={n_comp}, saved_at={doc.get('saved_at')})",
        flush=True,
    )
    logger.info(
        "session checkpoint saved path=%s components=%s",
        path,
        n_comp,
    )
    return path
