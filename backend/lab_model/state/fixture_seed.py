"""Seed fixed-instrument rows (cameras, lasers) into lab_state at boot."""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from lab_model.catalog.schema import (
    catalog_is_fixed_instrument,
    normalize_capabilities,
    resolve_telemetry_stream_backend,
)
from lab_model.domain.component import (
    PRESENCE_OFF_TABLE,
    default_telemetry,
    ensure_component_shape,
    new_component_entry,
)


def _default_exposure_ms(catalog_row: Dict[str, Any]) -> Optional[float]:
    caps = normalize_capabilities(catalog_row.get("capabilities") or {})
    tun = (caps.get("statecontrol") or {}).get("tunables") or {}
    exp_decl = tun.get("exposure_time_ms") if isinstance(tun, dict) else None
    if isinstance(exp_decl, dict) and isinstance(exp_decl.get("default"), (int, float)):
        return float(exp_decl["default"])
    return None


def _default_output_power_mw(catalog_row: Dict[str, Any]) -> Optional[float]:
    caps = normalize_capabilities(catalog_row.get("capabilities") or {})
    tun = (caps.get("statecontrol") or {}).get("tunables") or {}
    pwr_decl = tun.get("output_power_mw") if isinstance(tun, dict) else None
    if isinstance(pwr_decl, dict) and isinstance(pwr_decl.get("default"), (int, float)):
        return float(pwr_decl["default"])
    return None


def build_fixture_component_entry(catalog_row: Dict[str, Any]) -> Dict[str, Any]:
    """Build a v1 lab_state row for a catalog fixed instrument."""
    tag_id = str(catalog_row.get("tag_id") or catalog_row.get("id") or "")
    comp_type = str(catalog_row.get("type") or "OPTICAL_CAMERA")
    entry = new_component_entry(
        tag_id,
        comp_type,
        presence=PRESENCE_OFF_TABLE,
        nominal_pose={"x": 0.0, "y": 0.0, "rotation": 0.0},
        meas_pose={"x": 0.0, "y": 0.0, "rotation": 0.0},
        placement_mode="MANUAL",
        in_storage=False,
        slot=None,
    )
    if comp_type in ("OPTICAL_CAMERA", "CEILING_CAMERA", "LASER_SOURCE"):
        entry["statecontrol"]["measurables"]["pose"] = None

    exp_ms = _default_exposure_ms(catalog_row)
    if exp_ms is not None:
        entry["statecontrol"]["tunables"]["exposure_time_ms"] = exp_ms

    pwr_mw = _default_output_power_mw(catalog_row)
    if pwr_mw is not None:
        entry["statecontrol"]["tunables"]["output_power_mw"] = pwr_mw

    stream_backend = resolve_telemetry_stream_backend(catalog_row)
    if stream_backend != "none":
        tel = entry.setdefault("telemetry", default_telemetry())
        lf = tel.setdefault("live_feed", default_telemetry()["live_feed"])
        stream = lf.setdefault("stream", {})
        if isinstance(stream, dict):
            stream["backend"] = stream_backend
            stream["resource_id"] = catalog_row.get("id")

    return entry


def merge_fixture_components(
    components: Dict[str, Any],
    catalog_rows: Iterable[Dict[str, Any]],
    *,
    overwrite_existing: bool = False,
) -> Dict[str, Any]:
    """Ensure every fixed-instrument catalog row has a lab_state component entry."""
    out = dict(components) if isinstance(components, dict) else {}
    for row in catalog_rows:
        if not isinstance(row, dict) or not catalog_is_fixed_instrument(row):
            continue
        tag_id = row.get("tag_id")
        if not isinstance(tag_id, str) or not tag_id.strip():
            continue
        if tag_id in out and not overwrite_existing:
            existing = out[tag_id]
            if isinstance(existing, dict):
                ensure_component_shape(existing)
                exp_ms = _default_exposure_ms(row)
                tun = (existing.get("statecontrol") or {}).get("tunables") or {}
                if exp_ms is not None and isinstance(tun, dict) and "exposure_time_ms" not in tun:
                    tun["exposure_time_ms"] = exp_ms
            continue
        out[tag_id] = build_fixture_component_entry(row)
    return out


def ensure_fixture_components_in_state(
    current_state: Dict[str, Any],
    catalog_rows: Iterable[Dict[str, Any]],
) -> bool:
    """Merge fixture rows into ``current_state['components']``. Returns True if changed."""
    comps = current_state.get("components")
    if not isinstance(comps, dict):
        comps = {}
    merged = merge_fixture_components(comps, catalog_rows)
    if merged == comps:
        return False
    current_state["components"] = merged
    return True
