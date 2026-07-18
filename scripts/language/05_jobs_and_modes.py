#!/usr/bin/env python3
"""05 — Objective compile/preflight, compiled DAG, closed-loop job.

Requires::

    pip install -e ./packages/cloudlabs

Run (mock server up)::

    python scripts/language/05_jobs_and_modes.py
    python scripts/language/05_jobs_and_modes.py --skip-optimize
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any, Dict, List

from cloudlabs import (
    ObjectiveGraphBuilder,
    compile_objective,
    connect,
    preflight_compiled_objective,
    resolve_backend_id,
    submit_compiled_dag,
    wait_for_job,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ENSEMBLE_EXAMPLE = (
    _REPO_ROOT / "schemas" / "ensemble_optimization_examples" / "two_mirror_mock.json"
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
_LOG = logging.getLogger("05_jobs_and_modes")


def _section_objective(lab: Any) -> Dict[str, Any]:
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
    preflight_compiled_objective(compiled, client=lab)
    _LOG.info("preflight OK")
    return compiled


def _section_compiled_dag(lab: Any, tag_capture: str) -> str:
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
        holder="language:compiled_dag",
    )
    job_id = str(submitted["job_id"])
    _LOG.info("submitted compiled_dag job_id=%s", job_id)
    done = wait_for_job(lab, job_id, poll_interval_s=0.35, timeout_s=120.0)
    _LOG.info("compiled_dag terminal status=%s", done.get("status"))
    return job_id


def _load_ensemble_envelope() -> Dict[str, Any]:
    if _ENSEMBLE_EXAMPLE.is_file():
        with _ENSEMBLE_EXAMPLE.open(encoding="utf-8") as fh:
            return json.load(fh)
    raise FileNotFoundError(f"missing example: {_ENSEMBLE_EXAMPLE}")


def _pick_kernels(lab: Any) -> List[str]:
    rows = lab.list_kernels()
    mock_ids = [str(r["id"]) for r in rows if r.get("id")]
    preferred = [
        kid
        for kid in ("ensemble.eval.mock_landscape", "ensemble.eval.block_cobyla")
        if kid in mock_ids
    ]
    return preferred or mock_ids[:2]


def _section_closed_loop(lab: Any, kernels: List[str], *, max_evals: int) -> str:
    envelope = _load_ensemble_envelope()
    params = dict(envelope["parameters"])
    params["session_label"] = "language 05 two-mirror"
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
    done = wait_for_job(lab, job_id, poll_interval_s=0.35, timeout_s=180.0)
    status = str(done.get("status") or "")
    _LOG.info("closed_loop terminal status=%s error=%s", status, done.get("error"))
    return job_id


def main() -> int:
    parser = argparse.ArgumentParser(description="cloudlabs: jobs + execution modes")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--backend", default=None)
    parser.add_argument("--tag-capture", default="tag_22")
    parser.add_argument("--skip-optimize", action="store_true")
    parser.add_argument("--max-evals", type=int, default=16)
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    backend_id = args.backend or resolve_backend_id(base_url)
    _LOG.info("backend=%s %s", backend_id, base_url)

    with connect(backend_id, base_url=base_url, mode="imperative") as lab:
        _section_objective(lab)
        _section_compiled_dag(lab, args.tag_capture)
        if not args.skip_optimize:
            kernels = _pick_kernels(lab)
            _section_closed_loop(lab, kernels, max_evals=max(8, args.max_evals))
        else:
            _LOG.info("skipped closed-loop (--skip-optimize)")

    _LOG.info("05_jobs_and_modes complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
