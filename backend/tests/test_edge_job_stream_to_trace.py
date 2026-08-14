"""Phase 4: edge job SSE stream → optimization_session.trace."""
from __future__ import annotations

import asyncio
import copy
import threading
import unittest
from typing import Any, AsyncIterator, Dict, List
from unittest import mock

from lab_model.execution.edge.ensemble_host import (
    EdgeJobStreamError,
    HttpEdgeEnsembleHost,
    parse_sse_blocks,
)
from lab_model.execution.orchestration.optimize_ensemble import run_optimize_ensemble


def _minimal_state() -> Dict[str, Any]:
    return {
        "system_status": "IDLE",
        "components": {
            "tag_20": {
                "id": "tag_20",
                "statecontrol": {
                    "tunables": {
                        "motor_1_angle_deg": 0.0,
                    },
                    "measurables": {"camera_image": None},
                },
                "placement": "table",
            }
        },
        "optimization_session": None,
    }


class _FakeStore:
    def __init__(self, state: Dict[str, Any]) -> None:
        self.runtime = mock.Mock()
        self.runtime.state = state
        self.persisted = 0

    def persist(self) -> None:
        self.persisted += 1

    def snapshot(self) -> Dict[str, Any]:
        return copy.deepcopy(self.runtime.state)


def _ensemble_payload() -> Dict[str, Any]:
    return {
        "mode": "ensemble",
        "session_label": "phase4",
        "variables": [
            {
                "id": "m1",
                "tag_id": "tag_20",
                "path": "tunables.motor_1_angle_deg",
                "bounds": {"min": -1.0, "max": 1.0},
                "unit": "deg",
            }
        ],
        "objective": {
            "type": "weighted_sum",
            "minimize": True,
            "terms": [
                {
                    "id": "score",
                    "weight": 1.0,
                    "metric": "one_minus_normalized",
                    "source": {
                        "kind": "measurable_scalar",
                        "tag_id": "tag_20",
                        "path": "measurables.last_optimization_score",
                        "normalize": {"min": 0.0, "max": 1.0},
                    },
                }
            ],
        },
        "solver": {
            "type": "block_cobyla",
            "max_total_evals": 10,
            "blocks": [{"id": "b0", "variable_ids": ["m1"], "max_evals": 10}],
        },
        "pipeline": {
            "schema_version": 1,
            "variables": [
                {
                    "id": "m1",
                    "tag_id": "tag_20",
                    "actuator": {
                        "kind": "motor",
                        "controller": "wifi_stepper1",
                        "motor_id": 1,
                    },
                    "physical_type": "continuous",
                    "unit": "deg",
                    "bounds": {"min": -1.0, "max": 1.0},
                }
            ],
            "capture": [],
            "objective": {
                "type": "weighted_sum",
                "minimize": True,
                "terms": [
                    {
                        "id": "score",
                        "weight": 1.0,
                        "metric": "one_minus_normalized",
                        "measurable_path": "measurables.last_optimization_score",
                        "params": {"normalize": {"min": 0.0, "max": 1.0}},
                    }
                ],
            },
            "solver": {
                "type": "block_cobyla",
                "max_total_evals": 10,
                "settle_ms": 0,
                "blocks": [{"id": "b0", "variable_ids": ["m1"], "max_evals": 10}],
            },
        },
    }


class ParseSseTests(unittest.TestCase):
    def test_parse_sse_blocks(self) -> None:
        buf = 'data: {"event":"progress","eval":1}\n\ndata: {"event":"final"}\n\npartial'
        events, rest = parse_sse_blocks(buf)
        self.assertEqual(len(events), 2)
        self.assertEqual(rest, "partial")


