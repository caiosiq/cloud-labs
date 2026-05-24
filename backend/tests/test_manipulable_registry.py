"""Phase 8: registry-backed manipulable lookup on real communicator."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock


class TestManipulableRegistryBridge(unittest.TestCase):
    def test_get_manipulable_prefers_registry(self) -> None:
        from lab_communicator.real.communicator import RealLabCommunicator

        manip = MagicMock()
        manip.tag_id = "tag_5"
        exp = MagicMock()
        exp.get_manipulable.return_value = manip
        exp.list_manipulables.return_value = [manip]

        comm = RealLabCommunicator.__new__(RealLabCommunicator)
        comm.experiment = exp
        comm.component_map = {}

        self.assertIs(comm.get_manipulable("tag_5"), manip)

    def test_sync_component_map_from_registry(self) -> None:
        from lab_communicator.real.communicator import RealLabCommunicator

        manip = MagicMock()
        manip.tag_id = "tag_5"
        exp = MagicMock()
        exp.list_manipulables.return_value = [manip]

        comm = RealLabCommunicator.__new__(RealLabCommunicator)
        comm.experiment = exp
        comm.component_map = {}

        comm._sync_component_map_from_registry()
        self.assertEqual(comm.component_map, {"tag_5": manip})


if __name__ == "__main__":
    unittest.main()
