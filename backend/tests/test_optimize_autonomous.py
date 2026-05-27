"""Autonomous OPTIMIZE (mock) — OPTIMIZING lock, live pose, tunables-only commit."""
from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lab_communicator.mock.communicator import MockLabCommunicator
from lab_communicator.shared.lab_view_config import bootstrap_lab_view
from lab_model.domain.component import get_measurables, get_tunables
from lab_model.domain.holding import SYSTEM_STATUS_IDLE, SYSTEM_STATUS_OPTIMIZING
from lab_model.orchestration.cobyla_reference import refuse_if_cobyla_without_reference
from lab_model.orchestration.optimize_policy import refuse_if_optimize_strategy_not_allowed


def _make_mock_lab(tmp: Path) -> MockLabCommunicator:
    lv = tmp / "lab_view"
    src = Path(__file__).resolve().parents[1] / "lab_communicator" / "mock" / "lab_view"
    shutil.copytree(src, lv)
    os.environ["LAB_VIEW_PATH"] = str(lv)
    project_root = Path(__file__).resolve().parents[1]
    bootstrap_lab_view(str(project_root))
    from lab_model import motor_rotation_store as motor_rot

    motor_rot.configure(str(lv / "motor_rotations.json"))
    return MockLabCommunicator()


class TestAutonomousOptimize(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.lab = _make_mock_lab(Path(self._tmpdir.name))
        self.target = "tag_18"
        self.sensor = "tag_22"

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_optimize_uses_optimizing_and_commits_tunables_only(self) -> None:
        lab = self.lab
        target = self.target
        with lab._state_lock:
            comp = lab.current_state["components"][target]
            tun = get_tunables(comp)
            tun["nominal_pose"] = {"x": 100.0, "y": 200.0, "rotation": 5.0}
            tun["placement"] = {"mode": "NEWTON"}

        async def fast_optimize(*, target_id, strategy_name, params, live_pose_callback):
            live_pose_callback(pose={"x": 100.0, "y": 200.0, "rotation": 7.0})
            return {"score": 0.95, "final_pose": {"x": 100.0, "y": 200.0, "rotation": 7.5}}

        async def run() -> None:
            with patch.object(lab, "_primitive_optimize_component", side_effect=fast_optimize):
                await lab.optimize_component(
                    target, "NEWTON", {"sensor_component": self.sensor}
                )

        asyncio.run(run())

        with lab._state_lock:
            st = lab.current_state
            self.assertEqual(st["system_status"], SYSTEM_STATUS_IDLE)
            self.assertIsNone(st.get("optimization_target_id"))
            comp = st["components"][target]
            tun = get_tunables(comp)
            meas = get_measurables(comp)
            self.assertAlmostEqual(tun["nominal_pose"]["rotation"], 7.5)
            self.assertEqual(tun["placement"]["mode"], "MANUAL")
            self.assertAlmostEqual(meas["last_optimization_score"], 0.95)
            self.assertIsNone(meas.get("last_optimized_pose"))

    def test_live_pose_available_during_optimize(self) -> None:
        lab = self.lab
        target = self.target
        seen: dict = {}

        async def slow_optimize(*, target_id, strategy_name, params, live_pose_callback):
            with lab._state_lock:
                seen["status"] = lab.current_state.get("system_status")
            live_pose_callback(pose={"x": 1.0, "y": 2.0, "rotation": 3.0})
            seen["during"] = lab.get_teleop_live_pose(target_id)
            await asyncio.sleep(0.05)
            return {"score": 0.5, "final_pose": {"x": 1.0, "y": 2.0, "rotation": 4.0}}

        async def run() -> None:
            with patch.object(lab, "_primitive_optimize_component", side_effect=slow_optimize):
                await lab.optimize_component(target, "NEWTON", {})

        asyncio.run(run())
        self.assertEqual(seen.get("status"), SYSTEM_STATUS_OPTIMIZING)
        self.assertAlmostEqual(seen["during"]["rotation"], 3.0)

    def test_cobyla_refused_without_reference(self) -> None:
        lab = self.lab
        target = self.target

        with lab._state_lock:
            lab.current_state.pop("optimization_reference", None)
            cam = lab.current_state["components"].get(self.sensor)
            if isinstance(cam, dict):
                tun = cam.get("statecontrol", {}).get("tunables", {})
                if isinstance(tun, dict):
                    tun.pop("optimization_reference", None)

        async def should_not_run(**kwargs):
            raise AssertionError("COBYLA primitive should not run without reference")

        async def run() -> None:
            with patch.object(lab, "_primitive_optimize_component", side_effect=should_not_run):
                await lab.optimize_component(
                    target,
                    "COBYLA",
                    {"sensor_component": self.sensor},
                )

        asyncio.run(run())

        with lab._state_lock:
            self.assertEqual(lab.current_state["system_status"], SYSTEM_STATUS_IDLE)

    def test_cobyla_allowed_with_pinned_reference(self) -> None:
        lab = self.lab
        target = self.target
        ref_path = Path(self._tmpdir.name) / "cobyla_ref.png"
        ref_path.write_bytes(b"\x89PNG\r\n")

        with lab._state_lock:
            lab.current_state["optimization_reference"] = {
                "path": str(ref_path),
                "format": "png",
                "source": "test",
            }

        ran = {"ok": False}

        async def fast_optimize(*, target_id, strategy_name, params, live_pose_callback):
            ran["ok"] = True
            return {"score": 0.9, "final_pose": {"x": 1.0, "y": 2.0, "rotation": 0.0}}

        async def run() -> None:
            with patch.object(lab, "_primitive_optimize_component", side_effect=fast_optimize):
                await lab.optimize_component(
                    target,
                    "COBYLA",
                    {"sensor_component": self.sensor},
                )

        asyncio.run(run())
        self.assertTrue(ran["ok"])

    def test_cobyla_live_motor_positions_during_run(self) -> None:
        lab = self.lab
        target = self.target
        ref_path = Path(self._tmpdir.name) / "cobyla_ref.png"
        ref_path.write_bytes(b"\x89PNG\r\n")

        with lab._state_lock:
            lab.current_state["optimization_reference"] = {
                "path": str(ref_path),
                "format": "png",
                "source": "test",
            }
            comp = lab.current_state["components"][target]
            tun = get_tunables(comp)
            tun.setdefault("nominal_motor_positions", {"1": 0.0, "3": 0.0})

        seen: list = []

        async def cobyla_with_capture(*, target_id, strategy_name, params, live_pose_callback):
            def wrapping_callback(**kwargs):
                live_pose_callback(**kwargs)
                live = lab.get_teleop_live_pose(target_id)
                if live:
                    seen.append(dict(live))

            from lab_communicator.mock import primitives as mp

            return await mp.primitive_optimize_component(
                lab,
                target_id=target_id,
                strategy_name=strategy_name,
                params=params,
                live_pose_callback=wrapping_callback,
            )

        async def run() -> None:
            with patch.object(lab, "_primitive_optimize_component", side_effect=cobyla_with_capture):
                await lab.optimize_component(
                    target,
                    "COBYLA",
                    {"sensor_component": self.sensor},
                )

        asyncio.run(run())

        self.assertTrue(seen, "expected live motor telemetry during COBYLA")
        last = seen[-1]
        self.assertIn("motor_positions", last)
        self.assertIn("1", last["motor_positions"])
        self.assertIn("loss", last)

    def test_cobyla_refusal_helper(self) -> None:
        lab = self.lab
        with lab._state_lock:
            state = lab.current_state
            state.pop("optimization_reference", None)
            cam = state["components"].get(self.sensor)
            if isinstance(cam, dict):
                tun = cam.get("statecontrol", {}).get("tunables", {})
                if isinstance(tun, dict):
                    tun.pop("optimization_reference", None)
        refusal = refuse_if_cobyla_without_reference(
            state,
            "COBYLA",
            {"sensor_component": self.sensor},
            catalog_map=lab.catalog_map,
        )
        self.assertTrue(refusal)

    def test_newton_allowed_on_non_motor_placeable(self) -> None:
        lab = self.lab
        target = "tag_9"
        ran = {"ok": False}

        async def fast_optimize(*, target_id, strategy_name, params, live_pose_callback):
            ran["ok"] = True
            return {"score": 0.8, "final_pose": {"x": 1.0, "y": 2.0, "rotation": 0.0}}

        async def run() -> None:
            with patch.object(lab, "_primitive_optimize_component", side_effect=fast_optimize):
                await lab.optimize_component(
                    target,
                    "NEWTON",
                    {"sensor_component": self.sensor},
                )

        asyncio.run(run())
        self.assertTrue(ran["ok"])

    def test_cobyla_refused_on_non_motor_placeable(self) -> None:
        lab = self.lab
        target = "tag_9"

        async def should_not_run(**kwargs):
            raise AssertionError("COBYLA should not run on non-motor component")

        async def run() -> None:
            with patch.object(lab, "_primitive_optimize_component", side_effect=should_not_run):
                await lab.optimize_component(
                    target,
                    "COBYLA",
                    {"sensor_component": self.sensor},
                )

        asyncio.run(run())

        with lab._state_lock:
            self.assertEqual(lab.current_state["system_status"], SYSTEM_STATUS_IDLE)

    def test_optimize_policy_helper(self) -> None:
        lab = self.lab
        row = lab.catalog_map["tag_9"]
        refusal = refuse_if_optimize_strategy_not_allowed(row, "tag_9", "COBYLA")
        self.assertTrue(refusal)
        ok_newton = refuse_if_optimize_strategy_not_allowed(row, "tag_9", "NEWTON")
        self.assertFalse(ok_newton)


if __name__ == "__main__":
    unittest.main()
