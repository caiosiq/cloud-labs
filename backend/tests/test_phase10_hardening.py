"""Phase 10: laser tunable registration (cloud-labs)."""
from __future__ import annotations

import unittest


class TestLaserTunablePlugin(unittest.TestCase):
    def test_output_power_mw_registered(self) -> None:
        from lab_model import tunables  # noqa: F401
        from lab_model.tunables.registry import get_tunable

        spec = get_tunable("output_power_mw")
        self.assertIsNotNone(spec)
        assert spec is not None
        self.assertEqual(spec.write_primitive.value, "SET_LASER_OUTPUT")

    def test_measurable_registered(self) -> None:
        from lab_model import measurables  # noqa: F401
        from lab_model.measurables.registry import get_measurable

        spec = get_measurable("output_power_readback_mw")
        self.assertIsNotNone(spec)


if __name__ == "__main__":
    unittest.main()
