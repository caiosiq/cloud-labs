"""HTTP-edge OptimizeHost: POST OPTIMIZE → SSE job stream → progress_callback.

Used when a backend has ``edge.base_url`` configured (no in-process mock host).
Mirrors the mock communicator's ensemble hook, but the session runs on the edge
process and telemetry rides ``GET /jobs/{id}/stream``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from typing import Any, AsyncIterator, Callable, Dict, Mapping, Optional

import httpx

from lab_model.execution.edge.client import CONTRACT_VERSION
from lab_model.execution.orchestration.optimize import run_optimize_component

_LOG = logging.getLogger(__name__)

# How long a fetched edge /lab-state may be reused for init gates (seconds).
_EDGE_GATE_CACHE_TTL_S = 2.0


class EdgeJobStreamError(RuntimeError):
    """Edge job stream dropped or returned a non-success final status."""


def parse_sse_blocks(buffer: str) -> tuple[list[str], str]:
    """Split an SSE byte/text buffer into complete ``data:`` JSON payloads."""
    events: list[str] = []
    while "\n\n" in buffer:
        block, buffer = buffer.split("\n\n", 1)
        data_lines: list[str] = []
        for line in block.splitlines():
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
        if data_lines:
            events.append("\n".join(data_lines))
    return events, buffer


async def iter_edge_job_events(
    base_url: str,
    job_id: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
    timeout_s: float = 3600.0,
) -> AsyncIterator[Dict[str, Any]]:
    """Yield parsed JSON objects from ``GET {base}/jobs/{job_id}/stream``."""
    url = f"{base_url.rstrip('/')}/jobs/{job_id}/stream"
    own = client is None
    http = client or httpx.AsyncClient(timeout=timeout_s)
    try:
        async with http.stream("GET", url) as resp:
            resp.raise_for_status()
            buf = ""
            async for chunk in resp.aiter_text():
                buf += chunk
                payloads, buf = parse_sse_blocks(buf)
                for raw in payloads:
                    try:
                        event = json.loads(raw)
                    except json.JSONDecodeError:
                        _LOG.warning("edge job SSE non-JSON payload: %r", raw[:200])
                        continue
                    if isinstance(event, dict):
                        yield event
    finally:
        if own:
            await http.aclose()


class HttpEdgeEnsembleHost:
    """Minimal OptimizeHost backed by Edge Contract HTTP + Twin lab-state store."""

    def __init__(
        self,
        *,
        backend_id: str,
        base_url: str,
        state_store: Any,
        contract_version: str = CONTRACT_VERSION,
        catalog_map: Optional[Mapping[str, Any]] = None,
        stream_iter: Optional[Callable[..., AsyncIterator[Dict[str, Any]]]] = None,
        execute_fn: Optional[Callable[..., Any]] = None,
        edge_state_fetch: Optional[Callable[[], Optional[Dict[str, Any]]]] = None,
    ) -> None:
        self.backend_id = str(backend_id or "").strip()
        self.base_url = str(base_url or "").rstrip("/")
        self.contract_version = contract_version or CONTRACT_VERSION
        self._store = state_store
        self._state_lock = threading.RLock()
        self.log_prefix = f"[edge-ensemble {self.backend_id}]"
        self.catalog_map: Dict[str, Any] = {
            str(k): dict(v) for k, v in (catalog_map or {}).items() if isinstance(v, Mapping)
        }
        self._stream_iter = stream_iter or iter_edge_job_events
        self._execute_fn = execute_fn
        self._job_abort_check: Optional[Callable[[], bool]] = None
        self._edge_state_fetch = edge_state_fetch
        self._edge_gate_cache: Optional[Dict[str, Any]] = None
        self._edge_gate_cache_at: float = 0.0

    # -- StateHost -----------------------------------------------------------

    @property
    def current_state(self) -> Dict[str, Any]:
        return self._store.runtime.state

    def _persist_state(self) -> None:
        persist = getattr(self._store, "persist", None)
        if callable(persist):
            persist()

    def _set_status(self, status: str, *, persist: bool = True) -> None:
        with self._state_lock:
            self.current_state["system_status"] = str(status or "IDLE").upper()
        if persist:
            self._persist_state()

    def _catalog_meta_for_tag(self, tag_id: str) -> Optional[Dict[str, Any]]:
        tid = str(tag_id)
        if tid in self.catalog_map:
            return self.catalog_map[tid]
        comps = self.current_state.get("components")
        if isinstance(comps, dict) and tid in comps and isinstance(comps[tid], dict):
            return comps[tid]
        return None

    def _fetch_edge_lab_state(self) -> Optional[Dict[str, Any]]:
        """Live edge ``/lab-state`` for init gates (runtime_sync is edge-owned)."""
        now = time.monotonic()
        if (
            isinstance(self._edge_gate_cache, dict)
            and (now - self._edge_gate_cache_at) < _EDGE_GATE_CACHE_TTL_S
        ):
            return self._edge_gate_cache
        edge: Optional[Dict[str, Any]] = None
        if callable(self._edge_state_fetch):
            try:
                got = self._edge_state_fetch()
                if isinstance(got, dict):
                    edge = got
            except Exception as exc:  # noqa: BLE001
                _LOG.warning(
                    "%s edge_state_fetch failed: %s", self.log_prefix, exc
                )
        elif self.base_url:
            try:
                with httpx.Client(base_url=self.base_url, timeout=5.0) as client:
                    resp = client.get("/lab-state")
                    resp.raise_for_status()
                    payload = resp.json()
                if isinstance(payload, dict):
                    edge = payload
            except Exception as exc:  # noqa: BLE001
                _LOG.warning(
                    "%s edge /lab-state for gate failed: %s", self.log_prefix, exc
                )
        if isinstance(edge, dict):
            self._edge_gate_cache = edge
            self._edge_gate_cache_at = now
        return edge

    def get_lab_state(self) -> Dict[str, Any]:
        """Working Twin store + edge ``runtime_sync`` (same merge Twin polls use).

        Coordinator disk/working copy intentionally omits ``runtime_sync``; job
        runners that gate on ``get_lab_state`` must still see edge READY.
        """
        snap = getattr(self._store, "snapshot", None)
        working = snap() if callable(snap) else dict(self.current_state)
        if not isinstance(working, dict):
            working = {}
        edge = self._fetch_edge_lab_state()
        if isinstance(edge, dict):
            from lab_model.coordinator.state.merge_lab_state import (
                merge_lab_state_for_twin,
            )

            out = merge_lab_state_for_twin(
                working, edge, backend_id=self.backend_id
            )
        else:
            out = dict(working)
        out["active_backend_id"] = self.backend_id
        out.setdefault("edge_attached", True)
        return out

    def supports_primitive(self, action: str) -> bool:
        return str(action or "").upper() in {"OPTIMIZE"}

    # -- OptimizeHost --------------------------------------------------------

    async def optimize_component(
        self, target_id: str, strategy_name: str, params: Dict[str, Any]
    ) -> None:
        await run_optimize_component(self, target_id, strategy_name, params)

    def _primitive_prepare_optimization_run(
        self, target_id: str, strategy_name: str
    ) -> Optional[str]:
        _ = (target_id, strategy_name)
        return None

    def _primitive_finalize_optimization_run(self) -> None:
        return None

    async def _primitive_optimize_component(
        self,
        *,
        target_id: str,
        strategy_name: str,
        params: Dict[str, Any],
        progress_callback: Any,
    ) -> Optional[Dict[str, Any]]:
        # Legacy single-tag strategies are not hosted over HTTP ensemble path.
        _ = (target_id, strategy_name, params, progress_callback)
        raise NotImplementedError(
            "legacy OPTIMIZE over HTTP edge is not supported; use mode=ensemble + pipeline"
        )

    async def _primitive_run_ensemble_optimization(
        self,
        *,
        spec: Any,
        x0: Dict[str, float],
        session_id: str,
        progress_callback: Any,
        should_abort: Any = None,
        should_accept: Any = None,
    ) -> Optional[Dict[str, Any]]:
        """POST OPTIMIZE to the edge, subscribe to the job stream, forward evals."""
        abort = should_abort or self._job_abort_check
        accept = should_accept or getattr(self, "_job_accept_check", None)
        params = self._build_optimize_args(spec=spec, x0=x0, session_id=session_id)
        accept_resp = await self._post_optimize(params)
        job_id = str(accept_resp.get("job_id") or "").strip()
        if not job_id:
            raise EdgeJobStreamError(
                f"edge OPTIMIZE did not return job_id (got {accept_resp!r})"
            )

        final: Optional[Dict[str, Any]] = None
        accept_posted = False
        try:
            async for event in self._stream_iter(self.base_url, job_id):
                if (
                    not accept_posted
                    and accept is not None
                    and callable(accept)
                    and accept()
                ):
                    accept_posted = True
                    await self._post_job_signal(job_id, "accept")
                    _LOG.info("posted edge job accept job_id=%s", job_id)
                if abort is not None and callable(abort) and abort():
                    await self._post_job_signal(job_id, "cancel")
                    raise EdgeJobStreamError("optimization aborted by coordinator")
                kind = str(event.get("event") or "")
                if kind == "progress":
                    self._forward_progress(event, progress_callback)
                elif kind in ("final", "snapshot") and event.get("status") in (
                    "succeeded",
                    "failed",
                    "cancelled",
                ):
                    final = event
                    if kind == "final":
                        break
                elif kind == "final":
                    final = event
                    break
        except EdgeJobStreamError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise EdgeJobStreamError(
                f"edge job stream dropped for {job_id}: {exc}"
            ) from exc

        if final is None:
            raise EdgeJobStreamError(f"edge job {job_id} stream ended without final event")

        status = str(final.get("status") or "")
        if status != "succeeded":
            err = final.get("error") if isinstance(final.get("error"), dict) else {}
            msg = err.get("message") if isinstance(err, dict) else final.get("message")
            raise EdgeJobStreamError(
                f"edge job {job_id} ended status={status!r}: {msg or final}"
            )

        result = final.get("result") if isinstance(final.get("result"), dict) else {}
        out = {
            "session_id": result.get("session_id") or session_id,
            "best_loss": float(result.get("best_loss") if result.get("best_loss") is not None else float("inf")),
            "final_values": dict(result.get("final_values") or {}),
            "evals": int(result.get("evals") or 0),
            "trace": list(result.get("trace") or [])[-200:],
            "aborted": bool(result.get("aborted")),
            "early_stopped": bool(result.get("early_stopped")),
            "early_stop_reason": result.get("early_stop_reason"),
        }
        if not out["final_values"]:
            # Still allow commit path to skip rather than invent values.
            return out
        return out

    async def _post_job_signal(self, job_id: str, action: str) -> None:
        """Best-effort POST ``/jobs/{id}/accept`` or ``/cancel`` on the edge."""
        path = f"/jobs/{job_id}/{action}"
        try:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=10.0) as client:
                await client.post(path)
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("edge %s failed for %s: %s", action, job_id, exc)

    def _build_optimize_args(
        self,
        *,
        spec: Any,
        x0: Dict[str, float],
        session_id: str,
    ) -> Dict[str, Any]:
        pipeline = None
        if hasattr(spec, "model_dump"):
            # Prefer already-compiled pipeline on the host params path via attach_pipeline.
            pass
        # Reconstruct from state optimization session if needed — callers pass
        # OptimizeEnsembleParameters; pipeline lives on mutable_params before strip.
        # We stash pipeline on the host when dispatch attaches it.
        pipeline = getattr(self, "_pending_pipeline", None)
        packages = getattr(self, "_pending_kernel_packages", None)
        telemetry = getattr(self, "_pending_telemetry", None)
        tag_id = ""
        try:
            if getattr(spec, "variables", None):
                tag_id = str(spec.variables[0].tag_id)
        except Exception:  # noqa: BLE001
            tag_id = ""
        args: Dict[str, Any] = {
            "mode": "ensemble",
            "tag_id": tag_id,
            "x0": {str(k): float(v) for k, v in (x0 or {}).items()},
            "session_id": session_id,
        }
        if isinstance(pipeline, dict):
            args["pipeline"] = pipeline
        elif pipeline is not None and hasattr(pipeline, "model_dump"):
            args["pipeline"] = pipeline.model_dump()
        if isinstance(packages, list) and packages:
            args["kernel_packages"] = list(packages)
        if isinstance(telemetry, Mapping):
            every_n = telemetry.get("camera_every_n")
            if every_n is not None:
                try:
                    args["telemetry_camera_every_n"] = max(1, int(every_n))
                except (TypeError, ValueError):
                    args["telemetry_camera_every_n"] = 5
            q = telemetry.get("camera_jpeg_quality")
            if q is not None:
                try:
                    args["telemetry_camera_jpeg_quality"] = max(1, min(95, int(q)))
                except (TypeError, ValueError):
                    pass
            sc = telemetry.get("camera_jpeg_scale")
            if sc is not None:
                try:
                    args["telemetry_camera_jpeg_scale"] = float(sc)
                except (TypeError, ValueError):
                    pass
        return args

    async def _post_optimize(self, args: Dict[str, Any]) -> Dict[str, Any]:
        if self._execute_fn is not None:
            out = self._execute_fn(args)
            if asyncio.iscoroutine(out):
                out = await out
            if not isinstance(out, dict):
                raise EdgeJobStreamError(f"execute_fn returned non-dict: {out!r}")
            return out

        body = {"primitive": "OPTIMIZE", "args": args}
        async with httpx.AsyncClient(base_url=self.base_url, timeout=60.0) as client:
            resp = await client.post("/execute", json=body)
            data = resp.json() if resp.content else {}
        if not isinstance(data, dict):
            raise EdgeJobStreamError(f"edge OPTIMIZE non-object response http={resp.status_code}")
        status = str(data.get("status") or "")
        if status != "accepted":
            err = data.get("error") if isinstance(data.get("error"), dict) else {}
            msg = err.get("message") if isinstance(err, dict) else data.get("message")
            raise EdgeJobStreamError(
                f"edge OPTIMIZE refused status={status!r} http={resp.status_code}: {msg}"
            )
        # Contract: accepted may nest job under result or top-level job_id.
        job_id = data.get("job_id")
        if not job_id and isinstance(data.get("result"), dict):
            job_id = data["result"].get("job_id")
        return {"job_id": job_id, **data}

    @staticmethod
    def _forward_progress(event: Mapping[str, Any], progress_callback: Any) -> None:
        if progress_callback is None:
            return
        step = event.get("eval")
        if step is None:
            step = event.get("step")
        kwargs: Dict[str, Any] = {}
        for key in (
            "loss",
            "best_loss",
            "terms",
            "block_id",
            "u",
            "values",
            "camera_image",
            "stages",
            "refused",
            "early_stop",
            "stop_loss",
            "debug_capture",
            "debug_kernel",
            "debug_actuate",
            "debug_policy",
            "debug_presence",
            "debug_scales",
            "debug_telemetry",
        ):
            if key in event:
                kwargs[key] = event[key]
        try:
            if step is not None:
                progress_callback(step=int(step), **kwargs)
            else:
                progress_callback(**kwargs)
        except TypeError:
            # Some callbacks are positional-only wrappers.
            progress_callback(step=int(step or 0), **kwargs)


def bind_pending_pipeline(host: Any, params: Mapping[str, Any]) -> None:
    """Stash pipeline / kernel_packages / telemetry from OPTIMIZE params onto the HTTP host."""
    if not isinstance(host, HttpEdgeEnsembleHost):
        return
    pipe = params.get("pipeline")
    if isinstance(pipe, dict):
        host._pending_pipeline = dict(pipe)
    pkgs = params.get("kernel_packages")
    if isinstance(pkgs, list):
        host._pending_kernel_packages = list(pkgs)
    tel = params.get("telemetry")
    if isinstance(tel, Mapping):
        host._pending_telemetry = dict(tel)
    elif tel is not None and hasattr(tel, "model_dump"):
        host._pending_telemetry = tel.model_dump()


__all__ = [
    "EdgeJobStreamError",
    "HttpEdgeEnsembleHost",
    "bind_pending_pipeline",
    "iter_edge_job_events",
    "parse_sse_blocks",
]
