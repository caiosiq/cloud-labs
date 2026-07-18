"""START_LIVE_FEED / END_LIVE_FEED orchestration."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from lab_model.coordinator.catalog.schema import (
    live_feed_channel as catalog_live_feed_channel,
    resolve_cam_id_for_tag,
    resolve_telemetry_stream_backend,
)
from lab_model.language.domain.component import is_teleop_active, is_teleop_ready
from lab_model.coordinator.state.commits import commit_live_feed_end, commit_live_feed_start, end_all_live_feed_channels
from lab_model.coordinator.state.state_machine import refuse_if_not_in_state

from .protocol import LiveFeedHost


def _resolve_backend(catalog_meta: Dict[str, Any]) -> tuple[str, Optional[str], Optional[int]]:
    backend = resolve_telemetry_stream_backend(catalog_meta)
    resource_id = str(catalog_meta.get("id") or "")
    cam_id = resolve_cam_id_for_tag(catalog_meta)
    return backend, resource_id or None, cam_id


def _resolve_live_feed_profile(host: LiveFeedHost, target_id: str) -> str:
    """Use fast recorder preview when TeleOp session is active + ready."""
    with host._state_lock:
        entry = (host.current_state.get("components") or {}).get(target_id)
        if isinstance(entry, dict) and is_teleop_active(entry) and is_teleop_ready(entry):
            return "teleop"
    return "default"


async def run_start_live_feed(host: LiveFeedHost, target_id: str, *, channel: str = "stream") -> None:
    print(f"{host.log_prefix} StartLiveFeed {target_id} channel={channel}")
    with host._state_lock:
        snapshot = host.current_state

    refusal = refuse_if_not_in_state(snapshot, target_id, primitive_name="start_live_feed")
    if refusal:
        print(f"{host.log_prefix} Refusing start_live_feed: {refusal.reason}")
        return

    catalog_meta = host._catalog_meta_for_tag(target_id) or {}
    if catalog_live_feed_channel(catalog_meta, channel) is None:
        print(
            f"{host.log_prefix} Refusing start_live_feed: "
            f"{target_id} does not declare live_feed.{channel}"
        )
        return

    backend, resource_id, cam_id = _resolve_backend(catalog_meta)
    if backend == "none":
        print(f"{host.log_prefix} Refusing start_live_feed: no stream backend for {target_id}")
        return

    profile = _resolve_live_feed_profile(host, target_id)

    ok, msg = await host._primitive_start_live_feed(
        target_id,
        channel=channel,
        backend=backend,
        cam_id=cam_id,
        profile=profile,
    )
    if not ok:
        print(f"{host.log_prefix} start_live_feed hardware failed: {msg}")
        with host._state_lock:
            commit_live_feed_end(host.current_state, target_id, channel=channel, error=msg)
        host._persist_state()
        return

    with host._state_lock:
        commit_live_feed_start(
            host.current_state,
            target_id,
            channel=channel,
            backend=backend,
            resource_id=resource_id,
        )
        host.current_state["last_updated"] = datetime.now().isoformat()
    host._persist_state()
    print(f"{host.log_prefix} Live feed ON for {target_id} ({channel}, {backend}, profile={profile})")


async def run_end_live_feed(host: LiveFeedHost, target_id: str, *, channel: str = "stream") -> None:
    print(f"{host.log_prefix} EndLiveFeed {target_id} channel={channel}")
    ok, msg = await host._primitive_end_live_feed(
        target_id, channel=channel, catalog_meta=host._catalog_meta_for_tag(target_id) or {}
    )
    with host._state_lock:
        if channel == "all":
            end_all_live_feed_channels(host.current_state, target_id)
        else:
            commit_live_feed_end(
                host.current_state,
                target_id,
                channel=channel,
                error=None if ok else msg,
            )
        host.current_state["last_updated"] = datetime.now().isoformat()
    host._persist_state()
    print(f"{host.log_prefix} Live feed OFF for {target_id} ({channel})")
