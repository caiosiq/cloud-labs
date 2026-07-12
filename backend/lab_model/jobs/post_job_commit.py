"""Optional post-job configuration commit (Phase G.7)."""
from __future__ import annotations

import logging
from typing import Any, Dict, Mapping, Optional

from lab_model.state.control_manager import ControlManager, write_bench_origin

_LOG = logging.getLogger(__name__)


def _normalize_on_success(spec: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    raw = spec.get("on_success")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("on_success must be an object")
    commit_cfg = raw.get("commit_configuration")
    if commit_cfg is None:
        return None
    if not isinstance(commit_cfg, dict):
        raise ValueError("on_success.commit_configuration must be an object")
    repo_id = str(commit_cfg.get("repo_id") or "").strip()
    if not repo_id:
        raise ValueError("on_success.commit_configuration requires repo_id")
    branch = str(commit_cfg.get("branch") or "main").strip() or "main"
    message = str(commit_cfg.get("message") or "").strip() or "post-job commit"
    parent_id = commit_cfg.get("parent_id")
    if parent_id is not None:
        parent_id = str(parent_id).strip() or None
    return {
        "repo_id": repo_id,
        "branch": branch,
        "message": message,
        "parent_id": parent_id,
    }


async def apply_post_job_commit_if_needed(
    *,
    job_id: str,
    spec: Dict[str, Any],
    control_dir: str,
    lab: Any,
    runtime_manager: Any,
    repo_owns_bench: Any,
) -> Dict[str, Any]:
    """Commit live bench to local VC after a successful job when spec requests it."""
    commit_req = _normalize_on_success(spec)
    if commit_req is None:
        return {}

    if not (control_dir or "").strip():
        raise RuntimeError("control_dir is required for post-job commit")

    repo_id = commit_req["repo_id"]
    mgr = ControlManager(control_dir, repo_id)
    runtime = lab.get_lab_state() if lab is not None else {}
    owns = bool(repo_owns_bench(repo_id)) if callable(repo_owns_bench) else True
    working = mgr.working_state(runtime, owns_bench=owns)

    if working.get("detached"):
        raise RuntimeError(
            "post-job commit refused: bench is on a detached commit (fork first)"
        )
    if working.get("viewing"):
        raise RuntimeError("post-job commit refused: configuration preview is active")

    catalog_hash = None
    try:
        from lab_model.catalog.catalog_hash import compute_active_catalog_hash

        catalog_hash = compute_active_catalog_hash()
    except Exception:
        catalog_hash = None

    document = mgr.commit_from_runtime(
        runtime,
        message=commit_req["message"],
        branch=commit_req["branch"],
        parent_id=commit_req["parent_id"],
        author=f"job:{job_id}",
        catalog_hash=catalog_hash,
    )
    commit_id = str(document.get("id") or "")
    if commit_id:
        write_bench_origin(control_dir, repo_id, commit_id)

    if runtime_manager is not None and hasattr(runtime_manager, "apply_hard_checkout_projection"):
        configuration = document.get("configuration") or {}
        metadata = document.get("metadata") or {}
        runtime_manager.apply_hard_checkout_projection(
            configuration,
            source=f"post_job_commit:{job_id}",
            metadata=metadata if isinstance(metadata, dict) else None,
        )

    persist = getattr(lab, "_persist_state", None)
    if callable(persist):
        persist()

    summary = {
        "repo_id": repo_id,
        "branch": commit_req["branch"],
        "configuration_id": commit_id,
        "message": commit_req["message"],
        "status": "committed",
    }
    _LOG.info(
        "post-job commit job=%s repo=%s commit=%s",
        job_id,
        repo_id,
        commit_id,
    )
    return summary
