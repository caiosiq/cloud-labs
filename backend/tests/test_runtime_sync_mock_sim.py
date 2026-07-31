"""Phase 2: mock/sim SYNC_RUNTIME boot + READY gate."""
from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import unittest
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_MOCK_LAB_VIEW = _PROJECT_ROOT / "mock_backend" / "lab_view"


class MockRuntimeSyncTests(unittest.TestCase):
    def test_boot_sync_marks_ready_and_gates_motion(self) -> None:
        from lab_model.coordinator.backends.lab_view_config import (
            bootstrap_lab_view,
            get_lab_view_paths,
        )
        from lab_model.language.domain import motor_rotation_store as motor_rot
        from mock_backend.host.communicator import MockLabCommunicator

        os.environ.pop("CLOUDLABS_SKIP_RUNTIME_SYNC", None)
        os.environ["CLOUDLABS_MOCK_INIT_DELAY_S"] = "0"
        with tempfile.TemporaryDirectory() as tmp:
            lab_view = Path(tmp) / "lab_view"
            shutil.copytree(_MOCK_LAB_VIEW, lab_view)
            os.environ["LAB_VIEW_PATH"] = str(lab_view)
            bootstrap_lab_view(str(_PROJECT_ROOT))
            motor_rot.configure(get_lab_view_paths().motor_rotations_json)

            lab = MockLabCommunicator()
            self.assertTrue(lab.is_runtime_ready())
            self.assertEqual(lab.get_lab_state()["runtime_sync"]["status"], "ready")

            # Edge dispatch gate
            import sys

            edge = _PROJECT_ROOT / "mock_backend" / "cloudlabs_edge"
            sys.path.insert(0, str(edge))
            try:
                from adapters import context as edge_context
                from adapters.runtime_ready import ensure_runtime_ready
                from dispatch import dispatch_primitive

                edge_context.bind_lab(lab)
                # READY: MOVE allowed
                # (may refuse for other reasons; gate itself must not fire)
                ensure_runtime_ready("MOVE_COMPONENT")

                lab.set_runtime_sync_status("pending")
                with self.assertRaises(RuntimeError) as ctx:
                    asyncio.run(
                        dispatch_primitive("MOVE_COMPONENT", {"tag_id": "tag_22"})
                    )
                self.assertIn("runtime_sync", str(ctx.exception))
            finally:
                if str(edge) in sys.path:
                    sys.path.remove(str(edge))
                os.environ.pop("CLOUDLABS_MOCK_INIT_DELAY_S", None)

    def test_boot_sync_schedules_on_running_loop(self) -> None:
        """Coordinator constructs mock inside uvicorn's loop — must not stick pending."""
        from lab_model.coordinator.backends.lab_view_config import (
            bootstrap_lab_view,
            get_lab_view_paths,
        )
        from lab_model.language.domain import motor_rotation_store as motor_rot
        from mock_backend.host.communicator import MockLabCommunicator

        os.environ.pop("CLOUDLABS_SKIP_RUNTIME_SYNC", None)
        os.environ["CLOUDLABS_MOCK_INIT_DELAY_S"] = "0"
        with tempfile.TemporaryDirectory() as tmp:
            lab_view = Path(tmp) / "lab_view"
            shutil.copytree(_MOCK_LAB_VIEW, lab_view)
            os.environ["LAB_VIEW_PATH"] = str(lab_view)
            bootstrap_lab_view(str(_PROJECT_ROOT))
            motor_rot.configure(get_lab_view_paths().motor_rotations_json)

            async def _construct_and_await() -> str:
                lab = MockLabCommunicator()
                task = getattr(lab, "_boot_sync_task", None)
                self.assertIsNotNone(task)
                await task
                return lab.get_lab_state()["runtime_sync"]["status"]

            try:
                status = asyncio.run(_construct_and_await())
                self.assertEqual(status, "ready")
            finally:
                os.environ.pop("CLOUDLABS_MOCK_INIT_DELAY_S", None)


class SimRuntimeSyncTests(unittest.TestCase):
    def test_sim_boot_sync_ready(self) -> None:
        os.environ.pop("CLOUDLABS_SKIP_RUNTIME_SYNC", None)
        os.environ["CLOUDLABS_MOCK_INIT_DELAY_S"] = "0"
        import sys

        src = _PROJECT_ROOT / "simulation_edge" / "src"
        sys.path.insert(0, str(src))
        try:
            from simulation_edge.bootstrap import bootstrap_host

            lab, _ = bootstrap_host()
            self.assertTrue(lab.is_runtime_ready())
            self.assertEqual(lab.get_lab_state()["runtime_sync"]["status"], "ready")
        finally:
            if str(src) in sys.path:
                sys.path.remove(str(src))
            os.environ.pop("CLOUDLABS_MOCK_INIT_DELAY_S", None)


if __name__ == "__main__":
    unittest.main()
