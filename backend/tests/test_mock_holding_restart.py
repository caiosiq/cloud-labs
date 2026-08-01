"""Mock mid-air HOLDING must survive restart / boot SYNC pose refresh."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_MOCK_LAB_VIEW = _PROJECT_ROOT / "mock_backend" / "lab_view"
_MOCK_SRC = str((_PROJECT_ROOT / "mock_backend" / "src").resolve())
if _MOCK_SRC not in sys.path:
    sys.path.insert(0, _MOCK_SRC)
_BACKEND = str((_PROJECT_ROOT / "backend").resolve())
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)


class MockHoldingRestartTests(unittest.TestCase):
    def test_boot_restores_holding_after_idle_status_and_sync(self) -> None:
        from lab_model.coordinator.backends.lab_view_config import (
            bootstrap_lab_view,
            get_lab_view_paths,
        )
        from lab_model.language.domain import motor_rotation_store as motor_rot
        from lab_model.language.domain.holding import SYSTEM_STATUS_HOLDING
        from mock_backend.host.communicator import MockLabCommunicator

        os.environ.pop("CLOUDLABS_SKIP_RUNTIME_SYNC", None)
        os.environ["CLOUDLABS_MOCK_INIT_DELAY_S"] = "0"
        with tempfile.TemporaryDirectory() as tmp:
            lab_view = Path(tmp) / "lab_view"
            shutil.copytree(_MOCK_LAB_VIEW, lab_view)
            state_path = lab_view / "lab_state.json"
            doc = json.loads(state_path.read_text(encoding="utf-8"))
            # Inconsistent disk shape seen after leave-while-holding + pose refresh.
            comps = doc.get("components") or {}
            self.assertIn("tag_11", comps)
            doc["system_status"] = "IDLE"
            doc["holding"] = {
                "tag_id": "tag_11",
                "nominal_pose": {
                    "x": -10.0,
                    "y": 20.0,
                    "rotation": 0.0,
                    "z": 245.0,
                },
                "requires_operator_confirm": False,
            }
            state_path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")

            os.environ["LAB_VIEW_PATH"] = str(lab_view)
            bootstrap_lab_view(str(_PROJECT_ROOT))
            motor_rot.configure(get_lab_view_paths().motor_rotations_json)

            lab = MockLabCommunicator()
            state = lab.get_lab_state()
            self.assertEqual(state.get("system_status"), SYSTEM_STATUS_HOLDING)
            self.assertEqual((state.get("holding") or {}).get("tag_id"), "tag_11")

            # Explicit refresh must also preserve HOLDING (boot SYNC path).
            lab.refresh_pose_from_camera(apply_tag_ids=["tag_10"])
            after = lab.get_lab_state()
            self.assertEqual(after.get("system_status"), SYSTEM_STATUS_HOLDING)
            self.assertEqual((after.get("holding") or {}).get("tag_id"), "tag_11")

            os.environ.pop("CLOUDLABS_MOCK_INIT_DELAY_S", None)


if __name__ == "__main__":
    unittest.main()
