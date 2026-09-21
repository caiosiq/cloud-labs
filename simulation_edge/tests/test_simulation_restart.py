from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path
from unittest import mock

from simulation_edge.bootstrap import _resolve_catalog_rows
from simulation_edge.host.simulation_host import SimulationHost


LAB_VIEW = Path(__file__).resolve().parents[1] / "lab_view"


class _RunningClient:
    def __init__(self) -> None:
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


def _host() -> SimulationHost:
    layout = json.loads((LAB_VIEW / "layout.json").read_text(encoding="utf-8"))
    state = json.loads((LAB_VIEW / "lab_state.json").read_text(encoding="utf-8"))
    return SimulationHost(
        state,
        _resolve_catalog_rows(LAB_VIEW),
        layout,
        enable_mujoco=False,
    )


class SimulationRestartTests(unittest.TestCase):
    def test_shared_reset_revision_survives_edge_restart(self) -> None:
        host = _host()
        requested = copy.deepcopy(host.current_state)
        requested["simulation_reset"] = {
            "revision": "revision-123",
            "selector": "default",
        }
        host._client = _RunningClient()

        with mock.patch.object(host, "_start_mujoco"):
            host.restart_mujoco(lab_state=requested)

        self.assertEqual(
            host.current_state["simulation_reset"]["revision"],
            "revision-123",
        )

    def test_invalid_scene_is_rejected_before_running_viewer_is_stopped(self) -> None:
        host = _host()
        requested = copy.deepcopy(host.current_state)
        components = requested["components"]
        pose = components["tag_11"]["statecontrol"]["tunables"]["nominal_pose"]
        components["tag_14"]["statecontrol"]["tunables"]["nominal_pose"] = copy.deepcopy(pose)
        client = _RunningClient()
        host._client = client

        with mock.patch.object(host, "_start_mujoco") as start_mujoco:
            with self.assertRaisesRegex(ValueError, "overlapping startup components"):
                host.restart_mujoco(lab_state=requested)

        self.assertFalse(client.stopped)
        start_mujoco.assert_not_called()
        self.assertEqual(
            host.current_state["components"]["tag_14"]["statecontrol"]["tunables"][
                "nominal_pose"
            ],
            {"x": 320.0, "y": -95.0, "rotation": -90.0},
        )


if __name__ == "__main__":
    unittest.main()
