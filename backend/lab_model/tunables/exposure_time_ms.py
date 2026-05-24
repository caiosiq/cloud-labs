"""Tunable plugin: ``exposure_time_ms`` (cameras)."""
from __future__ import annotations

from typing import Any

from lab_model.catalog.schema import resolve_cam_id_for_tag
from lab_model.primitives.ids import PrimitiveId

from ._commit import commit_tunable_value
from .registry import register_tunable


@register_tunable(
    field_id="exposure_time_ms",
    widget="FloatRange",
    write_primitive=PrimitiveId.SET_EXPOSURE,
)
async def apply(bridge: Any, tag_id: str, exposure_time_ms: float) -> None:
    """Commit tunable intent and forward to table-cam when ``cam_id`` resolves."""
    exp_ms = float(exposure_time_ms)
    if exp_ms <= 0:
        print(f"{bridge.log_prefix} set_exposure_time_ms: invalid {exp_ms}")
        return

    commit_tunable_value(bridge, tag_id, "exposure_time_ms", exp_ms)

    row = (bridge._catalog_meta_for_tag(tag_id) if hasattr(bridge, "_catalog_meta_for_tag") else None) or {}
    cam_id = resolve_cam_id_for_tag(row)
    if cam_id is not None:
        exp_s = exp_ms / 1000.0
        ok, msg = bridge.table_cam_send_vexp(int(cam_id), exp_s)
        print(
            f"{bridge.log_prefix} set_exposure_time_ms {tag_id}: "
            f"{exp_ms:g} ms (cam {cam_id}) ok={ok} {msg}"
        )
    else:
        print(
            f"{bridge.log_prefix} set_exposure_time_ms {tag_id}: "
            f"{exp_ms:g} ms (intent only, no cam_id)"
        )
