"""Ensemble OPTIMIZE primitive orchestration."""
from __future__ import annotations

import secrets
from datetime import datetime
from typing import Any, Dict, Mapping, Optional

from lab_model.language.domain.holding import SYSTEM_STATUS_IDLE, SYSTEM_STATUS_OPTIMIZING
from lab_model.execution.edge.client import CONTRACT_VERSION
from lab_model.execution.optimization.preflight import ensemble_scope_tag_ids, preflight_ensemble
from lab_model.execution.optimization.spec import OptimizeEnsembleParameters
from lab_model.coordinator.state.commits import (
    commit_optimization_ensemble_complete,
    null_measurables_for_targets,
)
from lab_model.coordinator.state.state_machine import refuse_if_stored, refuse_if_teleop_active

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
    mutable_params: Dict[str, Any] = dict(params)
    try:
        from lab_model.execution.optimization.pipeline import (
            attach_pipeline,
            optional_catalog_map,
        )

        # ``pipeline`` is stripped before OptimizeEnsembleParameters validation.
        attach_pipeline(mutable_params, catalog=optional_catalog_map(host) or None)
        try:
            from lab_model.execution.edge.ensemble_host import bind_pending_pipeline

            bind_pending_pipeline(host, mutable_params)
        except Exception:  # noqa: BLE001
            pass
    except Exception as exc:  # noqa: BLE001 — compile is best-effort until Phase 2
        print(f"{host.log_prefix} Ensemble pipeline compile skipped: {exc}")

    with host._state_lock:
        snapshot = dict(host.current_state)

    try:
        edge_kernel_ids = None
        catalog_map = getattr(host, "catalog_map", None)
        if getattr(host, "base_url", None):
            # HTTP-edge host: validate TorchScript ids against the edge catalog,
            # never coordinator schemas/kernels. Refresh library rows so camera
            # capability checks match live GET /library (not a stale init snapshot).
            try:
                from lab_model.execution.edge.client import HttpEdgeClient
                from cloudlabs_edge_dev.edge_data import merged_active_rows

                client = HttpEdgeClient(
                    base_url=str(host.base_url),
                    contract_version=getattr(host, "contract_version", None)
                    or CONTRACT_VERSION,
                )
                lib = client.get_library()
                inv = client.get_inventory()
                if isinstance(lib, dict) and isinstance(inv, dict):
                    cmap: Dict[str, Any] = {}
                    for row in merged_active_rows(lib, inv):
                        tid = str(row.get("tag_id") or row.get("id") or "").strip()
                        if tid:
                            cmap[tid] = dict(row)
                    catalog_map = cmap
                    host.catalog_map = cmap
                    print(
                        f"{host.log_prefix} refreshed edge catalog n={len(cmap)}",
                        flush=True,
                    )
            except Exception as exc:  # noqa: BLE001
                print(
                    f"{host.log_prefix} catalog refresh for preflight failed: {exc}",
                    flush=True,
                )

            try:
                from lab_model.execution.edge.client import HttpEdgeClient

                client = HttpEdgeClient(
                    base_url=str(host.base_url),
                    contract_version=getattr(host, "contract_version", None)
                    or CONTRACT_VERSION,
                )
                payload = client.get_kernels()
                rows = (
                    payload.get("kernels")
                    if isinstance(payload, dict)
                    else None
                )
                if not isinstance(rows, list):
                    print(
                        f"{host.log_prefix} Refusing ensemble optimize: "
                        "edge GET /kernels unavailable"
                    )
                    return
                edge_kernel_ids = {
                    str(r.get("id") or "").strip()
                    for r in rows
                    if isinstance(r, dict) and r.get("id")
                }
                print(
                    f"{host.log_prefix} preflight edge kernels n={len(edge_kernel_ids)}",
                    flush=True,
                )
            except Exception as exc:  # noqa: BLE001
                print(
                    f"{host.log_prefix} Refusing ensemble optimize "
                    f"(edge kernel catalog): {exc}"
                )
                return

        spec, _resolver, x0 = preflight_ensemble(
            snapshot,
            mutable_params,
            catalog_map=catalog_map,
            strict_real_objectives=bool(getattr(host, "base_url", None)),
            edge_kernel_ids=edge_kernel_ids,
        )
        try:
            from lab_model.execution.edge.ensemble_host import bind_pending_pipeline

            if getattr(spec, "telemetry", None) is not None:
                bind_pending_pipeline(host, {"telemetry": spec.telemetry})
        except Exception:  # noqa: BLE001
            pass
    except Exception as exc:
        print(f"{host.log_prefix} Refusing ensemble optimize (pre-flight): {exc}")
        return

    params = mutable_params

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
                        "stages": kwargs.get("stages"),
                        "refused": kwargs.get("refused"),
                    }
                    if kwargs.get("early_stop"):
                        record["early_stop"] = True
                        record["stop_loss"] = kwargs.get("stop_loss")
                    # Merge top-level debug_* into stages for Twin panel.
                    stages = record.get("stages")
                    if not isinstance(stages, dict):
                        stages = {}
                        record["stages"] = stages
                    for dbg_key, stage_key in (
                        ("debug_policy", "policy"),
                        ("debug_presence", "presence"),
                        ("debug_scales", "scales"),
                        ("debug_telemetry", "telemetry"),
                    ):
                        payload = kwargs.get(dbg_key)
                        if payload is not None and stage_key not in stages:
                            stages[stage_key] = payload
                    cam = kwargs.get("camera_image")
                    if cam is not None:
                        record["camera_image"] = cam
                        # Sparse preview: mirror into component measurables so
                        # native ImageViewer / camera-image proxy refresh.
                        try:
                            from lab_model.coordinator.state.commits import (
                                commit_observed_measurables,
                            )

                            tag = ""
                            if isinstance(cam, Mapping):
                                tag = str(
                                    cam.get("tag_id")
                                    or cam.get("component_id")
                                    or ""
                                ).strip()
                            if tag:
                                commit_observed_measurables(
                                    host.current_state,
                                    tag,
                                    {"camera_image": cam},
                                )
                                sess["preview_camera_tag"] = tag
                                tel = stages.setdefault("telemetry", {})
                                if isinstance(tel, dict):
                                    tel["committed_to_lab_state"] = True
                                    tel["commit_tag"] = tag
                                print(
                                    f"{host.log_prefix} preview camera committed "
                                    f"tag={tag!r} eval={step}",
                                    flush=True,
                                )
                            else:
                                tel = stages.setdefault("telemetry", {})
                                if isinstance(tel, dict):
                                    tel["ok"] = False
                                    tel["error"] = (
                                        "camera_image on progress but no tag_id"
                                    )
                                print(
                                    f"{host.log_prefix} preview camera MISSING tag_id "
                                    f"eval={step}",
                                    flush=True,
                                )
                        except Exception as exc:  # noqa: BLE001
                            tel = stages.setdefault("telemetry", {})
                            if isinstance(tel, dict):
                                tel["ok"] = False
                                tel["error"] = f"commit failed: {exc}"
                            print(
                                f"{host.log_prefix} preview camera commit FAIL "
                                f"eval={step}: {exc}",
                                flush=True,
                            )
                    # One-line coordinator breadcrumb for policy/presence.
                    pol = stages.get("policy") if isinstance(stages, dict) else None
                    if isinstance(pol, dict):
                        print(
                            f"{host.log_prefix} policy eval={step} "
                            f"loss={kwargs.get('loss')!r} "
                            f"stop={pol.get('stop_loss')!r} "
                            f"early={bool(record.get('early_stop'))} "
                            f"presence_absent={bool(pol.get('presence_absent'))} "
                            f"fov={pol.get('fov')}",
                            flush=True,
                        )
                    trace = sess.setdefault("trace", [])
                    trace.append(record)
                    if len(trace) > 200:
                        sess["trace"] = trace[-200:]
                    sess["last_eval"] = record
            host.current_state["last_updated"] = datetime.now().isoformat()
        host._persist_state()

    result: Optional[Dict[str, Any]] = None
    should_abort = getattr(host, "_job_abort_check", None)
    should_accept = getattr(host, "_job_accept_check", None)
    try:
        result = await host._primitive_run_ensemble_optimization(
            spec=spec,
            x0=x0,
            session_id=session_id,
            progress_callback=progress_callback,
            should_abort=should_abort,
            should_accept=should_accept,
        )
    except NotImplementedError as exc:
        print(f"{host.log_prefix} Ensemble optimization stub: {exc}")
        result = None
    except TypeError:
        # Older hosts without should_accept kwarg.
        try:
            result = await host._primitive_run_ensemble_optimization(
                spec=spec,
                x0=x0,
                session_id=session_id,
                progress_callback=progress_callback,
                should_abort=should_abort,
            )
        except NotImplementedError as exc:
            print(f"{host.log_prefix} Ensemble optimization stub: {exc}")
            result = None
        except Exception as exc:  # noqa: BLE001
            print(f"{host.log_prefix} Ensemble optimization failed: {exc}")
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
                "aborted": bool(result.get("aborted")),
                "early_stopped": bool(result.get("early_stopped")),
                "early_stop_reason": result.get("early_stop_reason"),
                "refusal": result.get("refusal"),
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
        status = "complete" if isinstance(result, dict) and result.get("final_values") else "ended without commit"
        print(
            f"{host.log_prefix} Ensemble optimization {status} "
            f"({label}, session={session_id})"
        )


__all__ = ["run_optimize_ensemble"]
