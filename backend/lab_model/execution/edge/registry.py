"""Edge agent registry — outbound bench agents attach to the coordinator (Step B)."""
from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

_LOG = logging.getLogger(__name__)

# Fail-closed window: no heartbeat → edge considered gone (ops hardening).
DEFAULT_STALE_AFTER_S = 5.0


@dataclass
class EdgeAgentRecord:
    agent_id: str
    backend_id: str
    label: str = ""
    registered_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_heartbeat_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    meta: Dict[str, Any] = field(default_factory=dict)
    #: Last lab_state snapshot pushed by the edge (single source of truth while attached).
    lab_state: Optional[Dict[str, Any]] = None
    lab_state_updated_at: Optional[datetime] = None
    #: Set when this agent was removed for missing heartbeats.
    disconnected_reason: Optional[str] = None

    def heartbeat_age_s(self, *, now: Optional[datetime] = None) -> float:
        ref = now or datetime.now(timezone.utc)
        return max(0.0, (ref - self.last_heartbeat_at).total_seconds())

    def to_api_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "backend_id": self.backend_id,
            "label": self.label,
            "registered_at": self.registered_at.isoformat(),
            "last_heartbeat_at": self.last_heartbeat_at.isoformat(),
            "heartbeat_age_s": self.heartbeat_age_s(),
            "lab_state_updated_at": (
                self.lab_state_updated_at.isoformat()
                if self.lab_state_updated_at
                else None
            ),
            "has_lab_state": isinstance(self.lab_state, dict),
            "meta": dict(self.meta),
            "disconnected_reason": self.disconnected_reason,
        }


EvictCallback = Callable[[EdgeAgentRecord], None]


