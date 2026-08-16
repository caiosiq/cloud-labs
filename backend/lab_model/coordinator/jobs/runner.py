"""Background execution for submitted jobs (Phase C)."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Dict, Optional

from lab_model.coordinator.jobs.checkout_finalize import apply_checkout_finalize
from lab_model.coordinator.jobs.init_reconcile import run_init_reconcile_if_needed
from lab_model.coordinator.jobs.post_job_commit import apply_post_job_commit_if_needed
from lab_model.coordinator.jobs.lease_manager import (
    LeaseConflictError,
    LeaseExpiredError,
    LeaseNotFoundError,
    SessionLeaseManager,
)
from lab_model.coordinator.jobs.job_manager import JobManager
from lab_model.language.primitives.dispatch import execute_validated_command, parse_command_payload
from lab_model.language.primitives.schemas import OptimizeBody
from pydantic import ValidationError

_LOG = logging.getLogger(__name__)


async def run_job(
    job_id: str,
    *,
    lab: Any,
    runtime_manager: Any,
    lease_manager: SessionLeaseManager,
    job_manager: JobManager,
    backend_id: str,
    control_dir: Optional[str] = None,
    pins_lookup: Optional[Any] = None,
    repo_owns_bench: Optional[Any] = None,
    command_matrix: Any = None,
) -> None:
    """Execute one queued job: acquire lease, run spec, release lease."""
    try:
        record = job_manager.get(job_id)
    except KeyError:
        return

    lease_id: Optional[str] = None
    telemetry_task: Optional[asyncio.Task[None]] = None
    runtime_token: Optional[str] = None

    try:
        lease = lease_manager.acquire(
            backend_id=backend_id,
            holder=record.holder,
            mode=record.mode,  # type: ignore[arg-type]
            snapshot_ref=record.snapshot_ref,
        )
        lease_id = lease.lease_id
        job_manager.mark_running(job_id, lease_id=lease_id)

        # Activate job-staged session kernels for TorchScript resolution.
        try:
            from lab_model.execution.optimization.kernels import session_store

            session_store.activate_job_roots(backend_id, job_id)
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("job %s kernel root activate failed: %s", job_id, exc)

        target_lab = lab
        if runtime_manager is not None:
            target_lab, runtime_token = runtime_manager.reserve_operation()

        telemetry_task = asyncio.create_task(
            _telemetry_loop(job_id, lab=lab, job_manager=job_manager),
            name=f"job-telemetry-{job_id}",
        )

        init_summary = await run_init_reconcile_if_needed(
            job_id,
            lab=target_lab,
            spec=record.spec,
            control_dir=control_dir or "",
            job_manager=job_manager,
            pins_lookup=pins_lookup,
        )
        if init_summary:
            job_manager.update_progress(job_id, {"init": init_summary})

        if record.mode == "closed_loop":
            await _run_closed_loop(
                job_id,
                target_lab,
                record.spec,
                job_manager,
                backend_id=backend_id,
                lease_id=lease_id,
                command_matrix=command_matrix,
            )
        elif record.mode == "compiled_dag":
            await _run_compiled_dag(
                job_id,
                target_lab,
                record.spec,
                job_manager,
                runtime_manager=runtime_manager,
                control_dir=control_dir,
            )
        else:
            raise ValueError(f"unsupported mode {record.mode!r}")

        result = _collect_result(lab, record.mode, record.spec)
        latest = job_manager.get(job_id)
        fin = (latest.progress or {}).get("finalize")
        if isinstance(fin, dict):
            result["finalize"] = fin
        # Persist kernel audit digests on the job result for later inspection.
        packages = (latest.spec or {}).get("kernel_packages")
        if isinstance(packages, list) and packages:
            result["kernel_audit"] = packages

        if not job_manager.is_cancel_requested(job_id):
            try:
                post_commit = await apply_post_job_commit_if_needed(
                    job_id=job_id,
                    spec=record.spec,
                    control_dir=control_dir or "",
                    lab=lab,
                    runtime_manager=runtime_manager,
                    repo_owns_bench=repo_owns_bench,
                )
                if post_commit:
                    result["post_commit"] = post_commit
                    job_manager.update_progress(
                        job_id,
                        {
                            "phase": "post_commit",
                            "message": (
                                f"Post-job commit {post_commit.get('configuration_id', '')[:8]}…"
                            ),
                        },
                    )
            except Exception as exc:  # noqa: BLE001
                _LOG.warning("job %s post-commit skipped: %s", job_id, exc)
                result["post_commit_error"] = str(exc)

        if job_manager.is_cancel_requested(job_id):
            job_manager.complete(
                job_id,
                status="cancelled",
                result=result,
                error="Cancelled during execution (best-effort)",
            )
        else:
            job_manager.complete(job_id, status="succeeded", result=result)

    except asyncio.CancelledError:
        job_manager.complete(job_id, status="cancelled", error="Runner task cancelled")
        raise
    except (LeaseConflictError, LeaseNotFoundError, LeaseExpiredError) as exc:
        job_manager.complete(job_id, status="failed", error=str(exc))
    except Exception as exc:  # noqa: BLE001
        _LOG.exception("job %s failed: %s", job_id, exc)
        job_manager.complete(job_id, status="failed", error=str(exc))
    finally:
        if telemetry_task is not None:
            telemetry_task.cancel()
            try:
                await telemetry_task
            except asyncio.CancelledError:
                pass
        if lease_id:
            lease_manager.release(lease_id)
        try:
            from lab_model.execution.optimization.kernels import session_store

            session_store.delete_job_scratch(backend_id, job_id)
            session_store.clear_extra_kernels_roots()
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("job %s kernel scratch cleanup failed: %s", job_id, exc)
        if runtime_manager is not None and runtime_token is not None:
            runtime_manager.release_operation(runtime_token)


async def _run_closed_loop(
    job_id: str,
    lab: Any,
    spec: Dict[str, Any],
    job_manager: JobManager,
    *,
    backend_id: str = "",
    lease_id: Optional[str] = None,
    command_matrix: Any = None,
) -> None:
    command = dict(spec.get("command") or {})
    # Thread job-level kernels[] into OPTIMIZE parameters (edge eval flags).
    job_kernels = spec.get("kernels") or []
    params = dict(command.get("parameters") or {})
    if job_kernels:
        existing = list(params.get("kernels") or [])
        for kid in job_kernels:
            if kid not in existing:
                existing.append(kid)
        params["kernels"] = existing
    # Forward staged session packages into the edge OPTIMIZE / pipeline payload.
    packages = spec.get("kernel_packages")
    if isinstance(packages, list) and packages:
        params["kernel_packages"] = list(packages)
    command["parameters"] = params
    try:
        cmd = parse_command_payload(dict(command))
    except ValidationError as exc:
        raise ValueError(f"invalid closed_loop command: {exc}") from exc
    if not isinstance(cmd, OptimizeBody):
        raise ValueError("closed_loop command must be OPTIMIZE")
    if job_manager.is_cancel_requested(job_id):
        return
    job_manager.update_progress(job_id, {"phase": "optimizing", "message": "OPTIMIZING"})
    setattr(lab, "_job_abort_check", lambda: job_manager.is_cancel_requested(job_id))
    setattr(lab, "_job_accept_check", lambda: job_manager.is_accept_requested(job_id))
    setattr(lab, "_job_kernels", list(params.get("kernels") or []))
    try:
        from lab_model.coordinator.jobs.command_matrix import command_matrix_enabled

        use_matrix = (
            command_matrix is not None
            and backend_id
            and command_matrix_enabled(backend_id)
        )
        if use_matrix:
            from lab_model.coordinator.jobs.matrix_drain import enqueue_and_await

            lab_state = None
            try:
                lab_state = lab.get_lab_state() if hasattr(lab, "get_lab_state") else None
            except Exception:  # noqa: BLE001
                lab_state = None

            async def _execute(item: Any) -> None:
                await execute_validated_command(lab, parse_command_payload(item.payload))

            await enqueue_and_await(
                command_matrix,
                dict(command),
                backend_id=backend_id,
                lease_id=str(lease_id or ""),
                execute=_execute,
                lab_state=lab_state if isinstance(lab_state, dict) else None,
            )
        else:
            await execute_validated_command(lab, cmd)
    finally:
        if hasattr(lab, "_job_abort_check"):
            delattr(lab, "_job_abort_check")
        if hasattr(lab, "_job_accept_check"):
            delattr(lab, "_job_accept_check")
        if hasattr(lab, "_job_kernels"):
            delattr(lab, "_job_kernels")


async def _run_compiled_dag(
    job_id: str,
    lab: Any,
    spec: Dict[str, Any],
    job_manager: JobManager,
    *,
    runtime_manager: Any = None,
    control_dir: Optional[str] = None,
) -> None:
    steps = spec.get("steps") or []
    total = len(steps)
    job_manager.update_progress(
        job_id,
        {"phase": "compiled_dag", "step_index": 0, "steps_total": total},
    )
    for index, envelope in enumerate(steps):
        if job_manager.is_cancel_requested(job_id):
            return
        if not isinstance(envelope, dict):
            raise ValueError(f"step {index + 1} is not an object")
        job_manager.update_progress(
            job_id,
            {
                "phase": "compiled_dag",
                "step_index": index,
                "steps_total": total,
                "current_action": envelope.get("action"),
                "current_target": envelope.get("target_id"),
                "message": f"Step {index + 1}/{total}: {envelope.get('action')}",
            },
        )
        try:
            cmd = parse_command_payload(dict(envelope))
        except ValidationError as exc:
            raise ValueError(f"step {index + 1} invalid primitive: {exc}") from exc
        if job_manager.is_cancel_requested(job_id):
            return
        try:
            await execute_validated_command(lab, cmd)
        except Exception as exc:
            action = envelope.get("action", "?")
            raise RuntimeError(
                f"compiled_dag step {index + 1} ({action}) failed: {exc}"
            ) from exc
        job_manager.update_progress(
            job_id,
            {"steps_completed": index + 1},
        )

    finalize = spec.get("finalize_checkout")
    if (
        isinstance(finalize, dict)
        and not job_manager.is_cancel_requested(job_id)
        and control_dir
    ):
        job_manager.update_progress(
            job_id,
            {
                "phase": "finalize",
                "message": "Status: Finalizing checkout pointer…",
            },
        )
        fin = await apply_checkout_finalize(
            control_dir=control_dir,
            repo_id=str(finalize.get("repo_id")),
            configuration_id=str(finalize.get("configuration_id")),
            branch=str(finalize.get("branch") or "main"),
            lab=lab,
            runtime_manager=runtime_manager,
        )
        job_manager.update_progress(job_id, {"finalize": fin, "message": "Checkout finalized"})


async def _telemetry_loop(
    job_id: str,
    *,
    lab: Any,
    job_manager: JobManager,
    interval_s: float = 0.25,
) -> None:
    while True:
        try:
            record = job_manager.get(job_id)
        except KeyError:
            return
        if record.status != "running":
            return

        state = lab.get_lab_state() if lab is not None else {}
        patch: Dict[str, Any] = {
            "system_status": state.get("system_status"),
        }
        patch.update(record.progress or {})

        if record.mode == "closed_loop":
            sess = state.get("optimization_session")
            if isinstance(sess, dict):
                patch.update(
                    {
                        "eval": sess.get("eval"),
                        "best_loss": sess.get("best_loss"),
                        "session_label": sess.get("session_label"),
                    }
                )
                trace = sess.get("trace")
                if isinstance(trace, list) and trace:
                    patch["trace_tail"] = trace[-5:]
                last_eval = sess.get("last_eval")
                if isinstance(last_eval, dict):
                    patch["last_eval"] = last_eval

            last_result = state.get("last_ensemble_optimization")
            if isinstance(last_result, dict) and record.status == "running":
                patch["best_loss"] = patch.get("best_loss") or last_result.get("best_loss")

        job_manager.update_progress(job_id, patch)
        await asyncio.sleep(interval_s)


def _collect_result(lab: Any, mode: str, spec: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    state = lab.get_lab_state() if lab is not None else {}
    if mode == "compiled_dag" and isinstance(spec, dict):
        out: Dict[str, Any] = {
            "system_status": state.get("system_status"),
            "steps_total": len(spec.get("steps") or []),
        }
        if spec.get("finalize_checkout"):
            out["finalize_checkout"] = spec.get("finalize_checkout")
        return out
    if mode == "closed_loop":
        last = state.get("last_ensemble_optimization")
        if isinstance(last, dict):
            return {"last_ensemble_optimization": last}
        sess = state.get("optimization_session")
        if isinstance(sess, dict):
            return {"optimization_session": sess}
    return {"system_status": state.get("system_status")}


def schedule_job_runner(
    *,
    job_manager: JobManager,
    loop: asyncio.AbstractEventLoop,
    run_fn: Callable[[str], Any],
) -> None:
    """Start the next queued job if the runner is idle."""
    if not job_manager.runner_should_start():
        return
    nxt = job_manager.pop_next_queued()
    if nxt is None:
        return
    job_manager.note_runner_scheduled(True)

    async def _wrapper() -> None:
        try:
            await run_fn(nxt.job_id)
        finally:
            job_manager.note_runner_scheduled(False)
            schedule_job_runner(job_manager=job_manager, loop=loop, run_fn=run_fn)

    loop.create_task(_wrapper(), name=f"job-runner-{nxt.job_id}")
