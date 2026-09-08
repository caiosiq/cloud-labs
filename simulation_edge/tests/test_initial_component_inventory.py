import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DESIRED_COMPONENTS = {
    "tag_20": ("Front Polarizer", "P1"),
    "tag_9": ("150mm Lens", "lens_150mm"),
    "tag_11": ("Middle Polarizer", "tilted_polarizer"),
    "tag_14": ("100mm Lens", "lens_100mm"),
    "tag_13": ("Back Polarizer", "P2"),
    "tag_22": ("Gripper Camera 1", "cam_gripper_1"),
    "tag_15": ("60mm Lens", "lens_60mm"),
}


def load(relative_path: str):
    return json.loads((ROOT / relative_path).read_text(encoding="utf-8"))


class InitialComponentInventoryTests(unittest.TestCase):
    def test_simulation_and_mock_initialize_with_exactly_requested_components(self):
        expected_tags = list(DESIRED_COMPONENTS)
        for prefix in ("simulation_edge", "mock_backend"):
            active = load(f"{prefix}/lab_view/active_catalog.json")
            inventory = load(f"{prefix}/cloudlabs_edge/data/inventory.json")
            state = load(f"{prefix}/lab_view/lab_state.json")
            library = load(f"{prefix}/cloudlabs_edge/data/library.json")

            self.assertEqual(active["tag_ids"], expected_tags)
            self.assertEqual(list(inventory["entries"]), expected_tags)
            self.assertEqual(list(state["components"]), expected_tags)
            for tag_id, (name, catalog_id) in DESIRED_COMPONENTS.items():
                self.assertEqual(library["components"][tag_id]["name"], name)
                self.assertEqual(library["components"][tag_id]["id"], catalog_id)

    def test_existing_canvas_renderer_dispatches_requested_icons(self):
        renderer = (ROOT / "frontend/js/canvas/render.js").read_text(
            encoding="utf-8"
        )
        for dispatch_key in ("P1", "tilted_polarizer", "OPTICAL_POLARIZER"):
            self.assertIn(dispatch_key, renderer)
        self.assertIn("type === 'OPTICAL_LENS'", renderer)
        self.assertIn("catalogId === 'cam_gripper_1'", renderer)


if __name__ == "__main__":
    unittest.main()
