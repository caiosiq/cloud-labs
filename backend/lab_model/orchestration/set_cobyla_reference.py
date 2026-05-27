"""SET_COBYLA_REFERENCE — pin lab optimization reference from measurables."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict

from lab_model.domain.component import get_measurables
from lab_model.state.commits import commit_pin_optimization_reference
from lab_model.state.state_machine import PrimitiveRefusalError

from .cobyla_reference import refuse_if_no_measurable_camera_image
from .protocol import StateHost


async def run_set_cobyla_reference(host: StateHost, tag_id: str) -> Dict[str, Any]:
    """Copy ``measurables.camera_image`` on ``tag_id`` into ``optimization_reference``."""
    print(f"{host.log_prefix} SetCobylaReference from {tag_id}")

    with host._state_lock:
        snapshot = host.current_state

    refusal = refuse_if_no_measurable_camera_image(snapshot, tag_id)
    if refusal:
        print(f"{host.log_prefix} Refusing set_cobyla_reference: {refusal.reason}")
        raise PrimitiveRefusalError(refusal.reason or "set_cobyla_reference refused")

    with host._state_lock:
        entry = (host.current_state.get("components") or {}).get(tag_id)
        camera_image = get_measurables(entry).get("camera_image")
        payload = commit_pin_optimization_reference(
            host.current_state, tag_id, camera_image
        )
        host.current_state["last_updated"] = datetime.now().isoformat()

    host._persist_state()
    print(
        f"{host.log_prefix} Lab optimization reference pinned from {tag_id}: "
        f"{payload.get('path')}"
    )
    return payload
