"""Unit tests for fluent ``lab.components`` API (Step F)."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, call

from cloudlabs.components import ComponentProxy, ComponentsNamespace
from cloudlabs.measurable import MeasurableHandle


class ComponentsNamespaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = MagicMock()
        self.ns = ComponentsNamespace(self.client)

    def test_getattr_and_getitem(self) -> None:
        a = self.ns.tag_20
        b = self.ns["tag_20"]
        self.assertIsInstance(a, ComponentProxy)
        self.assertEqual(a.tag_id, "tag_20")
        self.assertEqual(b.tag_id, "tag_20")
        self.assertIs(a.client, self.client)

    def test_list_delegates(self) -> None:
        self.client.list_components.return_value = [{"tag_id": "tag_20"}]
        rows = self.ns.list()
        self.assertEqual(rows[0]["tag_id"], "tag_20")
        self.client.list_components.assert_called_once_with(refresh=False)

    def test_keys(self) -> None:
        self.client.list_components.return_value = [
            {"tag_id": "tag_20"},
            {"tag_id": "tag_22"},
        ]
        self.assertEqual(self.ns.keys(), ["tag_20", "tag_22"])

    def test_contains(self) -> None:
        self.client.list_components.return_value = [{"tag_id": "tag_20"}]
        self.assertIn("tag_20", self.ns)
        self.assertNotIn("tag_99", self.ns)


class ComponentProxyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = MagicMock()
        self.comp = ComponentProxy(self.client, "tag_20")

    def test_move_x(self) -> None:
        out = self.comp.move(x=12.5)
        self.client.move_component.assert_called_once_with(
            "tag_20",
            "tunables.nominal_pose.x",
            12.5,
        )
        self.assertIs(out, self.comp)

    def test_move_multi_axis(self) -> None:
        self.comp.move(x=1.0, y=2.0, rotation=3.0)
        self.assertEqual(
            self.client.move_component.call_args_list,
            [
                call("tag_20", "tunables.nominal_pose.x", 1.0),
                call("tag_20", "tunables.nominal_pose.y", 2.0),
                call("tag_20", "tunables.nominal_pose.rotation", 3.0),
            ],
        )

    def test_move_requires_axis(self) -> None:
        with self.assertRaises(ValueError):
            self.comp.move()

    def test_motor(self) -> None:
        self.comp.motor(3, 45.0)
        self.client.move_component.assert_called_once_with(
            "tag_20",
            "tunables.nominal_motor_positions.3",
            45.0,
        )

    def test_set_and_capture(self) -> None:
        self.comp.set("tunables.nominal_pose.x", 9.0)
        self.client.set_tunable.assert_called_once_with(
            "tag_20",
            "tunables.nominal_pose.x",
            9.0,
        )
        self.client.capture_measurable.return_value = {"ok": True}
        self.comp.capture("camera_image")
        self.client.capture_measurable.assert_called_once_with(
            "tag_20",
            "camera_image",
        )

    def test_measurable_handle(self) -> None:
        handle = MeasurableHandle(self.client, "tag_20", "camera_image")
        self.client.measurable.return_value = handle
        got = self.comp.measurable("camera_image")
        self.assertIs(got, handle)
        self.client.measurable.assert_called_once_with("tag_20", "camera_image")

    def test_wait_until_idle(self) -> None:
        self.comp.wait_until_idle(timeout_s=5.0)
        self.client.wait_until_idle.assert_called_once_with(
            poll_interval_s=0.25,
            timeout_s=5.0,
        )


class ClientComponentsPropertyTests(unittest.TestCase):
    def test_property_cached(self) -> None:
        from cloudlabs.client import CloudLabsClient

        lab = CloudLabsClient("mock.default", verbose=False)
        a = lab.components
        b = lab.components
        self.assertIs(a, b)
        self.assertIsInstance(a.tag_22, ComponentProxy)
        self.assertEqual(a.tag_22.tag_id, "tag_22")


if __name__ == "__main__":
    unittest.main()
