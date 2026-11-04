import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from lab_model.coordinator.backends.lab_view_config import atomic_write_json


class AtomicJsonWriteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "state.json"
        self.path.write_text('{"old": true}')

    def test_windows_sharing_violation_is_retried(self):
        replace = os.replace
        calls = []
        def temporarily_locked(source, target):
            calls.append(source)
            if len(calls) < 3:
                raise PermissionError("file in use")
            replace(source, target)
        with patch("lab_model.coordinator.backends.lab_view_config.os.replace", side_effect=temporarily_locked), patch(
            "lab_model.coordinator.backends.lab_view_config.time.sleep"
        ):
            atomic_write_json(str(self.path), {"new": True})
        self.assertEqual(len(calls), 3)
        self.assertEqual(json.loads(self.path.read_text()), {"new": True})
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_permanent_error_preserves_old_file_and_cleans_temporary_file(self):
        with patch("lab_model.coordinator.backends.lab_view_config.os.replace", side_effect=PermissionError("locked")) as replace, patch(
            "lab_model.coordinator.backends.lab_view_config.time.sleep"
        ):
            with self.assertRaises(PermissionError):
                atomic_write_json(str(self.path), {"new": True})
        self.assertEqual(replace.call_count, 5)
        self.assertEqual(json.loads(self.path.read_text()), {"old": True})
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_serialization_error_preserves_old_file(self):
        with self.assertRaises(TypeError):
            atomic_write_json(str(self.path), {"bad": object()})
        self.assertEqual(json.loads(self.path.read_text()), {"old": True})
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_concurrent_writers_do_not_share_temporary_files(self):
        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(lambda i: atomic_write_json(str(self.path), {"value": i}), range(32)))
        self.assertIn(json.loads(self.path.read_text())["value"], range(32))
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])
