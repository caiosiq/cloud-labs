"""Job runner integration on mock communicator (Phase C)."""
from __future__ import annotations

import asyncio
import json
import os
import unittest
from pathlib import Path

from mock_backend.host.communicator import MockLabCommunicator
from lab_model.coordinator.backends.lab_view_config import bootstrap_lab_view, get_lab_view_paths
from lab_model.coordinator.jobs.job_manager import JobManager
from lab_model.coordinator.jobs.lease_manager import SessionLeaseManager
from lab_model.coordinator.jobs.runner import run_job

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_MOCK_LAB_VIEW = _PROJECT_ROOT / "mock_backend" / "lab_view"
_EXAMPLE = _PROJECT_ROOT / "schemas" / "ensemble_optimization_examples" / "two_mirror_mock.json"


class JobRunnerMockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ["LAB_VIEW_PATH"] = str(_MOCK_LAB_VIEW)
        bootstrap_lab_view(str(_PROJECT_ROOT))
        from lab_model.language.domain import motor_rotation_store as motor_rot

        motor_rot.configure(get_lab_view_paths().motor_rotations_json)

    def setUp(self) -> None:
        self.lab = MockLabCommunicator()
        self.job_manager = JobManager()
        self.lease_manager = SessionLeaseManager()

    def test_compiled_dag_record_measurables(self) -> None:
        record = self.job_manager.submit(
            backend_id="mock.default",
            mode="compiled_dag",
            holder="job:test-dag",
            spec={
                "steps": [
                    {
                        "action": "RECORD_MEASURABLES",
                        "target_id": "tag_22",
                        "parameters": {},
                    }
                ],
            },
        )
        asyncio.run(
            run_job(
                record.job_id,
                lab=self.lab,
                runtime_manager=None,
                lease_manager=self.lease_manager,
                job_manager=self.job_manager,
                backend_id="mock.default",
            )
        )
        done = self.job_manager.get(record.job_id)
        self.assertEqual(done.status, "succeeded")
        meas = (
            self.lab.current_state.get("components", {})
            .get("tag_22", {})
            .get("statecontrol", {})
            .get("measurables", {})
        )
        self.assertTrue(isinstance(meas, dict) and meas)

    def test_closed_loop_cancel_during_optimize(self) -> None:
        with _EXAMPLE.open(encoding="utf-8") as fh:
            envelope = json.load(fh)
        params = dict(envelope["parameters"])
        params["solver"] = dict(params["solver"])
        params["solver"]["max_total_evals"] = 60
        params["solver"]["blocks"] = [
            {
                **params["solver"]["blocks"][0],
                "max_evals": 60,
                "passes": 3,
            }
        ]
        record = self.job_manager.submit(
            backend_id="mock.default",
            mode="closed_loop",
            holder="job:test-cancel",
            spec={
                "command": {
                    "action": "OPTIMIZE",
                    "target_id": envelope["target_id"],
                    "parameters": params,
                },
                "kernels": ["ensemble.eval.mock_landscape"],
            },
        )

        async def _run_with_cancel() -> None:
            task = asyncio.create_task(
                run_job(
                    record.job_id,
                    lab=self.lab,
                    runtime_manager=None,
                    lease_manager=self.lease_manager,
                    job_manager=self.job_manager,
                    backend_id="mock.default",
                )
            )
            for _ in range(80):
                await asyncio.sleep(0.05)
                rec = self.job_manager.get(record.job_id)
                if rec.status == "running":
                    progress = rec.progress or {}
                    if int(progress.get("eval") or 0) >= 2:
                        self.job_manager.request_cancel(record.job_id)
                        break
            await task

        asyncio.run(_run_with_cancel())
        done = self.job_manager.get(record.job_id)
        self.assertIn(done.status, {"succeeded", "cancelled"})


if __name__ == "__main__":
    unittest.main()
