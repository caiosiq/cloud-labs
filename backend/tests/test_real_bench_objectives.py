"""Tests for real-bench objective measurement planning and strict preflight."""
from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock

from lab_communicator.real.ensemble import RealEnsembleBackend, RealEnsembleHardwareBridge
from lab_communicator.shared.lab_view_config import bootstrap_lab_view
from lab_model.optimization.errors import EnsemblePreflightError
from lab_model.optimization.objective_measurements import (
    collect_objective_measurements,
    find_default_camera_tag,
    plan_objective_terms,
    resolve_centroid_capture_tag,
)
from lab_model.optimization.preflight import preflight_ensemble
from lab_model.optimization.spec import ObjectiveSpec, OptimizeEnsembleParameters
from lab_model.optimization.router import ActuatorRouter

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_MOCK_LAB_VIEW = _PROJECT_ROOT / "backend" / "lab_communicator" / "mock" / "lab_view"
_REAL_CATALOG = (
    _PROJECT_ROOT
    / "backend"
    / "lab_communicator"
    / "real"
    / "lab_view"
    / "default"
    / "component_library.json"
)
_LAB_STATE = _MOCK_LAB_VIEW / "lab_state.json"


def _four_mirror_payload() -> Dict[str, Any]:
    motors = [
        ("v_m20_m1", "tag_20", "1"),
        ("v_m20_m3", "tag_20", "3"),
    ]
    variables = [
        {
            "id": vid,
            "tag_id": tag,
            "path": f"tunables.nominal_motor_positions.{mid}",
            "physical_type": "continuous",
            "unit": "deg",
            "bounds": {"min": -3.0, "max": 3.0},
            "delta": True,
        }
        for vid, tag, mid in motors
    ]
    return {
        "mode": "ensemble",
        "variables": variables,
        "objective": {
            "type": "weighted_sum",
            "minimize": True,
            "terms": [
                {
                    "id": "centroid_rms_px",
                    "weight": 1.0,
                    "source": {
                        "tag_id": "tag_20",
                        "kind": "derived_centroid",
                        "from": "measurables.camera_image",
                        "target_px": {"x": 512.0, "y": 384.0},
                    },
                    "metric": "rms_distance_px",
                },
                {
                    "id": "laser_power",
                    "weight": 0.25,
                    "source": {
                        "tag_id": "tag_50",
                        "kind": "measurable_scalar",
                        "path": "measurables.output_power_readback_mw",
                        "normalize": {"min": 0.0, "max": 100.0},
                    },
                    "metric": "one_minus_normalized",
                },
            ],
        },
        "solver": {
            "blocks": [{"id": "b1", "variable_ids": [v["id"] for v in variables], "max_evals": 5}],
        },
    }


class RealBenchObjectiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ["LAB_VIEW_PATH"] = str(_MOCK_LAB_VIEW)
        bootstrap_lab_view(str(_PROJECT_ROOT))
        with open(_LAB_STATE, "r", encoding="utf-8") as handle:
            cls.fixture_runtime = json.load(handle)
        with open(_REAL_CATALOG, "r", encoding="utf-8") as handle:
            catalog_obj = json.load(handle)
        components = catalog_obj.get("components")
        if not isinstance(components, dict):
            components = {
                k: v for k, v in catalog_obj.items() if isinstance(v, dict) and k != "schema_version"
            }
        cls.catalog_map = {str(k): v for k, v in components.items() if isinstance(v, dict)}

    def test_resolve_mirror_centroid_to_camera_tag(self) -> None:
        objective = ObjectiveSpec.model_validate(_four_mirror_payload()["objective"])
        term = objective.terms[0]
        capture = resolve_centroid_capture_tag(catalog_map=self.catalog_map, term=term)
        self.assertEqual(capture, "tag_22")
        plans = plan_objective_terms(objective, catalog_map=self.catalog_map)
        self.assertEqual(plans[0].capture_tag_id, "tag_22")
        self.assertEqual(plans[0].source_tag_id, "tag_20")

    def test_strict_preflight_accepts_mirror_centroid_with_catalog_camera(self) -> None:
        state = dict(self.fixture_runtime)
        state.setdefault("components", {})["tag_50"] = {
            "id": "tag_50",
            "statecontrol": {
                "tunables": {"output_power_mw": 12.0},
                "measurables": {"output_power_readback_mw": 12.0},
            },
            "telemetry": {"teleop": {"active": False}, "live_feed": {}},
        }
        preflight_ensemble(
            state,
            _four_mirror_payload(),
            catalog_map=self.catalog_map,
            strict_real_objectives=True,
        )

    def test_strict_preflight_rejects_laser_term_on_mirror(self) -> None:
        payload = _four_mirror_payload()
        payload["objective"]["terms"][1]["source"]["tag_id"] = "tag_20"
        payload["objective"]["terms"][1]["source"]["path"] = (
            "measurables.output_power_readback_mw"
        )
        state = dict(self.fixture_runtime)
        entry = state["components"]["tag_20"]
        sc = entry.setdefault("statecontrol", {})
        meas = sc.setdefault("measurables", {})
        meas["output_power_readback_mw"] = 0.0
        entry.setdefault("telemetry", {"teleop": {"active": False}, "live_feed": {}})
        with self.assertRaises(EnsemblePreflightError) as ctx:
            preflight_ensemble(
                state,
                payload,
                catalog_map=self.catalog_map,
                strict_real_objectives=True,
            )
        self.assertIn("LASER_SOURCE", ctx.exception.message)

    def test_strict_preflight_rejects_centroid_without_camera_catalog(self) -> None:
        payload = _four_mirror_payload()
        mirror_row = self.catalog_map.get("tag_20") or {
            "tag_id": "tag_20",
            "type": "OPTICAL_MIRROR",
            "properties": {"motor_ids": [1, 3]},
        }
        empty_catalog: Dict[str, Any] = {"tag_20": mirror_row}
        with self.assertRaises(EnsemblePreflightError) as ctx:
            preflight_ensemble(
                self.fixture_runtime,
                payload,
                catalog_map=empty_catalog,
                strict_real_objectives=True,
            )
        self.assertIn("capture", ctx.exception.message.lower())

    def test_collect_measurements_uses_camera_tag_not_mirror(self) -> None:
        try:
            import cv2
            import numpy as np
        except ImportError:
            self.skipTest("opencv not installed")

        objective = ObjectiveSpec.model_validate(_four_mirror_payload()["objective"])
        captured_tags: list[str] = []

        def _fake_bgr(tag_id: str):
            captured_tags.append(tag_id)
            img = np.zeros((80, 100, 3), dtype=np.uint8)
            cv2.circle(img, (70, 40), 8, (255, 255, 255), -1)
            return img

        meas = collect_objective_measurements(
            objective,
            state=self.fixture_runtime,
            catalog_map=self.catalog_map,
            capture_bgr_for_tag=_fake_bgr,
            read_scalar=lambda _tag, field: 50.0 if field == "output_power_readback_mw" else None,
            allow_image_scalar_fallback=False,
        )
        self.assertEqual(captured_tags, ["tag_22"])
        self.assertAlmostEqual(meas["centroid_rms_px"]["centroid_x"], 70.0, delta=4.0)
        self.assertAlmostEqual(meas["laser_power"]["scalar"], 50.0)

    def test_real_backend_routes_capture_through_planner(self) -> None:
        try:
            import cv2
            import numpy as np
        except ImportError:
            self.skipTest("opencv not installed")

        png_bytes = self._spot_png_bytes()
        comm = MagicMock()
        comm.current_state = self.fixture_runtime
        comm.catalog_map = self.catalog_map
        comm._hardware_read_laser_output_power_mw = MagicMock(return_value=42.0)

        def _capture(tag_id: str, **kwargs: Any) -> bytes:
            self.assertEqual(tag_id, 1)
            return png_bytes

        comm.return_tunables_for_tag.return_value = {}
        comm.capture_table_cam.side_effect = _capture
        comm._table_cam_connected = {1: True}

        bridge = RealEnsembleHardwareBridge(communicator=comm, variables_by_id={}, settle_ms=0)
        spec = OptimizeEnsembleParameters.model_validate(_four_mirror_payload())
        backend = RealEnsembleBackend(bridge=bridge, spec=spec)
        meas, camera_images = backend._measurements_for_objective(spec.objective)
        self.assertAlmostEqual(meas["centroid_rms_px"]["centroid_x"], 70.0, delta=4.0)
        self.assertAlmostEqual(meas["laser_power"]["scalar"], 42.0)
        self.assertIn("tag_22", camera_images)
        comm._hardware_read_laser_output_power_mw.assert_called_with("tag_50")

        # Full evaluate_loss must sync centroid receipts into runtime state.
        router = MagicMock(spec=ActuatorRouter)
        loss, terms = backend.evaluate_loss({}, spec.objective, router=router)
        self.assertGreaterEqual(loss, 0.0)
        self.assertIn("centroid_rms_px", terms)
        entry = self.fixture_runtime["components"]["tag_22"]
        meas_state = entry["statecontrol"]["measurables"]
        self.assertIn("centroid_x_px", meas_state)
        self.assertIn("camera_image", meas_state)

    def test_find_default_camera_tag(self) -> None:
        self.assertEqual(find_default_camera_tag(self.catalog_map), "tag_22")

    @staticmethod
    def _spot_png_bytes() -> bytes:
        import cv2
        import numpy as np

        img = np.zeros((100, 120, 3), dtype=np.uint8)
        cv2.circle(img, (70, 40), 8, (255, 255, 255), -1)
        ok, buf = cv2.imencode(".png", img)
        assert ok
        return buf.tobytes()


if __name__ == "__main__":
    unittest.main()
