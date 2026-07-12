"""Phase F kernel registry tests."""
from __future__ import annotations

import unittest

from lab_model.jobs.job_manager import validate_submit_spec
from lab_model.optimization.kernels import get_kernel, list_kernels, validate_kernel_ids


class KernelRegistryTests(unittest.TestCase):
    def test_list_kernels_mock_filter(self) -> None:
        mock_rows = list_kernels(backend="mock")
        ids = {k.id for k in mock_rows}
        self.assertIn("ensemble.eval.mock_landscape", ids)
        self.assertIn("ensemble.eval.block_cobyla", ids)

    def test_validate_kernel_ids(self) -> None:
        out = validate_kernel_ids(["ensemble.eval.mock_landscape", "ensemble.eval.block_cobyla"])
        self.assertEqual(len(out), 2)

    def test_unknown_kernel_rejected(self) -> None:
        with self.assertRaises(ValueError):
            validate_kernel_ids(["not.a.kernel"])

    def test_submit_spec_accepts_kernels(self) -> None:
        spec = validate_submit_spec(
            "closed_loop",
            {
                "command": {
                    "action": "OPTIMIZE",
                    "target_id": "tag_20",
                    "parameters": {"mode": "ensemble"},
                },
                "kernels": ["ensemble.eval.mock_landscape"],
            },
        )
        self.assertEqual(spec["kernels"], ["ensemble.eval.mock_landscape"])

    def test_get_kernel(self) -> None:
        row = get_kernel("objective.compile.weighted_sum")
        self.assertIsNotNone(row)
        assert row is not None
        self.assertIn("compile", row.hooks)


if __name__ == "__main__":
    unittest.main()
