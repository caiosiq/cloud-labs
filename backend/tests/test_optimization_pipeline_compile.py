"""Phase 0: ensemble IR → edge optimization_pipeline compile + schema."""
from __future__ import annotations

import json
import unittest
from typing import Any, Dict

from lab_model.execution.optimization.pipeline import (
    PipelineCompileError,
    attach_pipeline,
    compile_pipeline,
    path_to_actuator,
    validate_pipeline_document,
)


def _catalog() -> Dict[str, Any]:
    return {
        "tag_18": {
            "tag_id": "tag_18",
            "type": "OPTICAL_MIRROR",
            "motor_controller": "wifi_stepper1",
            "motor_ids": [1, 3],
        },
        "tag_20": {
            "tag_id": "tag_20",
            "type": "OPTICAL_MIRROR",
            "motor_controller": "wifi_stepper2",
            "motor_ids": [1, 3],
        },
        "tag_9": {
            "tag_id": "tag_9",
            "type": "OPTICAL_LENS",
        },
        "tag_22": {
            "tag_id": "tag_22",
            "type": "OPTICAL_CAMERA",
            "parameters": {
                "hardware_binding": {"backend": "recorder_tcp", "recorder_cam_id": 1}
            },
        },
    }


def _two_mirror_payload() -> Dict[str, Any]:
    """Same shape as ``test_ensemble_optimization_mock._four_mirror_payload`` (subset)."""
    motors = [
        ("v_m20_m1", "tag_20", "1"),
        ("v_m20_m3", "tag_20", "3"),
        ("v_m18_m1", "tag_18", "1"),
        ("v_m18_m3", "tag_18", "3"),
    ]
    variables = [
        {
            "id": vid,
            "tag_id": tag,
            "path": f"tunables.nominal_motor_positions.{mid}",
            "physical_type": "continuous",
            "unit": "deg",
            "bounds": {"min": -3.0, "max": 3.0},
            "delta": True,
        }
        for vid, tag, mid in motors
    ]
    return {
        "mode": "ensemble",
        "session_label": "two-mirror mock",
        "strategy": "NEWTON",  # legacy noise — must be stripped
        "variables": variables,
        "objective": {
            "type": "weighted_sum",
            "minimize": True,
            "terms": [
                {
                    "id": "centroid_rms_px",
                    "weight": 1.0,
                    "source": {
                        "tag_id": "tag_20",
                        "kind": "derived_centroid",
                        "from": "measurables.camera_image",
                        "target_px": {"x": 512.0, "y": 384.0},
                    },
                    "metric": "rms_distance_px",
                },
                {
                    "id": "power_intensity",
                    "weight": 0.35,
                    "source": {
                        "tag_id": "tag_20",
                        "kind": "measurable_scalar",
                        "path": "measurables.last_optimization_score",
                        "normalize": {"min": 0.0, "max": 1.0},
                    },
                    "metric": "one_minus_normalized",
                },
            ],
        },
        "solver": {
            "type": "block_cobyla",
            "max_total_evals": 200,
            "keep_best": True,
            "settle_ms": 0,
            "blocks": [
                {
                    "id": "block_mirrors",
                    "variable_ids": [v["id"] for v in variables],
                    "max_evals": 100,
                    "trust_region_u": 0.3,
                    "rhobeg_u": 0.1,
                    "rhoend_u": 0.001,
                    "passes": 3,
                }
            ],
        },
    }


class PathToActuatorTests(unittest.TestCase):
    def test_motor_path(self) -> None:
        act = path_to_actuator(
            "tunables.nominal_motor_positions.1",
            tag_id="tag_20",
            catalog=_catalog(),
        )
        self.assertEqual(
            act,
            {"kind": "motor", "controller": "wifi_stepper2", "motor_id": 1},
        )

    def test_pose_path(self) -> None:
        act = path_to_actuator(
            "tunables.nominal_pose.y",
            tag_id="tag_9",
            catalog=_catalog(),
        )
        self.assertEqual(act, {"kind": "pose", "axis": "y"})

    def test_unknown_path_raises(self) -> None:
        with self.assertRaises(PipelineCompileError):
            path_to_actuator(
                "tunables.not_a_real_path.x",
                tag_id="tag_9",
                catalog=_catalog(),
            )

    def test_motor_missing_controller_raises(self) -> None:
        with self.assertRaises(PipelineCompileError):
            path_to_actuator(
                "tunables.nominal_motor_positions.1",
                tag_id="tag_9",
                catalog=_catalog(),
            )


