"""Tests for post-job commit hook (Phase G.7)."""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import MagicMock

from lab_model.jobs.job_manager import validate_submit_spec
from lab_model.jobs.post_job_commit import _normalize_on_success
from lab_model.state.control_manager import ControlManager


class OnSuccessSpecTests(unittest.TestCase):
    def test_validate_closed_loop_with_on_success(self) -> None:
        spec = validate_submit_spec(
            "closed_loop",
            {
                "command": {
                    "action": "OPTIMIZE",
                    "target_id": "tag_20",
                    "parameters": {"mode": "ensemble", "variables": []},
                },
                "on_success": {
                    "commit_configuration": {
                        "repo_id": "laser-cavity",
                        "branch": "main",
                        "message": "after optimization",
                    }
                },
            },
        )
        self.assertIn("on_success", spec)
        self.assertEqual(
            spec["on_success"]["commit_configuration"]["repo_id"],
            "laser-cavity",
        )

    def test_normalize_requires_repo(self) -> None:
        with self.assertRaises(ValueError):
            _normalize_on_success(
                {"on_success": {"commit_configuration": {"branch": "main"}}}
            )


class PostJobCommitTests(unittest.IsolatedAsyncioTestCase):
    async def test_apply_post_job_commit_writes_commit(self) -> None:
        from lab_model.jobs.post_job_commit import apply_post_job_commit_if_needed

        with tempfile.TemporaryDirectory() as tmp:
            repo = "test-repo"
            mgr = ControlManager(tmp, repo)
            mgr.set_applied("base123", branch="main")
            mgr.save_configuration_document(
                {
                    "id": "base123",
                    "repo_id": repo,
                    "branch": "main",
                    "parent_id": None,
                    "message": "base",
                    "configuration": {"components": {}},
                    "metadata": {},
                }
            )
            mgr.set_head("main", "base123")

            lab = MagicMock()
            lab.get_lab_state.return_value = {
                "components": {
                    "tag_20": {
                        "statecontrol": {
                            "tunables": {"nominal_pose": {"x": 1.0, "y": 2.0, "rotation": 0.0}},
                            "measurables": {},
                        }
                    }
                }
            }
            lab._persist_state = MagicMock()

            runtime_manager = MagicMock()

            result = await apply_post_job_commit_if_needed(
                job_id="job_test",
                spec={
                    "on_success": {
                        "commit_configuration": {
                            "repo_id": repo,
                            "branch": "main",
                            "message": "post-job snapshot",
                        }
                    }
                },
                control_dir=tmp,
                lab=lab,
                runtime_manager=runtime_manager,
                repo_owns_bench=lambda _rid: True,
            )

            self.assertEqual(result["status"], "committed")
            self.assertTrue(result["configuration_id"])
            self.assertEqual(mgr.get_head("main"), result["configuration_id"])


if __name__ == "__main__":
    unittest.main()
