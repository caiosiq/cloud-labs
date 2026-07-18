#!/usr/bin/env python3
"""Mock edge agent â€” second process that owns the lab for the coordinator.

Step B / B.1: FastAPI stays the control plane (leases, queue, catalog); this
process owns MockLabCommunicator and executes:

- closed_loop + compiled_dag jobs (``GET /api/edge/work``)
- imperative primitives + kernel eval (``GET /api/edge/commands``)

Terminal 1 (coordinator)::

    $env:PYTHONPATH="backend"; python backend/main.py

Terminal 2 (edge â€” start BEFORE submitting work)::

    $env:PYTHONPATH="backend"; python scripts/ops/mock_edge_agent.py

Terminal 3 (client)::

    pip install -e ./packages/cloudlabs
    python scripts/language/03_closed_loop_catalog.py

``GET /api/backends`` shows ``edge_attached: true`` while this agent heartbeats.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

import requests

_REPO = Path(__file__).resolve().parents[2]
_BACKEND = _REPO / "backend"
_MOCK_SRC = _REPO / "mock_edge" / "src"
for _p in (_BACKEND, _MOCK_SRC):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

_LOG = logging.getLogger("mock_edge_agent")


def _post(base: str, path: str, body: Dict[str, Any]) -> Dict[str, Any]:
    resp = requests.post(f"{base}{path}", json=body, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _get(base: str, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    resp = requests.get(f"{base}{path}", params=params or {}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _bootstrap_mock_lab() -> Any:
    """Prefer ``python -m mock_edge``; this agent remains for poll-attach jobs."""
    from lab_model.coordinator.backends.lab_view_config import bootstrap_lab_view, get_lab_view_paths
    from mock_edge.host.communicator import MockLabCommunicator

    mock_view = _REPO / "mock_edge" / "lab_view"
    os.environ.setdefault("LAB_VIEW_PATH", str(mock_view))
    bootstrap_lab_view(str(_REPO))
    from lab_model.language.domain import motor_rotation_store as motor_rot

    motor_rot.configure(get_lab_view_paths().motor_rotations_json)
    return MockLabCommunicator()


def _complete_command(
    base: str,
    command_id: str,
    *,
    result: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
) -> None:
    _post(
        base,
        f"/api/edge/commands/{command_id}/complete",
        {"result": result, "error": error},
    )


async def _handle_edge_command(
    *,
    base_url: str,
    backend_id: str,
    command: Dict[str, Any],
    lab: Any,
) -> None:
    from lab_model.language.primitives import (
        EvalKernelBody,
        PrimitiveId,
        RecordMeasurablesBody,
        execute_validated_command,
        fetch_read_primitive,
        parse_command_payload,
    )

    command_id = str(command.get("command_id") or "")
    kind = str(command.get("kind") or "")
    payload = command.get("payload") if isinstance(command.get("payload"), dict) else {}

    try:
        # Compat: rewrite legacy kernel_eval queue kind â†’ EVAL_KERNEL primitive.
        if kind == "kernel_eval":
            kernel_id = str(payload.get("kernel_id") or "").strip()
            tag_id = str(payload.get("tag_id") or "").strip()
            lease_id = str(payload.get("lease_id") or "").strip()
            if not kernel_id or not tag_id:
                raise ValueError("kernel_id and tag_id required")
            kind = "primitive"
            payload = {
                "command": {
                    "action": "EVAL_KERNEL",
                    "target_id": tag_id,
                    "parameters": {
                        "kernel_id": kernel_id,
                        "field": "camera_image",
                        "lease_id": lease_id or None,
                    },
                    "lease_id": lease_id,
                }
            }

        if kind == "primitive":
            raw = payload.get("command")
            if not isinstance(raw, dict):
                raise ValueError("primitive payload.command must be an object")
            state = lab.get_lab_state() if hasattr(lab, "get_lab_state") else {}
            status = (state or {}).get("system_status")
            if status in ("BUSY", "OPTIMIZING"):
                raise RuntimeError(f"System is {status}. Please wait.")
            cmd = parse_command_payload(raw)
            lease_id = str(raw.get("lease_id") or "").strip()
            lab._command_lease_id = lease_id or None
            lab._command_backend_id = backend_id
            result = await execute_validated_command(lab, cmd)
            if isinstance(cmd, RecordMeasurablesBody):
                meas = fetch_read_primitive(
                    lab,
                    PrimitiveId.GET_MEASURABLES,
                    cmd.target_id,
                )
                _complete_command(
                    base_url,
                    command_id,
                    result={"status": "ok", "measurables": meas},
                )
            elif isinstance(cmd, EvalKernelBody):
                if not isinstance(result, dict):
                    raise RuntimeError("EVAL_KERNEL returned no result")
                _complete_command(
                    base_url,
                    command_id,
                    result={"status": "ok", **result},
                )
            else:
                _complete_command(
                    base_url,
                    command_id,
                    result={
                        "status": "ok",
                        "message": f"{cmd.action} completed on edge for {cmd.target_id}",
                        "action": cmd.action,
                        "target_id": cmd.target_id,
                    },
                )
            return

        if kind == "get_lab_state":
            state = lab.get_lab_state() if hasattr(lab, "get_lab_state") else {}
            _complete_command(
                base_url,
                command_id,
                result={"lab_state": state if isinstance(state, dict) else {}},
            )
            return

        raise ValueError(f"unsupported edge command kind {kind!r}")
    except Exception as exc:  # noqa: BLE001
        _LOG.exception("edge command %s failed", command_id)
        try:
            _complete_command(base_url, command_id, error=str(exc))
        except Exception:
            pass


class _ProgressProxy:
    def __init__(self, base_url: str, job_id: str) -> None:
        self._base = base_url
        self._job_id = job_id
        self._cancel = False

    def is_cancel_requested(self, _jid: str) -> bool:
        return self._cancel

    def update_progress(self, _jid: str, patch: Dict[str, Any]) -> None:
        try:
            _post(
                self._base,
                f"/api/edge/jobs/{self._job_id}/progress",
                {"progress": patch},
            )
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("progress push failed: %s", exc)


async def _run_closed_loop_job(
    *,
    base_url: str,
    backend_id: str,
    job: Dict[str, Any],
    lab: Any,
) -> None:
    from lab_model.coordinator.jobs.runner import _collect_result, _run_closed_loop
    from lab_model.execution.optimization.kernels import session_store

    job_id = str(job["job_id"])
    spec = dict(job.get("spec") or {})

    try:
        session_store.activate_job_roots(backend_id, job_id)
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("kernel root activate failed: %s", exc)

    lease = _post(
        base_url,
        "/api/jobs/lease/acquire",
        {
            "backend_id": backend_id,
            "holder": f"edge:{job_id}",
            "mode": "closed_loop",
        },
    )
    lease_id = str(lease.get("lease_id") or "")
    proxy = _ProgressProxy(base_url, job_id)
    status = "succeeded"
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    try:
        await _run_closed_loop(job_id, lab, spec, proxy)  # type: ignore[arg-type]
        result = _collect_result(lab, "closed_loop", spec)
        packages = spec.get("kernel_packages")
        if isinstance(packages, list) and packages:
            result = dict(result or {})
            result["kernel_audit"] = packages
    except Exception as exc:  # noqa: BLE001
        _LOG.exception("edge job %s failed", job_id)
        status = "failed"
        error = str(exc)
    finally:
        try:
            _post(
                base_url,
                f"/api/edge/jobs/{job_id}/complete",
                {"status": status, "result": result, "error": error},
            )
        except Exception as exc:  # noqa: BLE001
            _LOG.error("complete push failed: %s", exc)
        if lease_id:
            try:
                _post(base_url, "/api/jobs/lease/release", {"lease_id": lease_id})
            except Exception as exc:  # noqa: BLE001
                _LOG.warning("lease release failed: %s", exc)
        try:
            session_store.delete_job_scratch(backend_id, job_id)
            session_store.clear_extra_kernels_roots()
        except Exception:
            pass


async def _run_compiled_dag_job(
    *,
    base_url: str,
    backend_id: str,
    job: Dict[str, Any],
    lab: Any,
) -> None:
    from lab_model.coordinator.jobs.runner import _collect_result, _run_compiled_dag
    from lab_model.execution.optimization.kernels import session_store

    job_id = str(job["job_id"])
    spec = dict(job.get("spec") or {})

    try:
        session_store.activate_job_roots(backend_id, job_id)
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("kernel root activate failed: %s", exc)

    lease = _post(
        base_url,
        "/api/jobs/lease/acquire",
        {
            "backend_id": backend_id,
            "holder": f"edge:{job_id}",
            "mode": "compiled_dag",
        },
    )
    lease_id = str(lease.get("lease_id") or "")
    proxy = _ProgressProxy(base_url, job_id)
    status = "succeeded"
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    try:
        await _run_compiled_dag(
            job_id,
            lab,
            spec,
            proxy,  # type: ignore[arg-type]
            control_dir=None,
        )
        result = _collect_result(lab, "compiled_dag", spec)
    except Exception as exc:  # noqa: BLE001
        _LOG.exception("edge compiled_dag job %s failed", job_id)
        status = "failed"
        error = str(exc)
    finally:
        try:
            _post(
                base_url,
                f"/api/edge/jobs/{job_id}/complete",
                {"status": status, "result": result, "error": error},
            )
        except Exception as exc:  # noqa: BLE001
            _LOG.error("complete push failed: %s", exc)
        if lease_id:
            try:
                _post(base_url, "/api/jobs/lease/release", {"lease_id": lease_id})
            except Exception as exc:  # noqa: BLE001
                _LOG.warning("lease release failed: %s", exc)
        try:
            session_store.delete_job_scratch(backend_id, job_id)
            session_store.clear_extra_kernels_roots()
        except Exception:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Cloud Labs mock edge agent (Step B.1)")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--backend-id", default="mock.default")
    parser.add_argument("--poll-s", type=float, default=0.25)
    parser.add_argument(
        "--heartbeat-s",
        type=float,
        default=1.5,
        help="Must be well under coordinator stale window (default 5s)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    base = args.base_url.rstrip("/")
    backend_id = args.backend_id.strip()

    # Boot local communicator BEFORE registering so a failed boot does not
    # leave the coordinator believing an edge is attached.
    lab = _bootstrap_mock_lab()
    _LOG.info("local MockLabCommunicator ready")

    reg = _post(
        base,
        "/api/edge/register",
        {"backend_id": backend_id, "label": "mock-edge-agent"},
    )
    agent_id = str((reg.get("agent") or {}).get("agent_id") or "")
    if not agent_id:
        _LOG.error("register did not return agent_id: %s", reg)
        return 1
    _LOG.info("registered agent_id=%s backend=%s", agent_id, backend_id)

    last_hb = 0.0
    try:
        while True:
            now = time.monotonic()
            if now - last_hb >= args.heartbeat_s:
                state = lab.get_lab_state() if hasattr(lab, "get_lab_state") else {}
                _post(
                    base,
                    "/api/edge/heartbeat",
                    {
                        "agent_id": agent_id,
                        "lab_state": state if isinstance(state, dict) else {},
                    },
                )
                last_hb = now

            # Prefer short-lived command work over claiming a long job.
            cmds = _get(
                base,
                "/api/edge/commands",
                {"backend_id": backend_id, "agent_id": agent_id},
            )
            command = cmds.get("command")
            if isinstance(command, dict) and command.get("command_id"):
                _LOG.info(
                    "command %s kind=%s",
                    command.get("command_id"),
                    command.get("kind"),
                )
                asyncio.run(
                    _handle_edge_command(
                        base_url=base,
                        backend_id=backend_id,
                        command=command,
                        lab=lab,
                    )
                )
                continue

            work = _get(
                base,
                "/api/edge/work",
                {"backend_id": backend_id, "agent_id": agent_id},
            )
            job = work.get("job")
            if isinstance(job, dict) and job.get("job_id"):
                mode = str(job.get("mode") or "")
                _LOG.info("claimed job %s mode=%s", job.get("job_id"), mode)
                if mode == "closed_loop":
                    asyncio.run(
                        _run_closed_loop_job(
                            base_url=base,
                            backend_id=backend_id,
                            job=job,
                            lab=lab,
                        )
                    )
                    _LOG.info("job %s finished", job.get("job_id"))
                elif mode == "compiled_dag":
                    asyncio.run(
                        _run_compiled_dag_job(
                            base_url=base,
                            backend_id=backend_id,
                            job=job,
                            lab=lab,
                        )
                    )
                    _LOG.info("job %s finished", job.get("job_id"))
                else:
                    _post(
                        base,
                        f"/api/edge/jobs/{job['job_id']}/complete",
                        {
                            "status": "failed",
                            "error": (
                                "mock edge agent supports closed_loop and "
                                f"compiled_dag (got {mode!r})"
                            ),
                        },
                    )
            else:
                time.sleep(args.poll_s)
    except KeyboardInterrupt:
        _LOG.info("shutting down")
        try:
            _post(base, "/api/edge/unregister", {"agent_id": agent_id})
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
