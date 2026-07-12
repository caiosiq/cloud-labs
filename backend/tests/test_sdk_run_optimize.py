"""Unit tests for run_optimize / ensemble intent builders."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from lab_model.optimization.sdk.client import CloudLabsClient, KernelMatchSpec, VariableSpec
from lab_model.optimization.sdk.intent import (
    build_cobyla_ensemble,
    build_ensemble_parameters,
    collect_kernel_ids,
)
from lab_model.optimization.sdk.objective import ObjectiveGraphBuilder


class IntentBuilderTests(unittest.TestCase):
    def test_collect_kernel_ids_from_terms_and_extra(self) -> None:
        obj = {
            "terms": [
                {"source": {"kernel_id": "session.a"}},
                {"source": {"kernel_id": "session.a"}},
                {"source": {"kernel_id": "demo.image_mean_score"}},
            ]
        }
        ids = collect_kernel_ids(obj, extra=["session.b", "session.a"])
        self.assertEqual(
            ids, ["session.a", "demo.image_mean_score", "session.b"]
        )

    def test_build_ensemble_parameters_feature_terms(self) -> None:
        params = build_ensemble_parameters(
            variables=[
                VariableSpec(
                    tag_id="tag_20",
                    path="tunables.nominal_motor_positions.1",
                    bounds=(-1.0, 1.0),
                    delta=True,
                )
            ],
            objective={
                "type": "weighted_sum",
                "minimize": True,
                "terms": [
                    {
                        "id": "match_brightness",
                        "weight": 1.0,
                        "metric": "squared_error",
                        "source": {
                            "tag_id": "tag_22",
                            "kind": "torchscript_features",
                            "kernel_id": "session.feat",
                            "from": "measurables.camera_image",
                            "feature_index": 0,
                            "target_scalar": 0.4,
                        },
                    }
                ],
            },
            max_evals=8,
            session_label="feat -> COBYLA",
        )
        self.assertEqual(params["mode"], "ensemble")
        self.assertEqual(params["kernels"], ["session.feat"])
        self.assertEqual(params["session_label"], "feat -> COBYLA")
        self.assertEqual(params["solver"]["type"], "block_cobyla")
        self.assertEqual(params["solver"]["max_total_evals"], 8)

    def test_build_cobyla_ensemble_thin(self) -> None:
        params = build_cobyla_ensemble(
            variables=[
                {
                    "id": "v0",
                    "tag_id": "tag_20",
                    "path": "tunables.nominal_motor_positions.1",
                    "physical_type": "continuous",
                    "unit": "deg",
                    "bounds": {"min": -1.0, "max": 1.0},
                    "delta": True,
                }
            ],
            match_kernel=KernelMatchSpec(
                tag_id="tag_22",
                field="camera_image",
                kernel_id="session.score",
                target=0.35,
            ),
            max_evals=5,
        )
        self.assertEqual(params["kernels"], ["session.score"])
        self.assertEqual(len(params["objective"]["terms"]), 1)


class RunOptimizeClientTests(unittest.TestCase):
    def test_run_optimize_exports_session_packages_and_submits(self) -> None:
        client = CloudLabsClient("mock.default", base_url="http://test")
        client._lease = MagicMock()
        client._lease.lease_id = "lease_test"
        client._released = False

        packages = [
            {
                "kernel_id": "session.feat",
                "artifact_b64": "abc",
                "output_kind": "features",
            }
        ]
        submitted = {"job_id": "job_1", "status": "queued"}
        finished = {"job_id": "job_1", "status": "succeeded", "result": {}}

        graph = (
            ObjectiveGraphBuilder()
            .term(
                term_id="match_brightness",
                weight=1.0,
                metric="squared_error",
                source={
                    "tag_id": "tag_22",
                    "kind": "torchscript_features",
                    "kernel_id": "session.feat",
                    "from": "measurables.camera_image",
                    "feature_index": 0,
                    "target_scalar": 0.4,
                },
            )
        )

        with patch.object(
            client, "export_session_kernel_packages", return_value=packages
        ) as export_mock, patch.object(
            client, "release_lease"
        ) as release_mock, patch.object(
            client, "optimize", return_value=submitted
        ) as opt_mock, patch(
            "lab_model.optimization.sdk.jobs.wait_for_job", return_value=finished
        ), patch.object(
            client, "acquire_lease"
        ) as acquire_mock:
            client._lease = MagicMock()
            client._released = False
            # After release_lease in run_optimize, lease becomes None for reacquire.
            def _release() -> None:
                client._lease = None
                client._released = True

            release_mock.side_effect = _release

            out = client.run_optimize(
                variables=[
                    VariableSpec(
                        tag_id="tag_20",
                        path="tunables.nominal_motor_positions.1",
                        bounds=(-1.0, 1.0),
                        delta=True,
                    )
                ],
                objective=graph,
                max_evals=4,
                session_label="test features",
            )

        self.assertEqual(out["status"], "succeeded")
        export_mock.assert_called_once()
        release_mock.assert_called_once()
        acquire_mock.assert_called_once()
        kwargs = opt_mock.call_args.kwargs
        self.assertEqual(kwargs.get("kernel_packages"), packages)
        self.assertIn("session.feat", kwargs.get("kernels") or [])

    def test_run_cobyla_delegates_to_run_optimize(self) -> None:
        client = CloudLabsClient("mock.default", base_url="http://test")
        with patch.object(client, "run_optimize", return_value={"status": "succeeded"}) as mock_ro:
            out = client.run_cobyla(
                variables=[
                    VariableSpec(
                        tag_id="tag_20",
                        path="tunables.nominal_motor_positions.1",
                        bounds=(-1.0, 1.0),
                    )
                ],
                match_kernel=KernelMatchSpec(
                    tag_id="tag_22",
                    field="camera_image",
                    kernel_id="demo.image_mean_score",
                    target=0.35,
                ),
                max_evals=3,
            )
        self.assertEqual(out["status"], "succeeded")
        mock_ro.assert_called_once()
        call_kw = mock_ro.call_args.kwargs
        self.assertEqual(call_kw["max_evals"], 3)
        self.assertIn("terms", call_kw["objective"])

    def test_export_prefers_register_cache(self) -> None:
        client = CloudLabsClient("mock.default", base_url="http://test")
        kid = "session.mock_default.feat.abc"
        client._session_kernel_cache[kid] = {
            "kernel_id": kid,
            "artifact_b64": "cached_b64",
            "output_kind": "features",
            "feature_names": ["a", "b"],
        }
        with patch.object(client, "_get_json") as get_mock:
            pkgs = client.export_session_kernel_packages([kid])
        get_mock.assert_not_called()
        self.assertEqual(len(pkgs), 1)
        self.assertEqual(pkgs[0]["artifact_b64"], "cached_b64")


if __name__ == "__main__":
    unittest.main()
