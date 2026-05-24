"""Catalog loading, validation, and capability inference."""

from .bundle import merged_catalog_maps, merged_catalog_rows
from .schema import (
    CatalogValidationError,
    HardwareBinding,
    catalog_declared_primitives,
    catalog_is_fixed_instrument,
    find_tag_id_for_cam_id,
    infer_default_capabilities,
    live_feed_channel,
    load_component_library_rows,
    normalize_capabilities,
    resolve_hardware_binding,
    resolve_cam_id_for_tag,
    resolve_telemetry_stream_backend,
    teleop_channel,
    telemetry_channel,
    validate_catalog_v1,
)

__all__ = [
    "CatalogValidationError",
    "HardwareBinding",
    "catalog_declared_primitives",
    "catalog_is_fixed_instrument",
    "infer_default_capabilities",
    "live_feed_channel",
    "normalize_capabilities",
    "teleop_channel",
    "load_component_library_rows",
    "validate_catalog_v1",
    "merged_catalog_maps",
    "merged_catalog_rows",
    "find_tag_id_for_cam_id",
    "resolve_hardware_binding",
    "resolve_cam_id_for_tag",
    "resolve_telemetry_stream_backend",
    "telemetry_channel",
]
