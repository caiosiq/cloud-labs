"""Ensemble OPTIMIZE primitive orchestration."""
from __future__ import annotations

import secrets
from datetime import datetime
from typing import Any, Dict, Mapping, Optional

from lab_model.domain.holding import SYSTEM_STATUS_IDLE, SYSTEM_STATUS_OPTIMIZING
from lab_model.optimization.preflight import ensemble_scope_tag_ids, preflight_ensemble
from lab_model.optimization.spec import OptimizeEnsembleParameters
from lab_model.state.commits import (
    commit_optimization_ensemble_complete,
    null_measurables_for_targets,
)
from lab_model.state.state_machine import refuse_if_stored, refuse_if_teleop_active

from .protocol import OptimizeHost


async def run_optimize_ensemble(
    host: OptimizeHost,
    anchor_tag_id: str,
    params: Mapping[str, Any],
) -> None:
    """
    Ensemble optimization entry: pre-flight → OPTIMIZING → inner loop → commit.

    Pre-flight (path resolve) runs before any session lock.
    """
    with host._state_lock:
        snapshot = dict(host.current_state)

    try:
        spec, _resolver, x0 = preflight_ensemble(snapshot, params)
    except Exception as exc:
        print(f"{host.log_prefix} Refusing ensemble optimize (pre-flight): {exc}")
        return

    scope = ensemble_scope_tag_ids(spec)
    if anchor_tag_id not in scope and spec.variables:
        # Anchor tag is legacy API field; first variable tag is acceptable.
        pass

    for tag_id in scope:
        if not host._catalog_meta_for_tag(tag_id):
            print(
                f"{host.log_prefix} Refusing ensemble optimize: "
                f"{tag_id} not in catalog."
            )
            return
        with host._state_lock:
            snap = host.current_state
        refusal = refuse_if_stored(snap, tag_id, primitive_name="optimize")
        if refusal:
            print(f"{host.log_prefix} Refusing ensemble optimize: {refusal.reason}")
            return
        refusal = refuse_if_teleop_active(snap, tag_id, primitive_name="optimize")
        if refusal:
            print(f"{host.log_prefix} Refusing ensemble optimize: {refusal.reason}")
            return

    session_id = secrets.token_hex(8)
    label = spec.session_label or "ensemble"
    run_dir_basename = host._primitive_prepare_optimization_run(anchor_tag_id, label)

    with host._state_lock:
        null_measurables_for_targets(host.current_state, scope)
        host.current_state["system_status"] = SYSTEM_STATUS_OPTIMIZING
        host.current_state["optimization_step"] = 0
        host.current_state["optimization_run_dir"] = run_dir_basename
        host.current_state["optimization_target_id"] = anchor_tag_id
        host.current_state["optimization_session"] = {
            "id": session_id,
            "mode": "ensemble",
            "scope": scope,
            "eval": 0,
            "best_loss": None,
            "session_label": label,
        }
        host.current_state["last_updated"] = datetime.now().isoformat()
    host._persist_state()

    def progress_callback(*, step: Optional[int] = None, **kwargs: Any) -> None:
        with host._state_lock:
            if step is not None:
                host.current_state["optimization_step"] = int(step)
            sess = host.current_state.get("optimization_session")
            if isinstance(sess, dict):
                if step is not None:
                    sess["eval"] = int(step)
                loss = kwargs.get("best_loss")
                if loss is not None:
                    try:
                        sess["best_loss"] = float(loss)
                    except (TypeError, ValueError):
                        pass
                if step is not None:
                    record: Dict[str, Any] = {
                        "eval": int(step),
                        "loss": kwargs.get("loss"),
                        "best_loss": kwargs.get("best_loss"),
                        "terms": kwargs.get("terms"),
                        "block_id": kwargs.get("block_id"),
                        "u": kwargs.get("u"),
                        "values": kwargs.get("values"),
                    }
                    trace = sess.setdefault("trace", [])
                    trace.append(record)
                    if len(trace) > 200:
                        sess["trace"] = trace[-200:]
                    sess["last_eval"] = record
            host.current_state["last_updated"] = datetime.now().isoformat()
        host._persist_state()

    result: Optional[Dict[str, Any]] = None
    try:
        result = await host._primitive_run_ensemble_optimization(
            spec=spec,
            x0=x0,
            session_id=session_id,
            progress_callback=progress_callback,
        )
    except NotImplementedError as exc:
        print(f"{host.log_prefix} Ensemble optimization stub: {exc}")
        result = None
    except Exception as exc:  # noqa: BLE001
        print(f"{host.log_prefix} Ensemble optimization failed: {exc}")
        result = None

    if isinstance(result, dict) and result.get("final_values"):
        with host._state_lock:
            commit_optimization_ensemble_complete(
                host.current_state,
                spec=spec,
                session_id=session_id,
                final_values=result["final_values"],
                best_loss=float(result.get("best_loss", result.get("score", 1.0))),
            )
            sess = host.current_state.get("optimization_session")
            trace = []
            if isinstance(sess, dict):
                raw_trace = sess.get("trace")
                if isinstance(raw_trace, list):
                    trace = raw_trace[-200:]
            host.current_state["last_ensemble_optimization"] = {
                "session_id": session_id,
                "session_label": label,
                "best_loss": float(result.get("best_loss", 1.0)),
                "evals": int(result.get("evals") or (sess or {}).get("eval") or 0),
                "final_values": dict(result["final_values"]),
                "trace": trace,
                "completed_at": datetime.now().isoformat(),
            }
            host.current_state["last_updated"] = datetime.now().isoformat()
        host._persist_state()

    try:
        host._primitive_finalize_optimization_run()
    finally:
        with host._state_lock:
            host.current_state["system_status"] = SYSTEM_STATUS_IDLE
            host.current_state["optimization_step"] = 0
            host.current_state["optimization_run_dir"] = None
            host.current_state["optimization_target_id"] = None
            host.current_state.pop("optimization_session", None)
            host.current_state["last_updated"] = datetime.now().isoformat()
        host._persist_state()
        print(
            f"{host.log_prefix} Ensemble optimization complete "
            f"({label}, session={session_id})"
        )


__all__ = ["run_optimize_ensemble"]
