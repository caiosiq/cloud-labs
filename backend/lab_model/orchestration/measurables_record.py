"""RECORD_MEASURABLES orchestration."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict

from lab_model.state.commits import commit_observed_measurables
from lab_model.state.state_machine import refuse_if_teleop_active

from .protocol import RecordMeasurablesHost


async def run_record_measurables(host: RecordMeasurablesHost, tag_id: str) -> Dict[str, Any]:
    with host._state_lock:
        entry = (host.current_state.get("components") or {}).get(tag_id)
    if not isinstance(entry, dict):
        return {}

    refusal = refuse_if_teleop_active(
        host.current_state, tag_id, primitive_name="record_measurables"
    )
    if refusal:
        print(f"{host.log_prefix} Refusing record_measurables: {refusal.reason}")
        return host.return_measurables_for_tag(tag_id)

    await host.end_live_feed(tag_id, channel="all")

    catalog_meta = host._catalog_meta_for_tag(tag_id) or {}
    try:
        captured = await host._primitive_record_measurables(tag_id, catalog_meta)
    except Exception as e:  # noqa: BLE001
        print(f"{host.log_prefix} record_measurables_for_tag failed: {e}")
        captured = None

    if isinstance(captured, dict):
        nested = captured.get("measurables")
        if isinstance(nested, dict):
            patch = dict(nested)
        elif captured.get("path"):
            patch = {
                "camera_image": {
                    "path": str(captured["path"]),
                    "source": str(captured.get("source") or ""),
                    "cam_id": int(captured.get("cam_id") or 0),
                    "format": str(captured.get("format") or "png"),
                }
            }
        else:
            patch = {k: v for k, v in captured.items() if v is not None}
        if patch:
            with host._state_lock:
                commit_observed_measurables(host.current_state, tag_id, patch)
                host.current_state["last_updated"] = datetime.now().isoformat()
            host._persist_state()
    return host.return_measurables_for_tag(tag_id)
