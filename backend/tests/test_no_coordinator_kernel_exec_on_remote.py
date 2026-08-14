"""Phase 3: remote backends must not run coordinator TorchScript in collect_objective_measurements."""
from __future__ import annotations

import unittest
from unittest import mock

from lab_model.execution.optimization.objective_measurements import (
    collect_objective_measurements,
)
from lab_model.execution.optimization.spec import ObjectiveSpec


class NoCoordinatorKernelExecTests(unittest.TestCase):
    def test_remote_flag_refuses_torchscript(self) -> None:
        objective = ObjectiveSpec.model_validate(
            {
                "terms": [
                    {
                        "id": "t0",
                        "weight": 1.0,
                        "metric": "one_minus_normalized",
                        "source": {
                            "kind": "torchscript_scalar",
                            "tag_id": "tag_20",
                            "kernel_id": "demo.image_mean_score",
                            "from": "measurables.camera_image",
                            "normalize": {"min": 0.0, "max": 1.0},
                        },
                    }
                ]
            }
        )
        state = {
            "components": {
                "tag_20": {
                    "statecontrol": {"measurables": {"camera_image": None}},
                }
            }
        }

        with mock.patch(
            "lab_model.execution.optimization.kernels.torchscript_runtime.run_torchscript_output"
        ) as run_ts:
            with self.assertRaises(RuntimeError) as ctx:
                collect_objective_measurements(
                    objective,
                    state=state,
                    capture_bgr_for_tag=lambda _t: object(),
                    allow_coordinator_torchscript=False,
                )
            self.assertIn("remote", str(ctx.exception).lower())
            run_ts.assert_not_called()


if __name__ == "__main__":
    unittest.main()
