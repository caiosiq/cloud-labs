"""Unit tests for OPTIMIZE apply_setpoints (tunables-only wave)."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from cloudlabs_edge_dev.optimization.apply_setpoints import (
    SpatialSetpointError,
    apply_setpoints,
    assert_tunables_only_variables,
    parse_setpoint_wave,
)
from cloudlabs_edge_dev.optimization.router import RecordingRouter


class ApplySetpointsTests(unittest.TestCase):
    def test_parse_motors_and_exposure(self) -> None:
        wave = parse_setpoint_wave(
            {
                "tag_m.motor.1": 12.5,
                "tag_cam.exposure_time_ms": 40.0,
                "misc_gain": 1.0,
            }
        )
        self.assertEqual(wave.motors["tag_m.motor.1"], 12.5)
        self.assertEqual(wave.exposure_ms["tag_cam.exposure_time_ms"], 40.0)
        self.assertEqual(wave.other["misc_gain"], 1.0)

    def test_refuse_spatial_keys(self) -> None:
        with self.assertRaises(SpatialSetpointError):
            parse_setpoint_wave({"target_x": 10.0, "target_y": 20.0})
        with self.assertRaises(SpatialSetpointError):
            apply_setpoints({"move_component.A": 1.0})

    def test_router_apply_eval(self) -> None:
        router = RecordingRouter()
        wave = apply_setpoints(
            {"tag_m.motor.1": 3.0},
            router=router,
            block_id="wave0",
        )
        self.assertEqual(wave.motors["tag_m.motor.1"], 3.0)
        self.assertEqual(router.last_applied, {"tag_m.motor.1": 3.0})

    def test_refuse_pose_variables(self) -> None:
        pose = SimpleNamespace(
            id="x_mm",
            actuator=SimpleNamespace(kind="pose"),
        )
        motor = SimpleNamespace(
            id="m1",
            actuator=SimpleNamespace(kind="motor"),
        )
        assert_tunables_only_variables([motor])
        with self.assertRaises(SpatialSetpointError):
            assert_tunables_only_variables([pose, motor])


if __name__ == "__main__":
    unittest.main()
