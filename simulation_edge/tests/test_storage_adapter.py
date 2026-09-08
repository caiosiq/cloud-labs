import unittest

from adapters import context, motion


class RecordingLab:
    def __init__(self):
        self.calls = []

    async def store_component(self, tag_id, **kwargs):
        self.calls.append((tag_id, kwargs))
        return {"tag_id": tag_id, **kwargs}


class StorageAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.previous_lab = context._lab
        self.lab = RecordingLab()
        context.bind_lab(self.lab)

    async def asyncTearDown(self):
        context.bind_lab(self.previous_lab)

    async def test_passes_explicit_slot_to_simulation_host(self):
        result = await motion.store_component(
            {"target_id": "tag_11", "slot_i": 2, "slot_j": 1}
        )

        self.assertEqual(self.lab.calls, [("tag_11", {"slot_i": 2, "slot_j": 1})])
        self.assertEqual((result["slot_i"], result["slot_j"]), (2, 1))

    async def test_preserves_automatic_allocation_without_slot(self):
        await motion.store_component({"tag_id": "tag_11"})

        self.assertEqual(self.lab.calls, [("tag_11", {})])

    async def test_rejects_partial_slot(self):
        with self.assertRaisesRegex(ValueError, "must be provided together"):
            await motion.store_component({"tag_id": "tag_11", "slot_i": 2})


if __name__ == "__main__":
    unittest.main()
