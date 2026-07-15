"""Step E — soft-deprecate legacy OPTIMIZE; optional mock redirect."""
from __future__ import annotations

import os
import unittest
import warnings
from unittest.mock import MagicMock

from lab_model.jobs.job_manager import validate_submit_spec
from lab_model.optimization.presets import compile_legacy_strategy
from lab_model.orchestration.optimize import (
    _legacy_redirect_enabled,
    _try_redirect_legacy_to_ensemble,
    run_optimize_component,
)


class CompileLegacyTests(unittest.TestCase):
    def test_compile_produces_ensemble_mode(self) -> None:
        spec = compile_legacy_strategy(
            target_id="tag_20",
            strategy="COBYLA",
            motor_ids=[1],
        )
        self.assertEqual(spec["mode"], "ensemble")
        self.assertEqual(len(spec["variables"]), 1)
        self.assertEqual(spec["variables"][0]["path"], "tunables.nominal_motor_positions.1")


class LegacyRedirectGateTests(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("CLOUDLABS_LEGACY_OPTIMIZE_REDIRECT", None)

    def test_disabled_by_default(self) -> None:
        host = MagicMock()
        host.lab_mode = "MOCK"
        self.assertFalse(_legacy_redirect_enabled(host))

    def test_enabled_on_mock_when_env_set(self) -> None:
        os.environ["CLOUDLABS_LEGACY_OPTIMIZE_REDIRECT"] = "1"
        host = MagicMock()
        host.lab_mode = "MOCK"
        self.assertTrue(_legacy_redirect_enabled(host))

    def test_never_on_real(self) -> None:
        os.environ["CLOUDLABS_LEGACY_OPTIMIZE_REDIRECT"] = "1"
        host = MagicMock()
        host.lab_mode = "REAL"
        self.assertFalse(_legacy_redirect_enabled(host))

    def test_try_redirect_builds_ensemble(self) -> None:
        host = MagicMock()
        compiled = _try_redirect_legacy_to_ensemble(
            host,
            "tag_20",
            "NEWTON",
            {"motor_ids": [1, 3], "settle_ms": 10},
        )
        assert compiled is not None
        self.assertEqual(compiled["mode"], "ensemble")
        self.assertEqual(compiled["settle_ms"], 10)


class LegacyDeprecationWarningTests(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_path_emits_deprecation_warning(self) -> None:
        host = MagicMock()
        host.log_prefix = "[TEST]"
        host.lab_mode = "MOCK"
        host._catalog_meta_for_tag.return_value = None  # refuse early after warn

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            await run_optimize_component(host, "tag_20", "NEWTON", {"mode": "legacy_strategy"})
        dep = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        self.assertTrue(dep)
        self.assertIn("legacy_strategy", str(dep[0].message))


class ClosedLoopEnsembleRequiredTests(unittest.TestCase):
    def test_legacy_rejected_with_migration_hint(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            validate_submit_spec(
                "closed_loop",
                {
                    "command": {
                        "action": "OPTIMIZE",
                        "target_id": "tag_20",
                        "parameters": {"strategy": "NEWTON"},
                    }
                },
            )
        self.assertIn("ensemble", str(ctx.exception).lower())
        self.assertIn("run_optimize", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
