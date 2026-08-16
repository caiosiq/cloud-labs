"""Helpers to attach a CommandMatrix to BackendRuntime."""
from __future__ import annotations

from typing import Any, List, Optional

from lab_model.coordinator.jobs.command_matrix import CommandMatrix


def get_or_create_matrix(
    runtime: Any,
    *,
    capabilities: dict | None = None,
    motor_ids: List[int] | None = None,
) -> CommandMatrix:
    """Return the backend's CommandMatrix, creating from capabilities if needed.

    ``motor_ids`` is unused (motors are tag-scoped and created on enqueue);
    kept for call-site compatibility.
    """
    if getattr(runtime, "command_matrix", None) is None:
        backend_id = getattr(runtime, "backend_id", None) or "unknown"
        runtime.command_matrix = CommandMatrix.from_capabilities(
            str(backend_id),
            capabilities,
            motor_ids,
        )
    return runtime.command_matrix
