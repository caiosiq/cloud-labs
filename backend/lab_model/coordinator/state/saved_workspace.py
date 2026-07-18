"""Versioned saved workspace snapshots for repeatable demos and debugging."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Tuple


WORKSPACE_STATE_KIND = "cloud_labs_workspace_state"
WORKSPACE_STATE_VERSION = 2


def build_workspace_state(
    lab_state: Mapping[str, Any],
    ui_state: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    return {
        "schema_version": WORKSPACE_STATE_VERSION,
        "kind": WORKSPACE_STATE_KIND,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "lab_state": copy.deepcopy(dict(lab_state)),
        "ui_state": copy.deepcopy(dict(ui_state or {})),
    }


def unpack_workspace_state(
    document: Mapping[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any], bool]:
    """Return ``(lab_state, ui_state, has_ui_state)``.

    Historical state files stored the lab state directly. They remain valid,
    but cannot restore browser-only state such as alignment guides.
    """
    if (
        document.get("kind") == WORKSPACE_STATE_KIND
        and isinstance(document.get("lab_state"), Mapping)
    ):
        ui_state = document.get("ui_state")
        return (
            copy.deepcopy(dict(document["lab_state"])),
            copy.deepcopy(dict(ui_state)) if isinstance(ui_state, Mapping) else {},
            isinstance(ui_state, Mapping),
        )
    return copy.deepcopy(dict(document)), {}, False

