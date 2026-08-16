"""Async drain loop for CommandMatrix (coordinator side).

Enqueued commands are executed via the same southbound / in-process paths as
``POST /api/command`` historically used; this module only serializes per thread
and admits barriers when all columns are free.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Dict, Optional, Set, Tuple

from lab_model.coordinator.jobs.command_matrix import CommandMatrix, QueuedCommand

_LOG = logging.getLogger(__name__)

# (backend_id, thread_id) → running drain task
_DRAIN_TASKS: Dict[Tuple[str, str], asyncio.Task] = {}

ExecuteFn = Callable[[QueuedCommand], Awaitable[None]]


def kick_matrix_drain(
    backend_id: str,
    matrix: CommandMatrix,
    *,
    execute: ExecuteFn,
    thread_ids: Optional[Set[str]] = None,
) -> None:
    """Ensure a drain task is running for each thread that may have work."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        _LOG.warning("kick_matrix_drain: no running event loop (backend=%s)", backend_id)
        return

    targets = thread_ids if thread_ids is not None else set(matrix.threads.keys())
    for tid in targets:
        key = (backend_id, tid)
        task = _DRAIN_TASKS.get(key)
        if task is not None and not task.done():
            continue
        _DRAIN_TASKS[key] = loop.create_task(
            _drain_thread(backend_id, tid, matrix, execute),
            name=f"matrix-drain:{backend_id}:{tid}",
        )


async def _drain_thread(
    backend_id: str,
    thread_id: str,
    matrix: CommandMatrix,
    execute: ExecuteFn,
) -> None:
    while True:
        item = matrix.claim_runnable(thread_id)
        if item is None:
            return

        _LOG.info(
            "matrix drain start backend=%s thread=%s cmd=%s action=%s",
            backend_id,
            thread_id,
            item.command_id,
            item.action,
        )
        err: Optional[str] = None
        try:
            await execute(item)
        except Exception as exc:  # noqa: BLE001
            err = str(exc) or type(exc).__name__
            _LOG.warning(
                "matrix drain failed backend=%s cmd=%s action=%s: %s",
                backend_id,
                item.command_id,
                item.action,
                err,
            )
        matrix.mark_done(item.command_id, error=err)
        _LOG.info(
            "matrix drain done backend=%s cmd=%s action=%s ok=%s",
            backend_id,
            item.command_id,
            item.action,
            err is None,
        )
        # Wake siblings (barrier release / newly runnable heads).
        kick_matrix_drain(backend_id, matrix, execute=execute)


async def await_matrix_command(
    matrix: CommandMatrix,
    command_id: str,
    *,
    timeout_s: float = 3600.0,
    poll_s: float = 0.05,
) -> QueuedCommand:
    """Block until *command_id* reaches done/failed/cancelled."""
    deadline = asyncio.get_running_loop().time() + float(timeout_s)
    while True:
        status = matrix.item_status(command_id)
        if status in ("done", "failed", "cancelled"):
            item = matrix.items.get(command_id)
            if item is None:
                raise RuntimeError(f"command {command_id} disappeared from matrix")
            if status == "failed":
                raise RuntimeError(item.error or f"{command_id} failed")
            if status == "cancelled":
                raise RuntimeError(f"{command_id} cancelled")
            return item
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError(f"timed out waiting for matrix command {command_id}")
        await asyncio.sleep(poll_s)


async def enqueue_and_await(
    matrix: CommandMatrix,
    payload: dict,
    *,
    backend_id: str,
    lease_id: str,
    execute: ExecuteFn,
    lab_state: dict | None = None,
    timeout_s: float = 3600.0,
) -> QueuedCommand:
    """Enqueue a command, kick drain, wait until finished."""
    ack = matrix.enqueue(payload, lease_id=lease_id, lab_state=lab_state)
    kick_matrix_drain(backend_id, matrix, execute=execute)
    return await await_matrix_command(matrix, ack["command_id"], timeout_s=timeout_s)
