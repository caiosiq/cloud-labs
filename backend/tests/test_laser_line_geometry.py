"""Laser-line geometry validity: vertical, horizontal, and sloped are all accepted."""

from __future__ import annotations

import unittest

from lab_model.coordinator.backends.lab_view_config import (
    two_points_define_line,
    two_points_to_ab,
)


class TwoPointsDefineLineTests(unittest.TestCase):
    def test_vertical_line_is_valid(self) -> None:
        self.assertTrue(
            two_points_define_line({"x": 0, "y": -450}, {"x": 0, "y": 450})
        )

    def test_horizontal_line_is_valid(self) -> None:
        self.assertTrue(
            two_points_define_line({"x": -200, "y": 200}, {"x": 200, "y": 200})
        )

    def test_sloped_line_is_valid(self) -> None:
        self.assertTrue(
            two_points_define_line({"x": -200, "y": -200}, {"x": 200, "y": 200})
        )

    def test_coincident_points_are_invalid(self) -> None:
        self.assertFalse(
            two_points_define_line({"x": 10, "y": 10}, {"x": 10, "y": 10})
        )

    def test_non_numeric_points_are_invalid(self) -> None:
        self.assertFalse(two_points_define_line({"x": "a", "y": 0}, {"x": 1, "y": 1}))
        self.assertFalse(two_points_define_line({"x": 0}, {"x": 1, "y": 1}))


class TwoPointsToAbLegacyTests(unittest.TestCase):
    def test_horizontal_line_has_no_legacy_ab(self) -> None:
        # The legacy x = a*y + b form cannot represent a horizontal line.
        self.assertIsNone(
            two_points_to_ab({"x": -200, "y": 200}, {"x": 200, "y": 200})
        )

    def test_vertical_line_has_legacy_ab(self) -> None:
        self.assertEqual(
            two_points_to_ab({"x": 5, "y": -450}, {"x": 5, "y": 450}),
            (0.0, 5.0),
        )


if __name__ == "__main__":
    unittest.main()
