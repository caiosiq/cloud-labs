"""Finalize control-repo pointer after a reconcile DAG job completes."""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from lab_model.coordinator.state.control_manager import ControlManager, write_bench_origin


async def apply_checkout_finalize(
    *,
    control_dir: str,
    repo_id: str,
    configuration_id: str,
    branch: str,
    lab: Any,
    runtime_manager: Any,
) -> Dict[str, Any]:
    """Record hard-checkout outcome after reconcile primitives finished on the bench."""
    safe_repo = (repo_id or "").strip()
    commit = (configuration_id or "").strip()
    branch_name = (branch or "main").strip() or "main"
    if not safe_repo or not commit:
        raise ValueError("finalize_checkout requires repo_id and configuration_id")

    mgr = ControlManager(control_dir, safe_repo)
    document = mgr.get_configuration(commit)
    configuration = document.get("configuration") or {}
    metadata = document.get("metadata") or {}

    runtime_mgr = runtime_manager
    if runtime_mgr is None:
        runtime_mgr = getattr(lab, "_lab_runtime_manager", None)
    if runtime_mgr is None:
        raise RuntimeError("RuntimeManager not available for checkout finalize")

    runtime_mgr.apply_hard_checkout_projection(
        configuration,
        source=f"hard_checkout:{commit}",
        metadata=metadata if isinstance(metadata, Mapping) else None,
    )
    mgr.set_applied(commit, branch=branch_name)
    mgr.set_viewing(None)
    write_bench_origin(control_dir, safe_repo, commit)

    persist = getattr(lab, "_persist_state", None)
    if callable(persist):
        persist()

    return {
        "repo_id": safe_repo,
        "branch": branch_name,
        "configuration_id": commit,
        "status": "finalized",
    }
