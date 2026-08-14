"""Coordinator-owned session checkpoint save / offers / apply.

Works for in-process mock hosts and HTTP-edge backends. Checkpoint files live
on ``LabViewPaths.session_checkpoint_json`` (coordinator_data for HTTP-only
backends). Apply restores tunables/measurables into the Twin lab-state store —
hardware is never commanded.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional

from lab_model.coordinator.backends.lab_view_config import atomic_write_json
from lab_model.language.domain.component import (
    get_measurables,
    get_tunables,
    measurables_bucket,
    tunables_bucket,
)
from lab_model.coordinator.state.runtime_manager import MutationKind

_ENV_TRUE = frozenset({"1", "true", "yes", "on"})
_ENV_FALSE = frozenset({"0", "false", "no", "off"})


def session_checkpoint_enabled(rt: Any) -> bool:
    """Feature gate from ``SESSION_CHECKPOINT`` env, else ``rt.manifest``."""
    raw = (os.getenv("SESSION_CHECKPOINT") or "").strip().lower()
    if raw in _ENV_TRUE:
        return True
    if raw in _ENV_FALSE:
        return False
    lab = getattr(rt, "lab", None)
    host_fn = getattr(lab, "session_checkpoint_enabled", None)
    if callable(host_fn):
        try:
            return bool(host_fn())
        except Exception:
            pass
    manifest = getattr(rt, "manifest", None)
    return bool(getattr(manifest, "session_checkpoint", False))


def checkpoint_path_for(rt: Any) -> str:
    paths = getattr(rt, "paths", None)
    return str(getattr(paths, "session_checkpoint_json", "") or "").strip()


def reconciliation_thresholds(rt: Any):
    """Prefer host helper; else this runtime's manifest / env defaults."""
    from mock_backend.shared.session_checkpoint import ReconciliationThresholds

    lab = getattr(rt, "lab", None)
    host_fn = getattr(lab, "session_reconciliation_thresholds", None)
    if callable(host_fn):
        try:
            return host_fn()
        except Exception:
            pass

    def _env_override(name: str):
        raw = (os.getenv(name) or "").strip()
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError:
            return None

    manifest = getattr(rt, "manifest", None)
    pos = _env_override("SESSION_REC_THRESH_MM")
    yaw = _env_override("SESSION_REC_THRESH_DEG")
    if pos is None:
        pos = float(getattr(manifest, "reconciliation_position_mm", 8.0) or 8.0)
    if yaw is None:
        yaw = float(getattr(manifest, "reconciliation_yaw_deg", 10.0) or 10.0)
    return ReconciliationThresholds(position_mm=pos, yaw_deg=yaw)


def current_lab_state_for_reconcile(rt: Any) -> Dict[str, Any]:
    """Best available Twin-shaped snapshot for offer / IDLE gating."""
    lab = getattr(rt, "lab", None)
    getter = getattr(lab, "get_lab_state", None)
    if callable(getter):
        try:
            snap = getter()
            if isinstance(snap, dict):
                return snap
        except Exception:
            pass
    store = getattr(rt, "lab_state_store", None)
    if store is not None and hasattr(store, "snapshot"):
        snap = store.snapshot()
        if isinstance(snap, dict):
            return snap
    return {}


def save_session_checkpoint_for_runtime(
    rt: Any, *, snapshot: Optional[Mapping[str, Any]] = None
) -> Optional[str]:
    """Write ``session_last_lab_state.json`` when the feature is enabled.

    Prefers the in-process host saver (mock) so motor-side hooks stay intact;
    otherwise persists the Twin store snapshot to ``rt.paths``.
    """
    if not session_checkpoint_enabled(rt):
        return None

    lab = getattr(rt, "lab", None)
    saver = getattr(lab, "save_session_checkpoint_if_enabled", None)
    if callable(saver) and snapshot is None:
        saver()
        return checkpoint_path_for(rt) or None

    from mock_backend.shared.session_checkpoint import build_checkpoint_document

    path = checkpoint_path_for(rt)
    if not path:
        return None

    if snapshot is None:
        store = getattr(rt, "lab_state_store", None)
        if store is None:
            try:
                from lab_model.coordinator.state.lab_state_store import (
                    ensure_lab_state_store,
                )

                store = ensure_lab_state_store(rt)
            except Exception:
                store = None
        # Prefer Twin store (no edge round-trip) — important on shutdown.
        if store is not None and hasattr(store, "snapshot"):
            snap = store.snapshot()
        else:
            snap = current_lab_state_for_reconcile(rt)
    else:
        snap = dict(snapshot)
    if not isinstance(snap, dict):
        return None

    lab_mode = (
        str(getattr(rt, "lab_mode", "") or "").upper()
        or str(getattr(getattr(rt, "manifest", None), "lab_mode", "") or "").upper()
        or "REAL"
    )
    doc = build_checkpoint_document(lab_mode, snap)
    atomic_write_json(path, doc)
    n_comp = len((doc.get("lab_state") or {}).get("components") or {})
    print(
        f"[session-checkpoint] saved {path} "
        f"(lab_mode={doc.get('lab_mode')}, components={n_comp}, "
        f"saved_at={doc.get('saved_at')}, backend={getattr(rt, 'backend_id', '')!r})",
        flush=True,
    )
    return path


