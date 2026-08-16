"""Lab initialization / runtime_sync for the coordinator.

Fail-closed: missing ``runtime_sync`` ⇒ not ready. Status-change logging
(no per-poll spam). Hard-refuse motion/teleop until initialized.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Mapping, Optional, Set

_LOG = logging.getLogger("lab_init")


class LabNotInitializedError(RuntimeError):
    """Raised when a gated action is scheduled before lab initialization completes."""

    def __init__(self, message: str, *, init: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.init = dict(init or {})

# Last observed runtime_sync.status per backend (process-local).
_last_runtime_sync: Dict[str, str] = {}
# Last composite lab_init key per backend for Twin/coordinator alignment logs.
_last_lab_init_key: Dict[str, str] = {}

#: Motions / teleop / tunable writes that require runtime_sync.status == ready.
REQUIRES_RUNTIME_READY: Set[str] = {
    "MOVE_COMPONENT",
    "MOVE_MOTOR",
    "SET_MOTOR_SETPOINT",
    "MOTOR_SET_ZERO",
    "MOTOR_SEND_HOME",
    "PICK_COMPONENT",
    "HOVER",
    "PLACE_FROM_HOVER",
    "STORE_COMPONENT",
    "PLACE_FROM_STORAGE",
    "AFFIRM_PLACED_AT_CURRENT",
    "REPACK_STORAGE",
    "RECENTER_IN_STORAGE",
    "REMOVE",
    "START_TELEOP",
    "TELEOP_JOG",
    "TELEOP_GOTO",
    "OPTIMIZE",
    "SET_EXPOSURE",
    "SET_LASER_OUTPUT",
}


def runtime_sync_from_state(state: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    if not isinstance(state, Mapping):
        return {"status": "missing"}
    raw = state.get("runtime_sync")
    if not isinstance(raw, Mapping):
        return {"status": "missing"}
    status = str(raw.get("status") or "missing").strip().lower() or "missing"
    out = dict(raw)
    out["status"] = status
    return out


def lab_init_phase(
    state: Optional[Mapping[str, Any]],
    *,
    edge_attached: Optional[bool] = None,
    edge_offline: Optional[bool] = None,
) -> Dict[str, Any]:
    """Derived initialization view for logs / future Twin overlay.

    Fail-closed: missing ``runtime_sync`` ⇒ not ready.
    """
    rs = runtime_sync_from_state(state)
    status = str(rs.get("status") or "missing")
    # Prefer runtime_sync when present (covers in-process mock/sim where
    # edge_attached may be false). Only wait on edge when sync is missing.
    if edge_offline and status not in ("ready", "running", "failed", "pending"):
        phase = "waiting_for_edge"
        ready = False
    elif status == "ready":
        phase = "ready"
        ready = True
    elif status == "running":
        phase = "measuring_inventory"
        ready = False
    elif status == "failed":
        phase = "failed"
        ready = False
    elif status == "pending":
        phase = "starting"
        ready = False
    elif edge_offline or edge_attached is False:
        phase = "waiting_for_edge"
        ready = False
    else:
        phase = "missing_runtime_sync"
        ready = False
    return {
        "ready": ready,
        "phase": phase,
        "runtime_sync": status,
        "edge_attached": edge_attached,
        "edge_offline": edge_offline,
        "errors": list(rs.get("errors") or []) if isinstance(rs.get("errors"), list) else [],
    }


def note_lab_state(
    backend_id: str,
    state: Optional[Mapping[str, Any]],
    *,
    source: str,
    edge_attached: Optional[bool] = None,
    edge_offline: Optional[bool] = None,
) -> Dict[str, Any]:
    """Log when runtime_sync or derived lab_init phase changes for ``backend_id``."""
    bid = (backend_id or "").strip() or "<unknown>"
    init = lab_init_phase(
        state,
        edge_attached=edge_attached,
        edge_offline=edge_offline,
    )
    rs_status = str(init.get("runtime_sync") or "missing")
    prev_rs = _last_runtime_sync.get(bid)
    if prev_rs != rs_status:
        _LOG.info(
            "[lab_init] backend=%s source=%s runtime_sync %s → %s",
            bid,
            source,
            prev_rs or "(none)",
            rs_status,
        )
        _last_runtime_sync[bid] = rs_status

    key = f"{init.get('phase')}|{rs_status}|{edge_attached}|{edge_offline}"
    prev_key = _last_lab_init_key.get(bid)
    if prev_key != key:
        err_n = len(init.get("errors") or [])
        _LOG.info(
            "[lab_init] backend=%s source=%s phase=%s ready=%s "
            "runtime_sync=%s edge_attached=%s edge_offline=%s errors=%d",
            bid,
            source,
            init.get("phase"),
            init.get("ready"),
            rs_status,
            edge_attached,
            edge_offline,
            err_n,
        )
        if err_n:
            _LOG.warning(
                "[lab_init] backend=%s errors=%s",
                bid,
                init.get("errors"),
            )
        _last_lab_init_key[bid] = key
    return init


def ensure_action_allowed(
    backend_id: str,
    action: str,
    state: Optional[Mapping[str, Any]],
    *,
    edge_attached: Optional[bool] = None,
    edge_offline: Optional[bool] = None,
) -> Dict[str, Any]:
    """Log + hard-refuse gated actions when the lab is not initialized.

    Returns the derived ``lab_initialization`` dict. Raises
    :class:`LabNotInitializedError` when ``action`` requires READY and the
    lab is not ready (fail-closed on missing ``runtime_sync``).
    """
    act = str(action or "").strip().upper()
    bid = (backend_id or "").strip() or "<unknown>"
    init = lab_init_phase(
        state,
        edge_attached=edge_attached,
        edge_offline=edge_offline,
    )
    if act not in REQUIRES_RUNTIME_READY:
        return init
    if init.get("ready"):
        _LOG.info(
            "[lab_init] backend=%s schedule action=%s ok (lab initialized)",
            bid,
            act,
        )
        return init
    _LOG.warning(
        "[lab_init] backend=%s schedule action=%s REFUSED phase=%s "
        "runtime_sync=%s (lab not initialized)",
        bid,
        act,
        init.get("phase"),
        init.get("runtime_sync"),
    )
    raise LabNotInitializedError(
        f"lab not initialized (phase={init.get('phase')}, "
        f"runtime_sync={init.get('runtime_sync')}); "
        f"cannot schedule {act}",
        init=init,
    )


def log_schedule_gate(
    backend_id: str,
    action: str,
    state: Optional[Mapping[str, Any]],
    *,
    edge_attached: Optional[bool] = None,
    edge_offline: Optional[bool] = None,
) -> Dict[str, Any]:
    """Alias for :func:`ensure_action_allowed` (raises when not ready)."""
    return ensure_action_allowed(
        backend_id,
        action,
        state,
        edge_attached=edge_attached,
        edge_offline=edge_offline,
    )
