"""EVAL_KERNEL primitive — kernels as inputs, not peer verbs."""
from __future__ import annotations

import unittest

from lab_model.language.primitives import EvalKernelBody, PrimitiveId, parse_command_payload
from lab_model.language.primitives.registry import PRIMITIVE_REGISTRY


class EvalKernelPrimitiveTests(unittest.TestCase):
    def test_parse_eval_kernel(self) -> None:
        cmd = parse_command_payload(
            {
                "action": "EVAL_KERNEL",
                "target_id": "tag_22",
                "parameters": {
                    "kernel_id": "demo.image_mean_score",
                    "field": "camera_image",
                },
            }
        )
        self.assertIsInstance(cmd, EvalKernelBody)
        self.assertEqual(cmd.action, "EVAL_KERNEL")
        self.assertEqual(cmd.parameters.kernel_id, "demo.image_mean_score")
        self.assertEqual(cmd.parameters.field, "camera_image")

    def test_registry_has_handler(self) -> None:
        meta = PRIMITIVE_REGISTRY[PrimitiveId.EVAL_KERNEL]
        self.assertEqual(meta["handler"], "eval_kernel_for_tag")
        self.assertEqual(meta["kind"].value if hasattr(meta["kind"], "value") else meta["kind"], "atomic")


if __name__ == "__main__":
    unittest.main()