def apply_session_reconciliation_tags_for_runtime(
    rt: Any, tag_ids: List[str]
) -> List[str]:
    """Restore checkpoint tunables/measurables for allowed tags into Twin state."""
    lab = getattr(rt, "lab", None)
    host_apply = getattr(lab, "apply_session_reconciliation_tags", None)
    if callable(host_apply):
        return list(host_apply(tag_ids) or [])

    from mock_backend.shared.session_checkpoint import (
        checkpoint_lab_state,
        merge_offers_tag_ids,
        read_checkpoint_document,
    )

    from lab_model.coordinator.state.lab_state_store import ensure_lab_state_store

    path = checkpoint_path_for(rt)
    thresholds = reconciliation_thresholds(rt)
    store = ensure_lab_state_store(rt)
    # Same snapshot basis as offers (Twin store + edge merge when available).
    current = current_lab_state_for_reconcile(rt)
    chk_doc = read_checkpoint_document(path) if path else None
    chk_state = checkpoint_lab_state(chk_doc)
    if not isinstance(chk_state, dict) or not isinstance(current, dict):
        return []

    allow = set(
        merge_offers_tag_ids(
            current_state=current,
            checkpoint_state=chk_state,
            thresholds=thresholds,
        )
    )
    meta_ch = chk_state.get("components") or {}
    if not isinstance(meta_ch, dict):
        return []

    merged_ids: List[str] = []
    wanted = [str(t) for t in (tag_ids or []) if str(t)]

    def _mutate(state: Dict[str, Any]) -> None:
        comps = state.setdefault("components", {})
        if not isinstance(comps, dict):
            return
        for tid in wanted:
            if tid not in allow or tid not in comps:
                continue
            src_ent = meta_ch.get(tid)
            dst = comps.get(tid)
            if not isinstance(src_ent, dict) or not isinstance(dst, dict):
                continue
            st_t = json.loads(json.dumps(get_tunables(src_ent)))
            st_m = json.loads(json.dumps(get_measurables(src_ent)))
            dst_tun = tunables_bucket(dst)
            dst_meas = measurables_bucket(dst)
            dst_tun.clear()
            dst_tun.update(st_t)
            dst_meas.clear()
            dst_meas.update(st_m)
            merged_ids.append(tid)
        state["last_updated"] = datetime.now().isoformat()

    store.mutate(
        _mutate,
        kind=MutationKind.ADMINISTRATIVE_LOAD,
        source="session_reconciliation",
        persist=True,
    )
    return merged_ids


def offers_dict_for_runtime(rt: Any) -> Dict[str, Any]:
    """Build the GET /api/session-reconciliation/offers payload for ``rt``."""
    from mock_backend.shared.session_checkpoint import (
        checkpoint_age_hours,
        checkpoint_lab_state,
        merge_offers_with_debug,
        read_checkpoint_document,
        stale_warning_hours_from_manifest,
    )

    chk_path = checkpoint_path_for(rt)
    stale_warn_hours = stale_warning_hours_from_manifest()
    thresholds = reconciliation_thresholds(rt)
    thresholds_dict = {
        "position_mm": thresholds.position_mm,
        "yaw_deg": thresholds.yaw_deg,
    }
    enabled = session_checkpoint_enabled(rt)

    doc = read_checkpoint_document(chk_path) if chk_path else None
    age_h = checkpoint_age_hours(doc.get("saved_at")) if isinstance(doc, dict) else None
    resp: Dict[str, Any] = {
        "enabled": enabled,
        "skipped_reason": None,
        "checkpoint_path": chk_path or None,
        "checkpoint_saved_at": doc.get("saved_at") if isinstance(doc, dict) else None,
        "checkpoint_lab_mode": doc.get("lab_mode") if isinstance(doc, dict) else None,
        "age_hours": age_h,
        "stale_warning_hours": stale_warn_hours,
        "stale_warning": False,
        "thresholds": thresholds_dict,
        "offers": [],
    }
    if age_h is not None:
        resp["stale_warning"] = float(age_h) >= float(stale_warn_hours)

    if not enabled:
        resp["skipped_reason"] = "feature_disabled"
        return resp

    cur = current_lab_state_for_reconcile(rt)
    if not isinstance(cur, dict) or not cur:
        resp["skipped_reason"] = "lab_unavailable"
        resp["enabled"] = False
        return resp

    status = cur.get("system_status")
    if status != "IDLE":
        resp["skipped_reason"] = f"busy:{status}"
        return resp

    chk_state = checkpoint_lab_state(doc) if isinstance(doc, dict) else None
    if not isinstance(chk_state, dict):
        resp["skipped_reason"] = "no_checkpoint"
        return resp

    offer_ids, merge_debug = merge_offers_with_debug(
        current_state=cur,
        checkpoint_state=chk_state,
        thresholds=thresholds,
    )
    resp["offers"] = [{"tag_id": tid} for tid in offer_ids]
    resp["debug"] = merge_debug
    try:
        manifest = getattr(rt, "manifest", None)
        if manifest is not None and hasattr(manifest, "as_dict"):
            md = manifest.as_dict()
            resp["manifest"] = {
                "session_checkpoint": bool(md.get("session_checkpoint")),
                "session_reconciliation": md.get("session_reconciliation"),
            }
        else:
            resp["manifest"] = None
    except Exception:
        resp["manifest"] = None

    bid = getattr(rt, "backend_id", "")
    print(
        f"[session-reconcile] backend_id={bid!r} "
        f"enabled={resp['enabled']} skipped={resp.get('skipped_reason')!r} "
        f"offers={len(offer_ids)} checkpoint={chk_path!r} "
        f"exists={os.path.isfile(chk_path) if chk_path else False} "
        f"debug={merge_debug}",
        flush=True,
    )
    return resp


__all__ = [
    "apply_session_reconciliation_tags_for_runtime",
    "checkpoint_path_for",
    "current_lab_state_for_reconcile",
    "offers_dict_for_runtime",
    "reconciliation_thresholds",
    "save_session_checkpoint_for_runtime",
    "session_checkpoint_enabled",
]
