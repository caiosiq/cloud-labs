"""Last-session checkpoint and UI reconciliation helpers.

Writes ``get_lab_state()``-shaped snapshots next to lab_view JSON so a restart
can offer to restore tunables/measurables when measured poses still match within
noise.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from lab_communicator.shared.lab_view_config import atomic_write_json, get_lab_view_paths_optional

CHECKPOINT_VERSION = 1


@dataclass(frozen=True)
class ReconciliationThresholds:
    """Planar XY distance (mm) and yaw magnitude difference (degrees)."""

    position_mm: float
    yaw_deg: float


def env_stale_warning_hours(default: float = 168.0) -> float:
    raw = (os.getenv("SESSION_CHECKPOINT_WARN_HOURS") or "").strip()
    if not raw:
        return float(default)
    try:
        return float(raw)
    except ValueError:
        return float(default)


def default_thresholds_from_env() -> ReconciliationThresholds:
    def _f(name: str, default: str) -> float:
        raw = (os.getenv(name) or "").strip()
        if not raw:
            return float(default)
        try:
            return float(raw)
        except ValueError:
            return float(default)

    return ReconciliationThresholds(
        position_mm=_f("SESSION_REC_THRESH_MM", "2"),
        yaw_deg=_f("SESSION_REC_THRESH_DEG", "5"),
    )


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

    slug = {"tunables": part.get("tunables"), "measurables": part.get("measurables")}
    return json.dumps(round_float_tree(slug), sort_keys=True)


def merge_offers_tag_ids(
    *,
    current_state: Dict[str, Any],
    checkpoint_state: Dict[str, Any],
    thresholds: ReconciliationThresholds,
) -> List[str]:
    """Tags in both snapshots whose meas poses match but tunables/meas differ."""

    cc = current_state.get("components") or {}
    ck = checkpoint_state.get("components") or {}
    if not isinstance(cc, dict) or not isinstance(ck, dict):
        return []

    shared = sorted(set(cc.keys()) & set(ck.keys()))
    out: List[str] = []
    for tid in shared:
        a = cc[tid]
        b = ck[tid]
        if not isinstance(a, dict) or not isinstance(b, dict):
            continue
        pa = (a.get("measurables") or {}).get("pose") or {}
        pb = (b.get("measurables") or {}).get("pose") or {}
        if not isinstance(pa, dict) or not isinstance(pb, dict):
            continue
        if not poses_close(pa, pb, thresholds):
            continue
        if _canonical_blob(a) == _canonical_blob(b):
            continue
        out.append(tid)
    return out


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
        return None
    path = getattr(paths, "session_checkpoint_json", None)
    if not path:
        return None
    doc = build_checkpoint_document(lab_mode, lab_state_snapshot)
    atomic_write_json(path, doc)
    return path
