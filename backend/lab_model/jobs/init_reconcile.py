"""Auto-prepend reconcile init step when a job declares a snapshot (Phase G)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from lab_model.jobs.initialization_policy import normalize_initialization_policy
from lab_model.state.control_manager import ControlManager
from lab_model.state.reconcile_executor import ReconcilePlanError, execute_reconcile_plan

from .job_manager import JobManager


def resolve_snapshot_target(
    snapshot: Dict[str, Any],
    *,
    pins_lookup: Optional[Any] = None,
) -> Tuple[str, str, str]:
    """Return ``(repo_id, branch, configuration_id)`` from a job snapshot dict."""
    source = str(snapshot.get("source") or "local").strip().lower()
    if source == "catalog":
        pin_id = str(snapshot.get("pin_id") or "").strip()
        if not pin_id:
            raise ValueError("catalog snapshot requires pin_id")
        if pins_lookup is None:
            raise ValueError("catalog pins store not available")
        pin = pins_lookup.get_pin(pin_id)
        if pin is None:
            raise ValueError(f"unknown catalog pin {pin_id!r}")
        repo = str(pin.get("repo_id") or "").strip()
        commit = str(pin.get("configuration_id") or pin.get("commit") or "").strip()
        branch = str(pin.get("branch") or "main").strip() or "main"
        if not repo or not commit:
            raise ValueError(f"catalog pin {pin_id!r} is incomplete")
        return repo, branch, commit

    repo = str(snapshot.get("repo_id") or snapshot.get("repo") or "").strip()
    branch = str(snapshot.get("branch") or "main").strip() or "main"
    commit = str(snapshot.get("commit") or snapshot.get("configuration_id") or "").strip()
    if not repo or not commit:
        raise ValueError("snapshot requires repo_id and commit/configuration_id")
    return repo, branch, commit


async def run_init_reconcile_if_needed(
    job_id: str,
    *,
    lab: Any,
    spec: Dict[str, Any],
    control_dir: str,
    job_manager: JobManager,
    pins_lookup: Optional[Any] = None,
) -> Dict[str, Any]:
    """Plan and execute init reconcile when policy + snapshot demand it.

    Mutates ``spec`` in place to set ``finalize_checkout`` when missing.
    Returns a summary dict (may be empty when init was skipped).
    """
    policy = normalize_initialization_policy(spec.get("initialization_policy"))
    if policy != "force_reconcile":
        return {}

    snapshot = spec.get("snapshot")
    if not isinstance(snapshot, dict) or not snapshot:
        return {}
    if not (control_dir or "").strip():
        raise RuntimeError("control_dir is required for init reconcile")

    repo_id, branch, commit = resolve_snapshot_target(snapshot, pins_lookup=pins_lookup)

    mgr = ControlManager(control_dir, repo_id)
    runtime = lab.get_lab_state()
    plan: List[Dict[str, Any]] = mgr.plan_checkout_from_runtime(runtime, commit)
    total = len(plan)

    job_manager.update_progress(
        job_id,
        {
            "phase": "init",
            "initialization_policy": policy,
            "message": (
                f"Status: Reconciling hardware to {commit[:8]}…"
                if total
                else f"Status: Bench matches {commit[:8]} — finalizing pointer."
            ),
            "init_steps_total": total,
            "init_step_index": 0,
        },
    )

    if plan:
        for index, envelope in enumerate(plan):
            if job_manager.is_cancel_requested(job_id):
                return {"skipped": "cancelled", "steps_planned": total}
            job_manager.update_progress(
                job_id,
                {
                    "phase": "init",
                    "init_step_index": index,
                    "init_steps_total": total,
                    "current_action": envelope.get("action"),
                    "current_target": envelope.get("target_id"),
                    "message": (
                        f"Status: Reconciling hardware — init step {index + 1}/{total}"
                    ),
                },
            )
            try:
                await execute_reconcile_plan(
                    lab,
                    [envelope],
                    source=f"job:{job_id}:init",
                )
            except ReconcilePlanError as exc:
                raise RuntimeError(f"init reconcile failed at step {index + 1}: {exc}") from exc

    if not isinstance(spec.get("finalize_checkout"), dict):
        spec["finalize_checkout"] = {
            "repo_id": repo_id,
            "configuration_id": commit,
            "branch": branch,
        }

    return {
        "repo_id": repo_id,
        "branch": branch,
        "configuration_id": commit,
        "steps_executed": total,
    }
