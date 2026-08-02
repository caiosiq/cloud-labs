"""RECORD_MEASURABLES orchestration."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict

from lab_model.coordinator.state.commits import commit_observed_measurables
from lab_model.coordinator.state.state_machine import refuse_if_teleop_active

from .protocol import RecordMeasurablesHost


def _summary_fields(patch: Dict[str, Any]) -> str:
    parts: list[str] = []
    for key, val in patch.items():
        if isinstance(val, dict):
            shape = val.get("shape")
            href = None
            data = val.get("data")
            if isinstance(data, dict):
                href = data.get("href")
            path = val.get("path")
            detail = shape if shape is not None else (href or path or "dict")
            parts.append(f"{key}={detail}")
        else:
            parts.append(f"{key}={type(val).__name__}")
    return ", ".join(parts) if parts else "(empty)"


async def run_record_measurables(host: RecordMeasurablesHost, tag_id: str) -> Dict[str, Any]:
    with host._state_lock:
        entry = (host.current_state.get("components") or {}).get(tag_id)
    if not isinstance(entry, dict):
        print(
            f"{host.log_prefix} RECORD_MEASURABLES skipped: "
            f"tag={tag_id!r} not in lab_state components",
            flush=True,
        )
        return {}

    refusal = refuse_if_teleop_active(
        host.current_state, tag_id, primitive_name="record_measurables"
    )
    if refusal:
        print(
            f"{host.log_prefix} Refusing record_measurables: {refusal.reason}",
            flush=True,
        )
        return host.return_measurables_for_tag(tag_id)

    print(
        f"{host.log_prefix} RECORD_MEASURABLES begin tag={tag_id!r}",
        flush=True,
    )
    await host.end_live_feed(tag_id, channel="all")

    catalog_meta = host._catalog_meta_for_tag(tag_id) or {}
    try:
        captured = await host._primitive_record_measurables(tag_id, catalog_meta)
    except Exception as e:  # noqa: BLE001
        print(
            f"{host.log_prefix} RECORD_MEASURABLES failed tag={tag_id!r}: {e}",
            flush=True,
        )
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
            print(
                f"{host.log_prefix} RECORD_MEASURABLES committed tag={tag_id!r} "
                f"{_summary_fields(patch)}",
                flush=True,
            )
        else:
            print(
                f"{host.log_prefix} RECORD_MEASURABLES empty patch tag={tag_id!r} "
                f"captured_keys={sorted(captured.keys())}",
                flush=True,
            )
    else:
        print(
            f"{host.log_prefix} RECORD_MEASURABLES no capture payload tag={tag_id!r} "
            f"captured={type(captured).__name__}",
            flush=True,
        )
    return host.return_measurables_for_tag(tag_id)
