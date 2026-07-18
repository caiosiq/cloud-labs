"""Synchronous edge command queue — coordinator waits, edge polls (Step B.1)."""
from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


@dataclass
class EdgeCommand:
    command_id: str
    backend_id: str
    kind: str  # primitive | get_lab_state (legacy: kernel_eval rewritten to EVAL_KERNEL)
    payload: Dict[str, Any]
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    done: threading.Event = field(default_factory=threading.Event)

    def to_api_dict(self) -> Dict[str, Any]:
        return {
            "command_id": self.command_id,
            "backend_id": self.backend_id,
            "kind": self.kind,
            "payload": dict(self.payload),
            "created_at": self.created_at.isoformat(),
        }


class EdgeCommandQueue:
    """Per-backend FIFO of imperative / eval commands for an attached edge."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._pending: Dict[str, List[str]] = {}
        self._by_id: Dict[str, EdgeCommand] = {}

    def submit(
        self,
        *,
        backend_id: str,
        kind: str,
        payload: Dict[str, Any],
    ) -> EdgeCommand:
        backend_id = backend_id.strip()
        kind = kind.strip()
        if not backend_id or not kind:
            raise ValueError("backend_id and kind required")
        cmd = EdgeCommand(
            command_id=f"ecmd_{uuid.uuid4().hex}",
            backend_id=backend_id,
            kind=kind,
            payload=dict(payload),
        )
        with self._lock:
            self._by_id[cmd.command_id] = cmd
            self._pending.setdefault(backend_id, []).append(cmd.command_id)
        return cmd

    def poll(self, backend_id: str) -> Optional[EdgeCommand]:
        with self._lock:
            q = self._pending.get(backend_id.strip()) or []
            while q:
                cid = q.pop(0)
                cmd = self._by_id.get(cid)
                if cmd is not None and not cmd.done.is_set():
                    return cmd
            return None

    def complete(
        self,
        command_id: str,
        *,
        result: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> EdgeCommand:
        with self._lock:
            cmd = self._by_id.get(command_id.strip())
            if cmd is None:
                raise KeyError(command_id)
            cmd.result = result
            cmd.error = error
            cmd.done.set()
            return cmd

    def wait(
        self,
        command_id: str,
        *,
        timeout_s: float = 120.0,
    ) -> EdgeCommand:
        with self._lock:
            cmd = self._by_id.get(command_id.strip())
            if cmd is None:
                raise KeyError(command_id)
        ok = cmd.done.wait(timeout=timeout_s)
        if not ok:
            cmd.error = cmd.error or f"timed out after {timeout_s}s waiting for edge"
            cmd.done.set()
        return cmd

    def submit_and_wait(
        self,
        *,
        backend_id: str,
        kind: str,
        payload: Dict[str, Any],
        timeout_s: float = 120.0,
    ) -> EdgeCommand:
        cmd = self.submit(backend_id=backend_id, kind=kind, payload=payload)
        return self.wait(cmd.command_id, timeout_s=timeout_s)


edge_command_queue = EdgeCommandQueue()

__all__ = ["EdgeCommand", "EdgeCommandQueue", "edge_command_queue"]