class CompilePipelineTests(unittest.TestCase):
    def test_two_mirror_compiles_and_validates(self) -> None:
        pipeline = compile_pipeline(_two_mirror_payload(), _catalog())
        validate_pipeline_document(pipeline)
        # Round-trip through JSON
        round_trip = json.loads(json.dumps(pipeline))
        validate_pipeline_document(round_trip)
        self.assertEqual(round_trip["schema_version"], 1)
        self.assertEqual(round_trip["session_label"], "two-mirror mock")
        self.assertEqual(len(round_trip["variables"]), 4)
        motors = [v for v in round_trip["variables"] if v["actuator"]["kind"] == "motor"]
        self.assertEqual(len(motors), 4)
        self.assertEqual(motors[0]["actuator"]["controller"], "wifi_stepper2")

    def test_two_terms_collapse_to_one_capture(self) -> None:
        payload = _two_mirror_payload()
        # Replace measurable_scalar with a second torchscript/camera term so both
        # need a capture on the same camera.
        payload["objective"]["terms"][1] = {
            "id": "centroid_again",
            "weight": 0.5,
            "source": {
                "tag_id": "tag_20",
                "kind": "derived_centroid",
                "from": "measurables.camera_image",
                "target_px": {"x": 100.0, "y": 200.0},
            },
            "metric": "rms_distance_px",
        }
        pipeline = compile_pipeline(payload, _catalog())
        validate_pipeline_document(pipeline)
        self.assertEqual(len(pipeline["capture"]), 1)
        self.assertEqual(pipeline["capture"][0]["tag_id"], "tag_22")
        self.assertEqual(pipeline["capture"][0]["field"], "camera_image")
        ids = {t["capture_id"] for t in pipeline["objective"]["terms"]}
        self.assertEqual(ids, {pipeline["capture"][0]["id"]})
        for term in pipeline["objective"]["terms"]:
            self.assertEqual(term["kernel_id"], "builtin.roi_centroid")
            self.assertEqual(term["metric"], "rms_distance")

    def test_measurable_scalar_without_kernel(self) -> None:
        pipeline = compile_pipeline(_two_mirror_payload(), _catalog())
        power = next(t for t in pipeline["objective"]["terms"] if t["id"] == "power_intensity")
        self.assertEqual(power["measurable_path"], "measurables.last_optimization_score")
        self.assertNotIn("kernel_id", power)
        self.assertNotIn("capture_id", power)

    def test_pose_variable_compiles(self) -> None:
        payload = {
            "mode": "ensemble",
            "variables": [
                {
                    "id": "v_lens_y",
                    "tag_id": "tag_9",
                    "path": "tunables.nominal_pose.y",
                    "physical_type": "invasive_discrete",
                    "unit": "mm",
                    "bounds": {"min": -8.0, "max": 8.0},
                    "delta": True,
                    "touch_and_go": {
                        "gripper_tag": "tag_9",
                        "measure_only_while_released": True,
                        "settle_ms_after_release": 450,
                    },
                }
            ],
            "objective": {
                "type": "weighted_sum",
                "minimize": True,
                "terms": [
                    {
                        "id": "center",
                        "weight": 1.0,
                        "source": {
                            "tag_id": "tag_22",
                            "kind": "torchscript_features",
                            "kernel_id": "builtin.roi_centroid",
                            "from": "measurables.camera_image",
                            "feature_index": [0, 1],
                            "target_px": {"x": 2744.0, "y": 1836.0},
                        },
                        "metric": "rms_distance",
                    }
                ],
            },
            "solver": {
                "type": "block_cobyla",
                "max_total_evals": 12,
                "blocks": [
                    {"id": "lens", "variable_ids": ["v_lens_y"], "max_evals": 12}
                ],
            },
        }
        pipeline = compile_pipeline(payload, _catalog())
        validate_pipeline_document(pipeline)
        self.assertEqual(pipeline["variables"][0]["actuator"], {"kind": "pose", "axis": "y"})
        self.assertEqual(pipeline["variables"][0]["physical_type"], "invasive_discrete")
        self.assertIn("touch_and_go", pipeline["variables"][0])

    def test_pose_path_coerces_even_when_ui_says_continuous(self) -> None:
        """Defense: mis-tagged continuous pose still becomes invasive for the edge."""
        payload = {
            "mode": "ensemble",
            "variables": [
                {
                    "id": "v_tag_22_x",
                    "tag_id": "tag_22",
                    "path": "tunables.nominal_pose.x",
                    "physical_type": "continuous",
                    "unit": "mm",
                    "bounds": {"min": -5.0, "max": 5.0},
                    "delta": True,
                }
            ],
            "objective": {
                "type": "weighted_sum",
                "minimize": True,
                "terms": [
                    {
                        "id": "center",
                        "weight": 1.0,
                        "source": {
                            "tag_id": "tag_22",
                            "kind": "torchscript_features",
                            "kernel_id": "builtin.roi_centroid",
                            "from": "measurables.camera_image",
                            "feature_index": [0, 1],
                            "target_px": {"x": 100.0, "y": 200.0},
                        },
                        "metric": "rms_distance",
                    }
                ],
            },
            "solver": {
                "type": "block_cobyla",
                "max_total_evals": 8,
                "blocks": [
                    {"id": "pose", "variable_ids": ["v_tag_22_x"], "max_evals": 8}
                ],
            },
        }
        pipeline = compile_pipeline(payload, _catalog())
        validate_pipeline_document(pipeline)
        var = pipeline["variables"][0]
        self.assertEqual(var["actuator"], {"kind": "pose", "axis": "x"})
        self.assertEqual(var["physical_type"], "invasive_discrete")
        self.assertEqual(var["touch_and_go"]["gripper_tag"], "tag_22")

    def test_attach_pipeline_mutates_params(self) -> None:
        params = _two_mirror_payload()
        attach_pipeline(params, catalog=_catalog())
        self.assertIn("pipeline", params)
        validate_pipeline_document(params["pipeline"])

    def test_session_package_ok_premade_rejected(self) -> None:
        with self.assertRaises(PipelineCompileError):
            compile_pipeline(
                _two_mirror_payload(),
                _catalog(),
                kernel_packages=[
                    {
                        "kernel_id": "builtin.roi_centroid",
                        "digest": "sha256:abc",
                        "artifact_b64": "AA==",
                    }
                ],
            )
        pipeline = compile_pipeline(
            _two_mirror_payload(),
            _catalog(),
            kernel_packages=[
                {
                    "kernel_id": "session.mock_default.score.deadbeef",
                    "digest": "sha256:abc",
                    "artifact_b64": "AA==",
                }
            ],
        )
        validate_pipeline_document(pipeline)
        self.assertEqual(len(pipeline["kernel_packages"]), 1)


if __name__ == "__main__":
    unittest.main()
