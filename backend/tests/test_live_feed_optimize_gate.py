"""Refuse ensemble OPTIMIZE when a capture/camera tag still has live feed on."""
from __future__ import annotations

import unittest

from lab_model.execution.optimization.errors import EnsemblePreflightError
from lab_model.execution.optimization.preflight import preflight_live_feed_conflicts
from lab_model.execution.optimization.spec import OptimizeEnsembleParameters
from lab_model.language.domain.component import ensure_component_shape


def _spec(camera_tag: str = "tag_22") -> OptimizeEnsembleParameters:
    return OptimizeEnsembleParameters.model_validate(
        {
            "session_label": "live_feed_gate",
            "variables": [
                {
                    "id": "m1",
                    "tag_id": "tag_20",
                    "path": "tunables.nominal_motor_positions.1",
                    "physical_type": "continuous",
                    "unit": "deg",
                    "bounds": {"min": -2.0, "max": 2.0},
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
                            "tag_id": camera_tag,
                            "kind": "measurable_scalar",
                            "path": "measurables.last_optimization_score",
                        },
                    }
                ],
            },
            "solver": {
                "type": "block_cobyla",
                "max_total_evals": 8,
                "blocks": [
                    {"id": "b0", "variable_ids": ["m1"], "max_evals": 8, "passes": 1}
                ],
            },
            "capture": {
                "before_each_eval": [
                    {"tag_id": camera_tag, "kind": "camera_frame", "field": "camera_image"}
                ],
            },
        }
    )


def _state(*, live: bool) -> dict:
    cam = {
        "tag_id": "tag_22",
        "type": "OPTICAL_CAMERA",
        "statecontrol": {
            "tunables": {},
            "measurables": {"last_optimization_score": 0.5},
        },
        "telemetry": {
            "live_feed": {
                "stream": {
                    "live": live,
                    "connected": live,
                    "backend": "edge",
                }
            }
        },
    }
    motor = {
        "tag_id": "tag_20",
        "type": "OPTICAL_MIRROR",
        "statecontrol": {
            "tunables": {"nominal_motor_positions": {"1": 0.0}},
            "measurables": {},
        },
    }
    ensure_component_shape(cam)
    ensure_component_shape(motor)
    return {"components": {"tag_22": cam, "tag_20": motor}}


class LiveFeedOptimizeGateTests(unittest.TestCase):
    def test_conflict_when_capture_camera_live(self) -> None:
        catalog = {
            "tag_22": {"tag_id": "tag_22", "type": "OPTICAL_CAMERA", "name": "cam"},
            "tag_20": {"tag_id": "tag_20", "type": "OPTICAL_MIRROR", "name": "m"},
        }
        with self.assertRaises(EnsemblePreflightError) as ctx:
            preflight_live_feed_conflicts(
                _state(live=True), _spec(), catalog_map=catalog
            )
        self.assertIn("live feed", ctx.exception.message.lower())

    def test_ok_when_live_feed_off(self) -> None:
        catalog = {
            "tag_22": {"tag_id": "tag_22", "type": "OPTICAL_CAMERA", "name": "cam"},
            "tag_20": {"tag_id": "tag_20", "type": "OPTICAL_MIRROR", "name": "m"},
        }
        preflight_live_feed_conflicts(
            _state(live=False), _spec(), catalog_map=catalog
        )


if __name__ == "__main__":
    unittest.main()
