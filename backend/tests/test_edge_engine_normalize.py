"""Phase 1: unit-hypercube normalize (edge engine)."""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from cloudlabs_edge_dev.optimization.normalize import NormalizedSearchSpace


def _var(vid: str, lo: float, hi: float, *, delta: bool = True):
    return SimpleNamespace(
        id=vid,
        bounds=SimpleNamespace(min=lo, max=hi),
        delta=delta,
    )


class NormalizeTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        space = NormalizedSearchSpace([_var("v1", -2.0, 2.0, delta=True)], {"v1": 10.0})
        # delta bounds → physical [8, 12]
        self.assertEqual(space.physical_bounds("v1"), (8.0, 12.0))
        u = space.normalize_scalar("v1", 10.0)
        self.assertAlmostEqual(u, 0.5)
        self.assertAlmostEqual(space.denormalize_scalar("v1", u), 10.0)
        self.assertAlmostEqual(space.denormalize_scalar("v1", 0.0), 8.0)
        self.assertAlmostEqual(space.denormalize_scalar("v1", 1.0), 12.0)

    def test_absolute_bounds(self) -> None:
        space = NormalizedSearchSpace(
            [_var("v1", 0.0, 10.0, delta=False)], {"v1": 3.0}
        )
        self.assertEqual(space.physical_bounds("v1"), (0.0, 10.0))
        self.assertAlmostEqual(space.normalize_scalar("v1", 5.0), 0.5)

    def test_degenerate_span(self) -> None:
        space = NormalizedSearchSpace(
            [_var("v1", 0.0, 0.0, delta=False)], {"v1": 1.0}
        )
        self.assertAlmostEqual(space.normalize_scalar("v1", 99.0), 0.5)
        self.assertAlmostEqual(space.denormalize_scalar("v1", 0.3), 0.0)

    def test_dict_round_trip(self) -> None:
        space = NormalizedSearchSpace(
            [_var("a", -1.0, 1.0), _var("b", -1.0, 1.0)],
            {"a": 0.0, "b": 5.0},
        )
        physical = {"a": 0.5, "b": 5.5}
        u = space.normalize_dict(physical)
        back = space.denormalize_list(u)
        self.assertAlmostEqual(back["a"], 0.5)
        self.assertAlmostEqual(back["b"], 5.5)


if __name__ == "__main__":
    unittest.main()
