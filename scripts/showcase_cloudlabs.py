#!/usr/bin/env python3
"""Full tour of the cloud-labs package (mock-first).

Run with the FastAPI server on mock backend::

    cd backend
    python main.py

Then from repo root::

    python scripts/showcase_cloudlabs.py

This script is the "read the codebase" companion: each section maps to a
subsystem in ``docs/PROGRAMMABLE_LAB_VISION.md`` and ``docs/EXECUTION_MODES.md``.

Sections
--------
1. Surfaces + backend discovery
2. Session lease (Phase B) — exclusive primitive ownership
3. Live state + refresh_pose
4. Imperative primitives — move, RECORD_MEASURABLES, MeasurableTensor (Phase D)
5. Objective graph compile + preflight (Phase E)
6. Edge kernel catalog (Phase F mock registry)
7. Compiled DAG job — full primitive IR, not reconcile-only (Phase C)
8. Closed-loop ensemble job — cooperative cancel + mock measurables bridge
9. Local VC vs catalog pins — list both sources; optional local commit

TorchScript kernels
-------------------
Catalog-kernel COBYLA: ``scripts/example_torchscript_cobyla_mirror.py``.
Author-defined session kernels + feature objectives:
``scripts/example_session_kernel_author.py`` (see ``docs/SESSION_KERNELS.md``).

Measurables note
----------------
On **mock**, ensemble optimization still uses a **synthetic landscape** for loss,
but each eval now **writes centroid/power into runtime measurables** so
``refresh_pose`` / ``RECORD_MEASURABLES`` / tensor resolve stay consistent.
**Real** bench + ``lab_automation`` integration is deferred until hardware paths
use ``materialize_measurable`` end-to-end.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

_BACKEND_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
)
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_ENSEMBLE_EXAMPLE = (
    _REPO_ROOT / "schemas" / "ensemble_optimization_examples" / "two_mirror_mock.json"
)

from lab_model.optimization.sdk import (  # noqa: E402
    CloudLabsClient,
    ObjectiveGraphBuilder,
    compile_objective,
    connect,
    preflight_compiled_objective,
    resolve_backend_id,
    submit_compiled_dag,
    wait_for_job,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
_LOG = logging.getLogger("showcase")


def _banner(title: str) -> None:
    line = "=" * min(72, max(40, len(title) + 8))
    _LOG.info(line)
    _LOG.info("  %s", title)
    _LOG.info(line)


def _section_surfaces(lab: CloudLabsClient) -> None:
    _banner("1 — Surfaces + backend")
    state = lab.get_lab_state()
    backend_id = state.get("active_backend_id") or lab.backend_id
    _LOG.info(
        "Landing (/) | Twin UI (/twin) = direct control + local VC | Operations (/operations) = jobs | "
        "Catalog (/catalog) = remote pins | Wiki (/wiki) = capabilities"
    )
    _LOG.info("active_backend_id=%s system_status=%s", backend_id, state.get("system_status"))
    tags = sorted((state.get("components") or {}).keys())
    _LOG.info("runtime components (%d): %s", len(tags), ", ".join(tags[:12]) + ("…" if len(tags) > 12 else ""))


def _section_lease(lab: CloudLabsClient) -> None:
    _banner("2 — Session lease (Phase B)")
    _LOG.info("lease_id=%s holder=%s mode=%s", lab.lease_id, lab.holder, lab.mode)
    _LOG.info(
        "Every hardware-affecting path must hold a lease (jobs acquire their own on run)."
    )


def _section_refresh(lab: CloudLabsClient, tag: str) -> Dict[str, Any]:
    _banner("3 — refresh_pose / live state")
    pose = lab.refresh_pose(tag)
    _LOG.info(
        "%s nominal_pose: x=%.3f y=%.3f rot=%.3f",
        tag,
        pose["nominal_pose"]["x"],
        pose["nominal_pose"]["y"],
        pose["nominal_pose"]["rotation"],
    )
    meas_keys = sorted((pose.get("measurables") or {}).keys())
    _LOG.info("measurables keys: %s", meas_keys or "(empty — capture next)")
    return pose


def _section_imperative(lab: CloudLabsClient, tag_move: str, tag_capture: str) -> None:
    _banner("4 — Imperative primitives + MeasurableTensor (Phase D)")
    before = lab.refresh_pose(tag_move, include_measurables=False)
    x0 = float(before["nominal_pose"]["x"])
    x1 = x0 + 0.25
    _LOG.info("MOVE_COMPONENT path: tunables.nominal_pose.x  %.3f → %.3f", x0, x1)
    lab.move_component(tag_move, "tunables.nominal_pose.x", x1)
    lab.wait_for_primitive_settled()
    _LOG.info("status after move: %s", lab.get_lab_state().get("system_status"))

    _LOG.info("RECORD_MEASURABLES on %s", tag_capture)
    image = lab.capture_measurable(tag_capture, "camera_image")
    if isinstance(image, dict):
        _LOG.info("camera_image payload keys: %s", sorted(image.keys()))
    tensor = lab.measurable(tag_capture, "camera_image").resolve()
    _LOG.info(
        "MeasurableTensor %s.%s dtype=%s shape=%s domain=%s",
        tensor.tag_id,
        tensor.field,
        tensor.dtype,
        tensor.shape,
        tensor.domain,
    )


def _section_objective(lab: CloudLabsClient) -> Dict[str, Any]:
    _banner("5 — Objective graph compile + preflight (Phase E)")
    graph = (
        ObjectiveGraphBuilder(minimize=True)
        .term(
            term_id="centroid_rms_px",
            tag_id="tag_20",
            field="camera_image",
            weight=1.0,
            metric="rms_distance_px",
            target_px={"x": 512.0, "y": 384.0},
        )
        .term(
            term_id="power_intensity",
            tag_id="tag_20",
            field="last_optimization_score",
            weight=0.35,
            metric="one_minus_normalized",
            normalize={"min": 0.0, "max": 1.0},
        )
        .build()
    )
    compiled = compile_objective(graph)
    _LOG.info("compiled objective terms: %d", len(compiled.get("terms", [])))
    state = lab.get_lab_state()
    preflight_compiled_objective(state, compiled)
    _LOG.info("preflight OK for compiled objective sources")
    return compiled


def _section_kernels(lab: CloudLabsClient) -> List[str]:
    _banner("6 — Edge kernel catalog (Phase F mock registry)")
    rows = lab.list_kernels()
    for row in rows:
        _LOG.info(
            "  %-36s  %s  [%s]",
            row.get("id"),
            row.get("label"),
            row.get("backend"),
        )
    mock_ids = [str(r["id"]) for r in rows if r.get("id")]
    preferred = [
        kid
        for kid in ("ensemble.eval.mock_landscape", "ensemble.eval.block_cobyla")
        if kid in mock_ids
    ]
    return preferred or mock_ids[:2]


def _load_ensemble_envelope() -> Dict[str, Any]:
    if _ENSEMBLE_EXAMPLE.is_file():
        with _ENSEMBLE_EXAMPLE.open(encoding="utf-8") as fh:
            return json.load(fh)
    raise FileNotFoundError(f"missing example: {_ENSEMBLE_EXAMPLE}")


def _section_compiled_dag(lab: CloudLabsClient, tag_capture: str) -> str:
    _banner("7 — Compiled DAG job (Phase C full primitive IR)")
    submitted = submit_compiled_dag(
        lab,
        steps=[
            {
                "action": "RECORD_MEASURABLES",
                "target_id": tag_capture,
                "parameters": {},
            },
            {
                "action": "SET_MOTOR_SETPOINT",
                "target_id": "tag_20",
                "parameters": {"motor_id": 1, "angle_deg": 0.1},
            },
        ],
        holder="showcase:compiled_dag",
    )
    job_id = str(submitted["job_id"])
    _LOG.info("submitted compiled_dag job_id=%s", job_id)
    done = wait_for_job(lab, job_id, poll_interval_s=0.35, timeout_s=120.0)
    _LOG.info("compiled_dag terminal status=%s", done.get("status"))
    return job_id


def _section_closed_loop(
    lab: CloudLabsClient,
    kernels: List[str],
    *,
    max_evals: int,
    demo_cancel: bool,
) -> str:
    _banner("8 — Closed-loop ensemble job (Phase C + mock measurables bridge)")
    envelope = _load_ensemble_envelope()
    params = dict(envelope["parameters"])
    params["session_label"] = "showcase two-mirror"
    solver = dict(params["solver"])
    solver["max_total_evals"] = max_evals
    blocks = list(solver.get("blocks") or [])
    if blocks:
        blocks[0] = {**blocks[0], "max_evals": max_evals, "passes": 1}
    solver["blocks"] = blocks
    params["solver"] = solver

    submitted = lab.optimize(
        envelope["target_id"],
        params,
        wait=False,
        kernels=kernels or None,
    )
    job_id = str(submitted["job_id"])
    _LOG.info("submitted closed_loop job_id=%s kernels=%s", job_id, kernels)

    if demo_cancel:
        from lab_model.optimization.sdk import get_job

        deadline = time.monotonic() + 180.0
        while time.monotonic() < deadline:
            rec = get_job(lab, job_id)
            status = str(rec.get("status") or "")
            progress = rec.get("progress") or {}
            if int(progress.get("eval") or 0) >= 2 and status == "running":
                _LOG.info("demo_cancel: requesting cancel after a few evals")
                lab.cancel_job(job_id)
                break
            if status in {"succeeded", "failed", "cancelled"}:
                break
            time.sleep(0.35)

    done = wait_for_job(lab, job_id, poll_interval_s=0.35, timeout_s=180.0)
    status = str(done.get("status") or "")
    _LOG.info("closed_loop terminal status=%s error=%s", status, done.get("error"))
    result = done.get("result") or {}
    last = result.get("last_ensemble_optimization") or {}
    if last:
        _LOG.info(
            "best_loss=%.6f evals=%s",
            float(last.get("best_loss", 0)),
            last.get("evals"),
        )
    pose = lab.refresh_pose("tag_20")
    meas = pose.get("measurables") or {}
    if meas.get("centroid_x_px") is not None:
        _LOG.info(
            "post-run measurables: centroid=(%.1f, %.1f) power=%.4f",
            float(meas.get("centroid_x_px", 0)),
            float(meas.get("centroid_y_px", 0)),
            float(meas.get("last_optimization_score", 0)),
        )
    return job_id


def _section_vc_commit(lab: CloudLabsClient) -> None:
    _banner("9 — Local VC vs catalog pins")
    repos = lab.list_control_repos()
    pins = lab.list_catalog_pins()
    _LOG.info(
        "local repos: %s",
        [r.get("repo_id") for r in repos] or "(none)",
    )
    _LOG.info(
        "catalog pins: %s",
        [
            f"{p.get('pin_id')} -> {str(p.get('configuration_id') or '')[:8]}…"
            for p in pins
        ]
        or "(none)",
    )
    if pins:
        sample = str(pins[0].get("pin_id") or "")
        if sample:
            _LOG.info(
                "script tip: lab.load_snapshot(catalog_pin=%r)  # frozen",
                sample,
            )
    if repos:
        sample_repo = str(repos[0].get("repo_id") or "")
        if sample_repo:
            _LOG.info(
                "script tip: lab.load_snapshot(%r, 'main')  # local branch head",
                sample_repo,
            )

    if not repos:
        _LOG.info("no control repos on this deployment — skip commit demo")
        return
    repo_id = str(repos[0].get("repo_id") or "")
    if not repo_id:
        _LOG.info("control repo list empty — skip")
        return
    try:
        out = lab.commit_configuration(repo=repo_id, message="showcase_cloudlabs snapshot")
        _LOG.info("commit_configuration repo=%s id=%s", repo_id, out.get("configuration_id", out))
    except Exception as exc:
        _LOG.warning("commit skipped: %s", exc)


def main() -> int:
    parser = argparse.ArgumentParser(description="cloud-labs full package showcase")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--backend", default=None)
    parser.add_argument("--tag-move", default="tag_20")
    parser.add_argument("--tag-capture", default="tag_22")
    parser.add_argument("--skip-optimize", action="store_true")
    parser.add_argument("--demo-cancel", action="store_true", help="Cancel closed-loop after ~2 evals")
    parser.add_argument("--max-evals", type=int, default=24, help="Cap evals for faster demo")
    parser.add_argument("--skip-vc", action="store_true")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    backend_id = args.backend or resolve_backend_id(base_url)
    _LOG.info("cloud-labs showcase | backend=%s | %s", backend_id, base_url)

    with connect(backend_id, base_url=base_url, mode="imperative") as lab:
        _section_surfaces(lab)
        _section_lease(lab)
        _section_refresh(lab, args.tag_move)
        _section_imperative(lab, args.tag_move, args.tag_capture)
        _section_objective(lab)
        kernels = _section_kernels(lab)
        _section_compiled_dag(lab, args.tag_capture)
        if not args.skip_optimize:
            _section_closed_loop(
                lab,
                kernels,
                max_evals=max(8, args.max_evals),
                demo_cancel=args.demo_cancel,
            )
        else:
            _LOG.info("skipped closed-loop (--skip-optimize)")
        if not args.skip_vc:
            _section_vc_commit(lab)

    _LOG.info("showcase complete — see docs/PROGRAMMABLE_LAB_VISION.md for architecture map")
    return 0


if __name__ == "__main__":
    sys.exit(main())
