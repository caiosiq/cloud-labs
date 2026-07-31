"""Unified southbound EdgeClient (Phase 3).

Priority when resolving a client for a backend:

1. **Poll** — Step-B agent attached (``edge_agent_registry``)
2. **HTTP** — ``backends.json`` ``edge.base_url`` (Edge Contract v1)
3. **In-process** — local ``LabCommunicator`` via ``execute_validated_command``

Twin dedicated routes and ``/api/command`` should call this module so they share
one semantics surface (no teleop/video side doors around the edge).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional, Protocol

import httpx

from lab_model.execution.edge.commands import edge_command_queue
from lab_model.execution.edge.endpoint import EdgeEndpointConfig
from lab_model.execution.edge.registry import edge_agent_registry
from lab_model.language.primitives.dispatch import (
    execute_validated_command,
    parse_command_payload,
)

_LOG = logging.getLogger(__name__)

CONTRACT_VERSION = "1.0.0"


class EdgeTransport(str, Enum):
    IN_PROCESS = "in_process"
    POLL = "poll"
    HTTP = "http"


@dataclass
class EdgeExecuteResult:
    """Normalized southbound result for coordinator routes."""

    ok: bool
    transport: EdgeTransport
    result: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    epoch_ms: Optional[int] = None
    latch_quality: Optional[str] = None
    status: Optional[str] = None  # Edge Contract execute status when HTTP

    def as_api_dict(self) -> Dict[str, Any]:
        out = dict(self.result)
        if self.epoch_ms is not None and "epoch_ms" not in out:
            out["epoch_ms"] = self.epoch_ms
        if self.latch_quality is not None and "latch_quality" not in out:
            out["latch_quality"] = self.latch_quality
        if "status" not in out:
            out["status"] = "ok" if self.ok else "error"
        if not self.ok and self.error and "detail" not in out:
            out["detail"] = self.error
        return out


class EdgeClient(Protocol):
    transport: EdgeTransport

    async def execute_command(
        self,
        command: Dict[str, Any],
        *,
        timeout_s: float = 120.0,
        lease_id: Optional[str] = None,
    ) -> EdgeExecuteResult: ...

    def get_capabilities(self) -> Optional[Dict[str, Any]]: ...

    def get_bench(self) -> Optional[Dict[str, Any]]: ...

    def get_library(self) -> Optional[Dict[str, Any]]: ...

    def get_inventory(self) -> Optional[Dict[str, Any]]: ...

    def get_lab_state(self) -> Optional[Dict[str, Any]]: ...

    def absolute_stream_url(self, path: str) -> Optional[str]: ...

    def fetch_bytes(self, path: str) -> Optional[bytes]: ...


def command_to_execute_body(command: Dict[str, Any]) -> Dict[str, Any]:
    """Map coordinator ``{action, target_id, parameters}`` → Edge Contract execute."""
    primitive = str(command.get("action") or command.get("primitive") or "").strip()
    args: Dict[str, Any] = {}
    params = command.get("parameters")
    if isinstance(params, dict):
        args.update(params)
    elif hasattr(params, "model_dump"):
        args.update(params.model_dump(exclude_none=True))
    target = command.get("target_id")
    if target is not None and "tag_id" not in args and "target_id" not in args:
        args["tag_id"] = target
        args["target_id"] = target
    # Twin live-feed bodies put channel at top level.
    if command.get("channel") is not None and "channel" not in args:
        args["channel"] = command["channel"]
    body: Dict[str, Any] = {"primitive": primitive, "args": args}
    if command.get("idempotency_key"):
        body["idempotency_key"] = command["idempotency_key"]
    if command.get("lease_id") or command.get("lease_token"):
        body["lease_token"] = command.get("lease_token") or command.get("lease_id")
    return body


@dataclass
class InProcessEdgeClient:
    """Local communicator — default teaching / CI path."""

    lab: Any
    transport: EdgeTransport = EdgeTransport.IN_PROCESS

    async def execute_command(
        self,
        command: Dict[str, Any],
        *,
        timeout_s: float = 120.0,
        lease_id: Optional[str] = None,
    ) -> EdgeExecuteResult:
        del timeout_s  # in-process; FastAPI await bounds the call
        if self.lab is None:
            return EdgeExecuteResult(
                ok=False,
                transport=self.transport,
                error="lab not initialized",
            )
        try:
            cmd = parse_command_payload(command)
        except Exception as exc:  # noqa: BLE001 — pydantic ValidationError etc.
            return EdgeExecuteResult(
                ok=False,
                transport=self.transport,
                error=str(exc),
            )
        if lease_id:
            self.lab._command_lease_id = lease_id
        try:
            result = await execute_validated_command(self.lab, cmd)
        except Exception as exc:  # noqa: BLE001
            return EdgeExecuteResult(
                ok=False,
                transport=self.transport,
                error=str(exc),
            )
        payload = result if isinstance(result, dict) else {"status": "ok"}
        epoch = payload.get("epoch_ms")
        latch = payload.get("latch_quality")
        return EdgeExecuteResult(
            ok=True,
            transport=self.transport,
            result=payload,
            epoch_ms=int(epoch) if isinstance(epoch, int) else None,
            latch_quality=str(latch) if latch else None,
            status="completed",
        )

    def get_capabilities(self) -> Optional[Dict[str, Any]]:
        return None

    def get_bench(self) -> Optional[Dict[str, Any]]:
        return None

    def get_library(self) -> Optional[Dict[str, Any]]:
        return None

    def get_inventory(self) -> Optional[Dict[str, Any]]:
        return None

    def get_lab_state(self) -> Optional[Dict[str, Any]]:
        return None

    def absolute_stream_url(self, path: str) -> Optional[str]:
        return None

    def fetch_bytes(self, path: str) -> Optional[bytes]:
        return None


@dataclass
class PollEdgeClient:
    """Step-B poll queue (attached ``mock_backend_agent`` / future edge agents)."""

    backend_id: str
    transport: EdgeTransport = EdgeTransport.POLL

    async def execute_command(
        self,
        command: Dict[str, Any],
        *,
        timeout_s: float = 120.0,
        lease_id: Optional[str] = None,
    ) -> EdgeExecuteResult:
        if lease_id and isinstance(command, dict) and "lease_id" not in command:
            command = {**command, "lease_id": lease_id}
        if not edge_agent_registry.is_attached(self.backend_id):
            return EdgeExecuteResult(
                ok=False,
                transport=self.transport,
                error=f"no edge agent attached for {self.backend_id!r}",
            )
        try:
            done = await asyncio.to_thread(
                edge_command_queue.submit_and_wait,
                backend_id=self.backend_id,
                kind="primitive",
                payload={"command": command},
                timeout_s=timeout_s,
            )
        except Exception as exc:  # noqa: BLE001
            return EdgeExecuteResult(
                ok=False,
                transport=self.transport,
                error=str(exc),
            )
        if done.error:
            return EdgeExecuteResult(
                ok=False,
                transport=self.transport,
                error=done.error,
            )
        result = done.result if isinstance(done.result, dict) else {"status": "ok"}
        epoch = result.get("epoch_ms")
        latch = result.get("latch_quality")
        return EdgeExecuteResult(
            ok=True,
            transport=self.transport,
            result=result,
            epoch_ms=int(epoch) if isinstance(epoch, int) else None,
            latch_quality=str(latch) if latch else None,
            status="completed",
        )

    def get_capabilities(self) -> Optional[Dict[str, Any]]:
        return None

    def get_bench(self) -> Optional[Dict[str, Any]]:
        return None

    def get_library(self) -> Optional[Dict[str, Any]]:
        return None

    def get_inventory(self) -> Optional[Dict[str, Any]]:
        return None

    def get_lab_state(self) -> Optional[Dict[str, Any]]:
        return None

    def absolute_stream_url(self, path: str) -> Optional[str]:
        return None

    def fetch_bytes(self, path: str) -> Optional[bytes]:
        return None


@dataclass
class HttpEdgeClient:
    """Edge Contract v1 HTTP client (``POST /execute``, capabilities, streams)."""

    base_url: str
    contract_version: str = CONTRACT_VERSION
    lan_direct_ok: bool = False
    transport: EdgeTransport = EdgeTransport.HTTP
    _caps_cache: Optional[Dict[str, Any]] = field(default=None, repr=False)
    _bench_cache: Optional[Dict[str, Any]] = field(default=None, repr=False)
    _library_cache: Optional[Dict[str, Any]] = field(default=None, repr=False)
    _inventory_cache: Optional[Dict[str, Any]] = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")

    async def execute_command(
        self,
        command: Dict[str, Any],
        *,
        timeout_s: float = 120.0,
        lease_id: Optional[str] = None,
    ) -> EdgeExecuteResult:
        body = command_to_execute_body(command)
        if lease_id and "lease_token" not in body:
            body["lease_token"] = lease_id
        try:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=timeout_s) as client:
                resp = await client.post("/execute", json=body)
                data = resp.json() if resp.content else {}
        except Exception as exc:  # noqa: BLE001
            return EdgeExecuteResult(
                ok=False,
                transport=self.transport,
                error=str(exc),
            )
        if not isinstance(data, dict):
            return EdgeExecuteResult(
                ok=False,
                transport=self.transport,
                error=f"edge returned non-object ({resp.status_code})",
            )
        status = str(data.get("status") or "")
        ok = status == "completed" and resp.status_code < 400
        err_obj = data.get("error") if isinstance(data.get("error"), dict) else None
        err_msg = None
        if err_obj:
            err_msg = str(err_obj.get("message") or err_obj.get("code") or err_obj)
        elif not ok:
            err_msg = f"edge status={status!r} http={resp.status_code}"
        result = data.get("result") if isinstance(data.get("result"), dict) else {}
        # Preserve top-level contract fields Twin/SDK may want.
        merged = dict(result)
        if "status" not in merged:
            merged["status"] = "ok" if ok else status or "error"
        epoch = data.get("epoch_ms")
        latch = data.get("latch_quality")
        return EdgeExecuteResult(
            ok=ok,
            transport=self.transport,
            result=merged,
            error=err_msg,
            epoch_ms=int(epoch) if isinstance(epoch, int) else None,
            latch_quality=str(latch) if latch else None,
            status=status or None,
        )

    def get_capabilities(self) -> Optional[Dict[str, Any]]:
        if self._caps_cache is not None:
            return self._caps_cache
        try:
            with httpx.Client(base_url=self.base_url, timeout=5.0) as client:
                resp = client.get("/capabilities")
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("HttpEdgeClient capabilities failed: %s", exc)
            return None
        if isinstance(data, dict):
            self._caps_cache = data
            return data
        return None

    def get_bench(self) -> Optional[Dict[str, Any]]:
        if self._bench_cache is not None:
            return self._bench_cache
        try:
            with httpx.Client(base_url=self.base_url, timeout=5.0) as client:
                resp = client.get("/bench")
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("HttpEdgeClient bench failed: %s", exc)
            return None
        if isinstance(data, dict):
            self._bench_cache = data
            return data
        return None

    def get_library(self) -> Optional[Dict[str, Any]]:
        if self._library_cache is not None:
            return self._library_cache
        try:
            with httpx.Client(base_url=self.base_url, timeout=5.0) as client:
                resp = client.get("/library")
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("HttpEdgeClient library failed: %s", exc)
            return None
        if isinstance(data, dict):
            self._library_cache = data
            return data
        return None

    def get_inventory(self) -> Optional[Dict[str, Any]]:
        if self._inventory_cache is not None:
            return self._inventory_cache
        try:
            with httpx.Client(base_url=self.base_url, timeout=5.0) as client:
                resp = client.get("/inventory")
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("HttpEdgeClient inventory failed: %s", exc)
            return None
        if isinstance(data, dict):
            self._inventory_cache = data
            return data
        return None

    def get_lab_state(self) -> Optional[Dict[str, Any]]:
        """Poll the edge's Tier-C overview snapshot (``GET /lab-state``).

        Not cached: state changes with every RECORD / move. Returns ``None`` when
        the edge does not implement the route or the poll fails.
        """
        try:
            with httpx.Client(base_url=self.base_url, timeout=5.0) as client:
                resp = client.get("/lab-state")
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("HttpEdgeClient lab-state failed: %s", exc)
            return None
        return data if isinstance(data, dict) else None

    def absolute_stream_url(self, path: str) -> Optional[str]:
        p = (path or "").strip()
        if not p:
            return None
        if p.startswith("http://") or p.startswith("https://"):
            return p
        if not p.startswith("/"):
            p = "/" + p
        return f"{self.base_url}{p}"

    def fetch_bytes(self, path: str) -> Optional[bytes]:
        """Fetch raw bytes from an edge-resident resource (on-demand image proxy).

        ``path`` may be an edge-relative path (e.g. ``/measurables/tag_22/
        camera_image.jpg``) or an absolute URL. Returns ``None`` on any failure so
        callers can fall back to a lazy reference.
        """
        url = self.absolute_stream_url(path)
        if not url:
            return None
        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.get(url)
                resp.raise_for_status()
                return resp.content
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("HttpEdgeClient fetch_bytes failed (%s): %s", path, exc)
            return None

    def absolute_ws_url(self, path: str) -> Optional[str]:
        """Return an absolute ``ws(s)://`` URL for an edge WebSocket ``path``.

        Mirrors :meth:`absolute_stream_url` but maps the HTTP scheme to the
        WebSocket scheme (``http`` -> ``ws``, ``https`` -> ``wss``) so the
        coordinator can open an outbound socket to the edge (e.g. teleop proxy).
        """
        url = self.absolute_stream_url(path)
        if not url:
            return None
        if url.startswith("https://"):
            return "wss://" + url[len("https://") :]
        if url.startswith("http://"):
            return "ws://" + url[len("http://") :]
        return url

    def teleop_ws_url(self) -> Optional[str]:
        """Resolve the edge's teleop WebSocket URL from advertised capabilities.

        Looks for a ``telemetry_channels`` entry with ``transport == "websocket"``
        (falling back to a channel literally named ``teleop``) and returns its
        absolute ``ws(s)://`` URL, or ``None`` when the edge advertises no teleop
        socket.
        """
        caps = self.get_capabilities() or {}
        channels = caps.get("telemetry_channels")
        if not isinstance(channels, dict):
            return None
        chan = channels.get("teleop")
        if not isinstance(chan, dict):
            for value in channels.values():
                if isinstance(value, dict) and value.get("transport") == "websocket":
                    chan = value
                    break
        if not isinstance(chan, dict):
            return None
        path = chan.get("path")
        if not isinstance(path, str) or not path:
            return None
        return self.absolute_ws_url(path)

    def invalidate_cache(self) -> None:
        self._caps_cache = None
        self._bench_cache = None
        self._library_cache = None
        self._inventory_cache = None


def resolve_edge_client(
    backend_id: str,
    *,
    lab: Any = None,
    edge_config: Optional[EdgeEndpointConfig] = None,
) -> EdgeClient:
    """Pick the southbound client for ``backend_id`` (poll > HTTP > in-process)."""
    bid = (backend_id or "").strip()
    if bid and edge_agent_registry.is_attached(bid):
        return PollEdgeClient(backend_id=bid)

    cfg = edge_config or EdgeEndpointConfig()
    base = cfg.normalized_base_url()
    if base:
        return HttpEdgeClient(
            base_url=base,
            contract_version=cfg.contract_version or CONTRACT_VERSION,
            lan_direct_ok=cfg.lan_direct_ok,
        )

    return InProcessEdgeClient(lab=lab)


def edge_config_for_backend(backend_registry: Any, backend_id: str) -> EdgeEndpointConfig:
    try:
        rt = backend_registry.get_runtime(backend_id, init=False)
        return getattr(rt.spec, "edge", None) or EdgeEndpointConfig()
    except Exception:
        return EdgeEndpointConfig()


async def southbound_execute(
    backend_id: str,
    command: Dict[str, Any],
    *,
    lab: Any = None,
    backend_registry: Any = None,
    timeout_s: float = 120.0,
    lease_id: Optional[str] = None,
) -> EdgeExecuteResult:
    """Convenience: resolve + execute for the active backend."""
    cfg = (
        edge_config_for_backend(backend_registry, backend_id)
        if backend_registry is not None
        else EdgeEndpointConfig()
    )
    client = resolve_edge_client(backend_id, lab=lab, edge_config=cfg)
    return await client.execute_command(
        command, timeout_s=timeout_s, lease_id=lease_id
    )


__all__ = [
    "CONTRACT_VERSION",
    "EdgeClient",
    "EdgeExecuteResult",
    "EdgeTransport",
    "HttpEdgeClient",
    "InProcessEdgeClient",
    "PollEdgeClient",
    "command_to_execute_body",
    "edge_config_for_backend",
    "resolve_edge_client",
    "southbound_execute",
]
