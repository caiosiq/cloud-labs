"""Unit tests for SDK capability-wiki helpers (no server required)."""

import unittest

from cloudlabs.wiki import (
    describe_component_row,
    measurable_script_handle,
    normalize_capabilities,
    tunable_script_handles,
)


class TestSdkWiki(unittest.TestCase):
    def test_normalize_capabilities_nested(self):
        caps = normalize_capabilities(
            {
                "statecontrol": {
                    "tunables": {"nominal_pose": {"widget": "TablePose"}},
                    "measurables": {"camera_image": {"widget": "ImageViewer"}},
                },
                "primitives": ["MOVE_COMPONENT", "RECORD_MEASURABLES"],
            }
        )
        self.assertIn("nominal_pose", caps["statecontrol"]["tunables"])
        self.assertIn("camera_image", caps["statecontrol"]["measurables"])
        self.assertIn("MOVE_COMPONENT", caps["primitives"])

    def test_tunable_handles_pose_axes(self):
        rows = tunable_script_handles("tag_20", "nominal_pose", {"widget": "TablePose"})
        paths = [r["path"] for r in rows]
        self.assertEqual(
            paths,
            [
                "tunables.nominal_pose.x",
                "tunables.nominal_pose.y",
                "tunables.nominal_pose.rotation",
            ],
        )
        self.assertIn("lab.move_component('tag_20'", rows[0]["snippet"])

    def test_measurable_handle_snippet(self):
        row = measurable_script_handle(
            "tag_22", "camera_image", {"widget": "ImageViewer", "format": "png"}
        )
        self.assertEqual(row["path"], "measurables.camera_image")
        self.assertEqual(
            row["snippet"],
            "lab.measurable('tag_22', 'camera_image').resolve(record=True)",
        )

    def test_describe_component_row(self):
        row = {
            "tag_id": "tag_22",
            "name": "Gripper Camera 1",
            "type": "OPTICAL_CAMERA",
            "properties": {"resolution": [1920, 1080]},
            "capabilities": {
                "statecontrol": {
                    "tunables": {
                        "exposure_time_ms": {
                            "widget": "FloatRange",
                            "min": 10,
                            "max": 1000,
                            "unit": "ms",
                        }
                    },
                    "measurables": {"camera_image": {"widget": "ImageViewer", "format": "png"}},
                },
                "primitives": ["RECORD_MEASURABLES", "SET_EXPOSURE"],
            },
        }
        desc = describe_component_row(
            row,
            on_bench=True,
            registries={"tunables": {"exposure_time_ms": {"write_primitive": "SET_EXPOSURE"}}},
        )
        self.assertTrue(desc["on_bench"])
        self.assertEqual(desc["parameters"]["properties.resolution"], [1920, 1080])
        self.assertTrue(any(m["field"] == "camera_image" for m in desc["measurables"]))
        self.assertTrue(any(t["path"] == "tunables.exposure_time_ms" for t in desc["tunables"]))
        self.assertIn("SET_EXPOSURE", desc["primitives"])


if __name__ == "__main__":
    unittest.main()
