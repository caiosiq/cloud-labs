"""Import-surface smoke for the standalone cloudlabs package."""
from __future__ import annotations

import unittest


class CloudlabsPackageImportTests(unittest.TestCase):
    def test_public_exports(self) -> None:
        import cloudlabs

        self.assertTrue(callable(cloudlabs.connect))
        self.assertTrue(callable(cloudlabs.resolve_backend_id))
        self.assertTrue(callable(cloudlabs.compile_objective))
        from cloudlabs import ObjectiveGraphBuilder, CloudLabsClient

        self.assertTrue(ObjectiveGraphBuilder)
        self.assertTrue(CloudLabsClient)
        from cloudlabs import ComponentProxy, ComponentsNamespace

        self.assertTrue(ComponentProxy)
        self.assertTrue(ComponentsNamespace)

    def test_no_lab_model_on_import(self) -> None:
        import sys
        from pathlib import Path

        # Fresh import already happened; ensure package modules do not require lab_model.
        banned = [
            name
            for name in sys.modules
            if name.startswith("cloudlabs") and "lab_model" in name
        ]
        self.assertEqual(banned, [])
        import cloudlabs

        pkg_root = Path(cloudlabs.__file__).resolve().parent
        leaks = []
        for path in pkg_root.rglob("*.py"):
            src = path.read_text(encoding="utf-8")
            if "lab_model" in src or "lab_communicator" in src:
                leaks.append(str(path.name))
        self.assertEqual(leaks, [])


if __name__ == "__main__":
    unittest.main()
