"""Job submit/monitor helpers for the CloudLabs SDK."""
from __future__ import annotations

import time
from typing import Any, Dict, List, Mapping, Optional

from .client import CloudLabsClient
from .exceptions import CloudLabsTimeoutError

_TERMINAL = frozenset({"succeeded", "failed", "cancelled"})


def submit_job(
    client: CloudLabsClient,
    *,
    mode: str,
    command: Optional[Mapping[str, Any]] = None,
    steps: Optional[List[Mapping[str, Any]]] = None,
    snapshot: Optional[Mapping[str, Any]] = None,
    holder: Optional[str] = None,
    finalize_checkout: Optional[Mapping[str, Any]] = None,
    kernels: Optional[List[str]] = None,
    on_success: Optional[Mapping[str, Any]] = None,
    initialization_policy: Optional[str] = None,
) -> Dict[str, Any]:
    """Submit a job via ``POST /api/jobs/submit``.

    Parameters
    ----------
    mode:
        ``closed_loop`` (OPTIMIZE ensemble) or ``compiled_dag`` (step list).
    command:
        Primitive envelope for closed-loop jobs.
    steps:
        Primitive list for compiled DAG jobs.
    """
    body: Dict[str, Any] = {
        "mode": mode,
        "backend_id": client.backend_id,
    }
    if holder:
        body["holder"] = holder
    if snapshot:
        body["snapshot"] = dict(snapshot)
    if command is not None:
        body["command"] = dict(command)
    if steps is not None:
        body["steps"] = [dict(s) for s in steps]
    if finalize_checkout is not None:
        body["finalize_checkout"] = dict(finalize_checkout)
    if kernels:
        body["kernels"] = list(kernels)
    if on_success is not None:
        body["on_success"] = dict(on_success)
    if initialization_policy:
        body["initialization_policy"] = initialization_policy
    return client._post_json("/api/jobs/submit", body, include_lease=False)


def get_job(client: CloudLabsClient, job_id: str) -> Dict[str, Any]:
    """Fetch one job record."""
    return client._get_json(f"/api/jobs/{job_id}")


def list_jobs(
    client: CloudLabsClient,
    *,
    limit: int = 50,
    status: Optional[str] = None,
) -> Dict[str, Any]:
    """List recent jobs."""
    params: Dict[str, Any] = {"limit": limit}
    if status:
        params["status"] = status
    return client._get_json("/api/jobs", params=params)


def wait_for_job(
    client: CloudLabsClient,
    job_id: str,
    *,
    poll_interval_s: float = 0.5,
    timeout_s: float = 3600.0,
) -> Dict[str, Any]:
    """Poll until the job reaches a terminal status."""
    import logging

    log = logging.getLogger("cloudlabs")
    verbose = bool(getattr(client, "verbose", True))
    deadline = time.monotonic() + timeout_s
    last: Dict[str, Any] = {}
    last_eval: Any = object()
    while time.monotonic() < deadline:
        last = get_job(client, job_id)
        status = str(last.get("status") or "")
        progress = last.get("progress") if isinstance(last.get("progress"), dict) else {}
        eval_n = progress.get("eval")
        if verbose and eval_n is not None and eval_n != last_eval:
            last_eval = eval_n
            log.info(
                "job %s eval=%s best_loss=%s",
                job_id[:18],
                eval_n,
                progress.get("best_loss"),
            )
        if status in _TERMINAL:
            if verbose:
                log.info("job %s terminal status=%s", job_id[:18], status)
            return last
        time.sleep(poll_interval_s)
    raise CloudLabsTimeoutError(
        f"Timed out after {timeout_s}s waiting for job {job_id!r}; "
        f"last status={last.get('status')!r}"
    )


def cancel_job(client: CloudLabsClient, job_id: str) -> Dict[str, Any]:
    """Request best-effort cancellation for a queued or running job."""
    return client.cancel_job(job_id)


def submit_compiled_dag(
    client: CloudLabsClient,
    steps: List[Mapping[str, Any]],
    *,
    snapshot: Optional[Mapping[str, Any]] = None,
    holder: Optional[str] = None,
    finalize_checkout: Optional[Mapping[str, Any]] = None,
    kernels: Optional[List[str]] = None,
    on_success: Optional[Mapping[str, Any]] = None,
    initialization_policy: Optional[str] = None,
) -> Dict[str, Any]:
    """Submit a compiled DAG job (primitive step list)."""
    body: Dict[str, Any] = {
        "mode": "compiled_dag",
        "backend_id": client.backend_id,
        "steps": [dict(s) for s in steps],
    }
    if holder:
        body["holder"] = holder
    if snapshot:
        body["snapshot"] = dict(snapshot)
    if finalize_checkout:
        body["finalize_checkout"] = dict(finalize_checkout)
    if kernels:
        body["kernels"] = list(kernels)
    if on_success:
        body["on_success"] = dict(on_success)
    if initialization_policy:
        body["initialization_policy"] = initialization_policy
    return client._post_json("/api/jobs/submit", body, include_lease=False)


def submit_closed_loop_optimize(
    client: CloudLabsClient,
    target_id: str,
    parameters: Mapping[str, Any],
    *,
    snapshot: Optional[Mapping[str, Any]] = None,
    holder: Optional[str] = None,
    kernels: Optional[List[str]] = None,
    on_success: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Convenience wrapper for ensemble OPTIMIZE jobs."""
    params = dict(parameters)
    params.setdefault("mode", "ensemble")
    return submit_job(
        client,
        mode="closed_loop",
        command={
            "action": "OPTIMIZE",
            "target_id": target_id,
            "parameters": params,
        },
        snapshot=snapshot,
        holder=holder,
        on_success=on_success,
        kernels=kernels,
        initialization_policy=client.initialization_policy,
    )
