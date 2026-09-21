import contextlib
import json
import unittest
from pathlib import Path

import mujoco
import numpy as np

from simulation_edge.bootstrap import _resolve_catalog_rows
from simulation_edge.host.runtime import (
    MUJOCO_RULER_HEIGHT_FROM_COMPONENT_BOTTOM_M,
    MUJOCO_RULER_RGBA,
    MuJoCoRobotRuntime,
)
from simulation_edge.host.scene import build_scene_spec


LAB_VIEW = Path(__file__).resolve().parents[1] / "lab_view"


class FakeViewer:
    def __init__(self, model: mujoco.MjModel):
        self.user_scn = mujoco.MjvScene(model, maxgeom=100)
        self.perturb = mujoco.MjvPerturb()
        self.cam = mujoco.MjvCamera()
        mujoco.mjv_defaultCamera(self.cam)
        self.cam.lookat[:] = (0.0, 0.0, 0.0)
        self.cam.distance = 3.0
        self.cam.azimuth = 135.0
        self.cam.elevation = -10.0
        self.viewport = mujoco.MjrRect(0, 0, 1280, 720)

    def lock(self):
        return contextlib.nullcontext()


def make_runtime() -> MuJoCoRobotRuntime:
    layout = json.loads((LAB_VIEW / "layout.json").read_text(encoding="utf-8"))
    state = json.loads((LAB_VIEW / "lab_state.json").read_text(encoding="utf-8"))
    rows = _resolve_catalog_rows(LAB_VIEW)
    scene = build_scene_spec(
        layout,
        rows,
        state,
        profile_id="optical_housings",
    )
    runtime = MuJoCoRobotRuntime.__new__(MuJoCoRobotRuntime)
    runtime.scene = scene
    runtime.model = mujoco.MjModel.from_xml_string(scene.xml)
    runtime.data = mujoco.MjData(runtime.model)
    mujoco.mj_forward(runtime.model, runtime.data)
    runtime._component_body_ids = {
        tag: runtime.model.body(spec.body_name).id
        for tag, spec in scene.components.items()
    }
    runtime._viewer_entered = FakeViewer(runtime.model)
    runtime._component_labels_visible = False
    runtime._measurement_mark_requested = False
    runtime._measurement_clear_requested = False
    runtime._measurement_tags = []
    return runtime


