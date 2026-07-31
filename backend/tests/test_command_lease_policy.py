"""Coordinator command-lease policy: required unless SOLO."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from lab_model.coordinator import lease_policy as lp


class CommandLeasePolicyTests(unittest.TestCase):
    def test_required_for_mock_and_real_by_default(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "CLOUDLABS_SOLO"}
        with patch.dict(os.environ, env, clear=True):
            self.assertTrue(lp.command_lease_required("mock.default"))
            self.assertTrue(lp.command_lease_required("real.default"))
            self.assertTrue(lp.command_lease_required("sim.default"))
            policy = lp.coordinator_policy()
            self.assertFalse(policy["solo"])
            self.assertTrue(policy["command_lease_required"])
            self.assertTrue(policy["strict_lease_mock"])

    def test_solo_disables_requirement(self) -> None:
        with patch.dict(os.environ, {"CLOUDLABS_SOLO": "1"}, clear=False):
            self.assertFalse(lp.command_lease_required("mock.default"))
            self.assertFalse(lp.command_lease_required("real.default"))
            policy = lp.coordinator_policy()
            self.assertTrue(policy["solo"])
            self.assertFalse(policy["command_lease_required"])


if __name__ == "__main__":
    unittest.main()