class EdgeAgentRegistry:
    """In-memory map of backend_id → attached edge agent."""

    def __init__(
        self,
        *,
        stale_after_s: float = DEFAULT_STALE_AFTER_S,
        on_evict: Optional[EvictCallback] = None,
    ) -> None:
        self._lock = threading.RLock()
        self._by_backend: Dict[str, EdgeAgentRecord] = {}
        self._by_agent: Dict[str, EdgeAgentRecord] = {}
        self._stale_after_s = float(stale_after_s)
        self._on_evict = on_evict
        #: Last known disconnect for UI (backend_id → summary dict).
        self._last_disconnect: Dict[str, Dict[str, Any]] = {}

    @property
    def stale_after_s(self) -> float:
        return self._stale_after_s

    def set_on_evict(self, callback: Optional[EvictCallback]) -> None:
        self._on_evict = callback

    def register(
        self,
        *,
        backend_id: str,
        label: str = "",
        agent_id: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> EdgeAgentRecord:
        backend_id = backend_id.strip()
        if not backend_id:
            raise ValueError("backend_id required")
        aid = (agent_id or "").strip() or f"edge_{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc)
        rec = EdgeAgentRecord(
            agent_id=aid,
            backend_id=backend_id,
            label=(label or "").strip() or aid,
            registered_at=now,
            last_heartbeat_at=now,
            meta=dict(meta or {}),
        )
        with self._lock:
            prev = self._by_backend.get(backend_id)
            if prev is not None:
                self._by_agent.pop(prev.agent_id, None)
            self._by_backend[backend_id] = rec
            self._by_agent[aid] = rec
            self._last_disconnect.pop(backend_id, None)
        return rec

    def heartbeat(
        self,
        agent_id: str,
        *,
        lab_state: Optional[Dict[str, Any]] = None,
    ) -> EdgeAgentRecord:
        with self._lock:
            rec = self._by_agent.get(agent_id.strip())
            if rec is None:
                raise KeyError(agent_id)
            now = datetime.now(timezone.utc)
            rec.last_heartbeat_at = now
            if isinstance(lab_state, dict):
                rec.lab_state = lab_state
                rec.lab_state_updated_at = now
            backend_id = rec.backend_id
            snapshot = rec.lab_state if isinstance(rec.lab_state, dict) else None
        if isinstance(snapshot, dict):
            try:
                from lab_model.coordinator.lab_initialization import note_lab_state

                note_lab_state(
                    backend_id,
                    snapshot,
                    source="edge_heartbeat",
                    edge_attached=True,
                    edge_offline=False,
                )
            except Exception:  # noqa: BLE001
                _LOG.debug("lab_init heartbeat note failed", exc_info=True)
        return rec

    def unregister(self, agent_id: str) -> bool:
        with self._lock:
            rec = self._by_agent.pop(agent_id.strip(), None)
            if rec is None:
                return False
            cur = self._by_backend.get(rec.backend_id)
            if cur is not None and cur.agent_id == rec.agent_id:
                del self._by_backend[rec.backend_id]
            return True

    def get_for_backend(self, backend_id: str) -> Optional[EdgeAgentRecord]:
        """Return the live agent, or ``None`` if missing/stale (stale triggers eviction)."""
        evicted: Optional[EdgeAgentRecord] = None
        with self._lock:
            rec = self._by_backend.get(backend_id.strip())
            if rec is None:
                return None
            if self._is_stale(rec):
                evicted = self._detach_locked(
                    rec,
                    reason=f"stale heartbeat (>{self._stale_after_s:.0f}s)",
                )
            else:
                return rec
        if evicted is not None:
            self._fire_evict(evicted)
        return None

    def is_attached(self, backend_id: str) -> bool:
        return self.get_for_backend(backend_id) is not None

    def get_cached_lab_state(self, backend_id: str) -> Optional[Dict[str, Any]]:
        rec = self.get_for_backend(backend_id)
        if rec is None or not isinstance(rec.lab_state, dict):
            return None
        return dict(rec.lab_state)

    def last_disconnect(self, backend_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._last_disconnect.get(backend_id.strip())
            return dict(row) if row else None

    def sweep_stale(self) -> List[EdgeAgentRecord]:
        """Detach every agent whose heartbeat is older than ``stale_after_s``."""
        evicted: List[EdgeAgentRecord] = []
        with self._lock:
            for rec in list(self._by_backend.values()):
                if self._is_stale(rec):
                    gone = self._detach_locked(
                        rec,
                        reason=f"stale heartbeat (>{self._stale_after_s:.0f}s)",
                    )
                    if gone is not None:
                        evicted.append(gone)
        for rec in evicted:
            self._fire_evict(rec)
        return evicted

    def _is_stale(self, rec: EdgeAgentRecord) -> bool:
        return rec.heartbeat_age_s() > self._stale_after_s

    def _detach_locked(
        self,
        rec: EdgeAgentRecord,
        *,
        reason: str,
    ) -> Optional[EdgeAgentRecord]:
        cur = self._by_backend.get(rec.backend_id)
        if cur is None or cur.agent_id != rec.agent_id:
            return None
        del self._by_backend[rec.backend_id]
        self._by_agent.pop(rec.agent_id, None)
        rec.disconnected_reason = reason
        self._last_disconnect[rec.backend_id] = {
            "backend_id": rec.backend_id,
            "agent_id": rec.agent_id,
            "reason": reason,
            "last_heartbeat_at": rec.last_heartbeat_at.isoformat(),
            "disconnected_at": datetime.now(timezone.utc).isoformat(),
            "stale_after_s": self._stale_after_s,
        }
        _LOG.warning(
            "edge evicted backend=%s agent_id=%s reason=%s",
            rec.backend_id,
            rec.agent_id,
            reason,
        )
        return rec

    def _fire_evict(self, rec: EdgeAgentRecord) -> None:
        cb = self._on_evict
        if cb is None:
            return
        try:
            cb(rec)
        except Exception:  # noqa: BLE001
            _LOG.exception(
                "edge on_evict failed backend=%s agent_id=%s",
                rec.backend_id,
                rec.agent_id,
            )


# Process-wide singleton used by FastAPI (stale window = 5s fail-closed).
edge_agent_registry = EdgeAgentRegistry(stale_after_s=DEFAULT_STALE_AFTER_S)

__all__ = [
    "DEFAULT_STALE_AFTER_S",
    "EdgeAgentRecord",
    "EdgeAgentRegistry",
    "edge_agent_registry",
]