class EdgeJobStreamToTraceTests(unittest.TestCase):
    def test_ten_progress_events_fill_trace_monotonic_best(self) -> None:
        state = _minimal_state()
        # measurable used by preflight
        state["components"]["tag_20"]["statecontrol"]["measurables"][
            "last_optimization_score"
        ] = 0.5
        store = _FakeStore(state)

        async def _stream(_base: str, _jid: str) -> AsyncIterator[Dict[str, Any]]:
            best = 10.0
            for i in range(1, 11):
                best = best - 0.5
                yield {
                    "event": "progress",
                    "status": "running",
                    "eval": i,
                    "loss": best + 0.1,
                    "best_loss": best,
                    "terms": {"score": best},
                    "block_id": "b0",
                    "u": {"m1": 0.1 * i},
                    "values": {"m1": 0.05 * i},
                }
            yield {
                "event": "final",
                "status": "succeeded",
                "result": {
                    "session_id": "s1",
                    "best_loss": best,
                    "final_values": {"m1": 0.5},
                    "evals": 10,
                    "trace": [],
                },
            }

        host = HttpEdgeEnsembleHost(
            backend_id="real.default",
            base_url="http://edge.test",
            state_store=store,
            catalog_map={"tag_20": {"tag_id": "tag_20", "type": "MIRROR"}},
            stream_iter=_stream,
            execute_fn=lambda _args: {"status": "accepted", "job_id": "job123"},
        )
        host._pending_pipeline = _ensemble_payload()["pipeline"]

        # Soften preflight refusals that need full ensemble IR — call host hook directly
        # then verify progress_callback recording via optimize_ensemble's pattern.
        records: List[Dict[str, Any]] = []

        def _cb(*, step=None, **kwargs):  # noqa: ANN001
            records.append({"eval": step, **kwargs})

        result = asyncio.run(
            host._primitive_run_ensemble_optimization(
                spec=mock.Mock(variables=[mock.Mock(tag_id="tag_20")]),
                x0={"m1": 0.0},
                session_id="s1",
                progress_callback=_cb,
            )
        )
        self.assertEqual(len(records), 10)
        bests = [float(r["best_loss"]) for r in records]
        self.assertEqual(bests, sorted(bests, reverse=True))
        self.assertTrue(all(bests[i] >= bests[i + 1] for i in range(len(bests) - 1)))
        self.assertEqual(result["evals"], 10)
        self.assertEqual(result["final_values"]["m1"], 0.5)

    def test_stream_drop_raises_without_corrupting_prior_trace(self) -> None:
        state = _minimal_state()
        state["optimization_session"] = {
            "id": "s1",
            "mode": "ensemble",
            "trace": [{"eval": 1, "best_loss": 9.0}],
            "eval": 1,
            "best_loss": 9.0,
        }
        store = _FakeStore(state)
        prior = copy.deepcopy(state["optimization_session"]["trace"])

        async def _stream(_base: str, _jid: str) -> AsyncIterator[Dict[str, Any]]:
            yield {
                "event": "progress",
                "eval": 2,
                "loss": 8.5,
                "best_loss": 8.5,
                "values": {"m1": 0.1},
            }
            raise ConnectionError("connection reset")

        host = HttpEdgeEnsembleHost(
            backend_id="real.default",
            base_url="http://edge.test",
            state_store=store,
            catalog_map={"tag_20": {"tag_id": "tag_20"}},
            stream_iter=_stream,
            execute_fn=lambda _args: {"job_id": "job999"},
        )
        host._pending_pipeline = {"schema_version": 1}

        with self.assertRaises(EdgeJobStreamError):
            asyncio.run(
                host._primitive_run_ensemble_optimization(
                    spec=mock.Mock(variables=[mock.Mock(tag_id="tag_20")]),
                    x0={"m1": 0.0},
                    session_id="s1",
                    progress_callback=lambda **_k: None,
                )
            )
        # Prior coordinator trace untouched (host does not rewrite session on stream drop).
        self.assertEqual(state["optimization_session"]["trace"], prior)

    def test_build_optimize_args_forwards_telemetry_camera(self) -> None:
        store = _FakeStore(_minimal_state())
        host = HttpEdgeEnsembleHost(
            backend_id="real.default",
            base_url="http://edge.test",
            state_store=store,
            catalog_map={"tag_20": {"tag_id": "tag_20"}},
        )
        host._pending_pipeline = {"schema_version": 1}
        host._pending_telemetry = {
            "stream": "optimization-ensemble",
            "include": ["camera_image"],
            "camera_every_n": 3,
            "camera_jpeg_quality": 60,
            "camera_jpeg_scale": 0.2,
        }
        args = host._build_optimize_args(
            spec=mock.Mock(variables=[mock.Mock(tag_id="tag_20")]),
            x0={"m1": 0.0},
            session_id="s1",
        )
        self.assertEqual(args["telemetry_camera_every_n"], 3)
        self.assertEqual(args["telemetry_camera_jpeg_quality"], 60)
        self.assertEqual(args["telemetry_camera_jpeg_scale"], 0.2)


if __name__ == "__main__":
    unittest.main()