class ViewerOverlayTests(unittest.TestCase):
    def test_known_viewer_font_widths_match_mujoco_150_percent_font(self):
        self.assertEqual(MuJoCoRobotRuntime._viewer_text_width_px("Main Lens"), 100)
        self.assertEqual(
            MuJoCoRobotRuntime._viewer_text_width_px("Gripper Camera 1"),
            171,
        )
        self.assertEqual(MuJoCoRobotRuntime._viewer_text_width_px("100.0 mm"), 94)

    def test_label_anchor_is_shifted_left_by_half_its_screen_width(self):
        runtime = make_runtime()
        original = np.array((0.1, -0.2, 0.3))
        text = "Main Lens"
        centered = runtime._centered_viewer_label_position(original, text)

        head = np.zeros(3)
        forward = np.zeros(3)
        up = np.zeros(3)
        right = np.zeros(3)
        mujoco.mjv_cameraFrame(
            head,
            forward,
            up,
            right,
            runtime.data,
            runtime._viewer_entered.cam,
        )
        depth = float(np.dot(original - head, forward))
        world_per_pixel = (
            2.0
            * depth
            * np.tan(np.deg2rad(runtime.model.vis.global_.fovy) / 2.0)
            / runtime._viewer_entered.viewport.height
        )
        shift_px = float(np.dot(original - centered, right) / world_per_pixel)
        self.assertAlmostEqual(
            shift_px,
            MuJoCoRobotRuntime._viewer_text_width_px(text) / 2.0,
            places=6,
        )

    def test_high_resolution_capture_uses_larger_font(self):
        runtime = make_runtime()

        self.assertEqual(
            runtime._capture_font_scale(2160),
            mujoco.mjtFontScale.mjFONTSCALE_300,
        )
        self.assertEqual(
            runtime._capture_font_scale(720),
            mujoco.mjtFontScale.mjFONTSCALE_150,
        )

    def test_nearest_resize_enlarges_overlay_pixels_without_blending(self):
        source = np.array(
            [
                [[1, 2, 3], [4, 5, 6]],
                [[7, 8, 9], [10, 11, 12]],
            ],
            dtype=np.uint8,
        )

        enlarged = MuJoCoRobotRuntime._resize_nearest(
            source,
            height=4,
            width=4,
        )

        expected = np.repeat(np.repeat(source, 2, axis=0), 2, axis=1)
        np.testing.assert_array_equal(enlarged, expected)

    def test_capture_label_remains_centered_with_larger_font(self):
        runtime = make_runtime()
        runtime._measurement_tags = ["tag_9", "tag_20"]
        capture_scene = mujoco.MjvScene(runtime.model, maxgeom=100)

        runtime._append_viewer_overlays_to_scene(
            capture_scene,
            capture_height=2160,
            capture_font_scale_percent=300,
        )

        label = next(
            capture_scene.geoms[index]
            for index in range(capture_scene.ngeom)
            if capture_scene.geoms[index].type == mujoco.mjtGeom.mjGEOM_LABEL
        )
        first = runtime._ruler_endpoint("tag_9")
        second = runtime._ruler_endpoint("tag_20")
        expected_center = (first + second) / 2.0
        expected_center[2] += 0.025
        expected = expected_center - runtime._label_half_width_world(
            expected_center,
            label.label,
            camera=runtime._viewer_entered.cam,
            viewport_height=2160,
            font_scale_percent=300,
        )
        np.testing.assert_allclose(label.pos, expected, atol=1e-9)

    def test_scene_preserves_catalog_component_names(self):
        runtime = make_runtime()
        self.assertEqual(runtime.scene.components["tag_9"].display_name, "150mm Lens")
        self.assertEqual(
            runtime.scene.components["tag_20"].display_name,
            "Front Polarizer",
        )

    def test_labels_include_components_only(self):
        runtime = make_runtime()
        runtime._component_labels_visible = True
        runtime._refresh_viewer_overlays()
        scene = runtime._viewer_entered.user_scn
        labels = {scene.geoms[index].label for index in range(scene.ngeom)}
        expected = {spec.display_name for spec in runtime.scene.components.values()}
        self.assertEqual(labels, expected)
        self.assertNotIn("joint1", labels)
        self.assertNotIn("link_base", labels)

    def test_selection_rejects_noncomponents_and_duplicate_component(self):
        runtime = make_runtime()
        runtime._viewer_entered.perturb.select = runtime.model.body("link_base").id
        runtime._measurement_mark_requested = True
        runtime._handle_viewer_measurement_requests()
        self.assertEqual(runtime._measurement_tags, [])

        first_tag = "tag_9"
        runtime._viewer_entered.perturb.select = runtime._component_body_ids[first_tag]
        runtime._measurement_mark_requested = True
        runtime._handle_viewer_measurement_requests()
        runtime._measurement_mark_requested = True
        runtime._handle_viewer_measurement_requests()
        self.assertEqual(runtime._measurement_tags, [first_tag])

    def test_completed_ruler_is_retained_when_next_ruler_starts(self):
        runtime = make_runtime()
        runtime._measurement_tags = ["tag_9", "tag_20"]
        runtime._viewer_entered.perturb.select = runtime._component_body_ids["tag_11"]
        runtime._measurement_mark_requested = True

        runtime._handle_viewer_measurement_requests()

        self.assertEqual(runtime._measurement_tags, ["tag_9", "tag_20", "tag_11"])

    def test_multiple_rulers_render_and_clear_together(self):
        runtime = make_runtime()
        runtime._measurement_tags = ["tag_9", "tag_20", "tag_11", "tag_14"]
        runtime._refresh_viewer_overlays()
        scene = runtime._viewer_entered.user_scn

        self.assertEqual(scene.ngeom, 4)
        self.assertEqual(scene.geoms[0].type, mujoco.mjtGeom.mjGEOM_LINE)
        self.assertEqual(scene.geoms[1].type, mujoco.mjtGeom.mjGEOM_LABEL)
        self.assertEqual(scene.geoms[2].type, mujoco.mjtGeom.mjGEOM_LINE)
        self.assertEqual(scene.geoms[3].type, mujoco.mjtGeom.mjGEOM_LABEL)
        np.testing.assert_allclose(scene.geoms[0].rgba, MUJOCO_RULER_RGBA)
        np.testing.assert_allclose(scene.geoms[2].rgba, MUJOCO_RULER_RGBA)

        runtime._measurement_clear_requested = True
        runtime._handle_viewer_measurement_requests()
        runtime._refresh_viewer_overlays()
        self.assertEqual(runtime._measurement_tags, [])
        self.assertEqual(scene.ngeom, 0)

    def test_active_overlays_are_copied_into_capture_scene(self):
        runtime = make_runtime()
        runtime._component_labels_visible = True
        runtime._measurement_tags = ["tag_9", "tag_20"]
        capture_scene = mujoco.MjvScene(runtime.model, maxgeom=100)
        mujoco.mjv_initGeom(
            capture_scene.geoms[0],
            mujoco.mjtGeom.mjGEOM_SPHERE,
            np.ones(3),
            np.zeros(3),
            np.eye(3).reshape(-1),
            np.ones(4, dtype=np.float32),
        )
        capture_scene.ngeom = 1

        copied = runtime._append_viewer_overlays_to_scene(capture_scene)

        self.assertEqual(copied, len(runtime.scene.components) + 2)
        self.assertEqual(capture_scene.geoms[0].type, mujoco.mjtGeom.mjGEOM_SPHERE)
        copied_geoms = [
            capture_scene.geoms[index]
            for index in range(1, capture_scene.ngeom)
        ]
        labels = {geom.label for geom in copied_geoms if geom.label}
        expected_labels = {
            spec.display_name for spec in runtime.scene.components.values()
        }
        self.assertTrue(expected_labels.issubset(labels))
        self.assertTrue(any(label.endswith(" mm") for label in labels))
        self.assertEqual(
            sum(geom.type == mujoco.mjtGeom.mjGEOM_LINE for geom in copied_geoms),
            1,
        )

    def test_ruler_tracks_optic_height_and_reports_millimetres(self):
        runtime = make_runtime()
        first_tag, second_tag = "tag_9", "tag_20"
        runtime._measurement_tags = [first_tag, second_tag]
        runtime._refresh_viewer_overlays()
        scene = runtime._viewer_entered.user_scn
        self.assertEqual(scene.ngeom, 2)
        line, label = scene.geoms[0], scene.geoms[1]
        self.assertEqual(line.type, mujoco.mjtGeom.mjGEOM_LINE)
        self.assertEqual(label.type, mujoco.mjtGeom.mjGEOM_LABEL)
        np.testing.assert_allclose(line.rgba, MUJOCO_RULER_RGBA)

        first = runtime._ruler_endpoint(first_tag)
        second = runtime._ruler_endpoint(second_tag)
        first_body_center = runtime.data.body(
            runtime.scene.components[first_tag].body_name
        ).xpos
        self.assertAlmostEqual(first[0], first_body_center[0])
        self.assertAlmostEqual(first[1], first_body_center[1])
        self.assertAlmostEqual(
            first[2],
            first_body_center[2]
            - runtime.scene.components[first_tag].height_m / 2.0
            + MUJOCO_RULER_HEIGHT_FROM_COMPONENT_BOTTOM_M,
        )
        expected_mm = float(np.linalg.norm(second - first) * 1000.0)
        self.assertEqual(label.label, f"{expected_mm:.1f} mm")
        np.testing.assert_allclose(line.pos, first, atol=1e-9)
        np.testing.assert_allclose(line.size[2], expected_mm / 1000.0, atol=1e-9)

        joint_id = runtime.model.joint(
            runtime.scene.components[second_tag].joint_name
        ).id
        qpos_address = int(runtime.model.jnt_qposadr[joint_id])
        runtime.data.qpos[qpos_address : qpos_address + 3] += (0.123, -0.045, 0.080)
        mujoco.mj_forward(runtime.model, runtime.data)
        runtime._refresh_viewer_overlays()
        moved_second = runtime._ruler_endpoint(second_tag)
        moved_mm = float(np.linalg.norm(moved_second - first) * 1000.0)
        self.assertEqual(scene.geoms[1].label, f"{moved_mm:.1f} mm")


if __name__ == "__main__":
    unittest.main()
