"""COBYLA optimization reference resolution and preflight checks."""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

from lab_model.domain.component import get_measurables
from lab_model.state.state_machine import RefusalResult, ok, refuse


def _image_path_from_field(field: Any) -> Optional[str]:
    if not isinstance(field, dict):
        return None
    path = field.get("path")
    if isinstance(path, str) and path.strip():
        return path.strip()
    return None


def resolve_optimize_sensor_tag(
    current_state: Dict[str, Any],
    params: Dict[str, Any],
    catalog_map: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Optional[str]:
    """Resolve the camera tag used as the COBYLA sensor / reference source."""
    sensor = (params or {}).get("sensor_component") or (params or {}).get("sensor_tag_id")
    if isinstance(sensor, str) and sensor.strip():
        return sensor.strip()

    cam_no = (params or {}).get("camera_number")
    if cam_no is not None and catalog_map:
        from lab_model.catalog.schema import find_tag_id_for_cam_id

        try:
            tag = find_tag_id_for_cam_id(catalog_map, int(cam_no))
            if tag:
                return tag
        except (TypeError, ValueError):
            pass
    return None


def cobyla_reference_path(
    current_state: Dict[str, Any],
    sensor_tag_id: str | None = None,
) -> Optional[str]:
    """Return filesystem path for the lab COBYLA reference image, if pinned."""
    void = sensor_tag_id  # kept for call-site compatibility
    return _image_path_from_field(current_state.get("optimization_reference"))


def cobyla_reference_file_exists(path: str) -> bool:
    if not path:
        return False
    return os.path.isfile(os.path.abspath(path))


def refuse_if_cobyla_without_reference(
    current_state: Dict[str, Any],
    strategy_name: str,
    params: Dict[str, Any],
    *,
    catalog_map: Optional[Dict[str, Dict[str, Any]]] = None,
) -> RefusalResult:
    """Refuse COBYLA when the sensor camera has no usable reference image."""
    if (strategy_name or "").upper() != "COBYLA":
        return ok()

    sensor = resolve_optimize_sensor_tag(current_state, params, catalog_map)
    if not sensor:
        return refuse(
            "COBYLA requires sensor_component (or camera_number mapped to a camera tag)."
        )

    path = cobyla_reference_path(current_state, sensor)
    if not path:
        return refuse(
            "COBYLA requires a lab optimization reference. "
            "Record a camera image and pin it as the lab optimization reference first."
        )

    if not cobyla_reference_file_exists(path):
        return refuse(
            f"COBYLA lab reference image file missing: {path}"
        )

    return ok()


def refuse_if_no_measurable_camera_image(
    current_state: Dict[str, Any],
    tag_id: str,
) -> RefusalResult:
    """Refuse when ``tag_id`` has no usable ``measurables.camera_image``."""
    components = current_state.get("components") or {}
    if not isinstance(components, dict):
        return refuse(f"Unknown component: {tag_id}")

    entry = components.get(tag_id)
    if not isinstance(entry, dict):
        return refuse(f"Unknown component: {tag_id}")

    camera_image = get_measurables(entry).get("camera_image")
    if not isinstance(camera_image, dict):
        return refuse(
            f"{tag_id} has no recorded camera_image in measurables. "
            f"Run record {tag_id} first."
        )

    path = camera_image.get("path")
    if not isinstance(path, str) or not path.strip():
        return refuse(f"{tag_id} measurables.camera_image has no path.")

    abs_path = os.path.abspath(path.strip())
    if not os.path.isfile(abs_path):
        return refuse(f"Camera image file missing: {abs_path}")

    return ok()
