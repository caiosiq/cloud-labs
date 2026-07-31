"""Software observation epochs for the mock teaching edge."""

from __future__ import annotations

import time
from typing import Any


def now_epoch_ms() -> int:
    """Return the current edge clock as integer milliseconds."""
    return int(time.time() * 1000)


def begin_latch(*, tag_id: str, reason: str = "record") -> dict[str, Any]:
    """Open a software capture epoch for ``tag_id``."""
    return {
        "tag_id": tag_id,
        "reason": reason,
        "t0_ms": now_epoch_ms(),
        "trigger": "software",
    }


def end_latch(handle: dict[str, Any]) -> dict[str, Any]:
    """Close a latch and stamp ``epoch_ms`` with software_approx quality."""
    t0 = int(handle.get("t0_ms") or now_epoch_ms())
    epoch = max(now_epoch_ms(), t0)
    return {
        "epoch_ms": epoch,
        "latch_quality": "software_approx",
        "tag_id": handle.get("tag_id"),
        "reason": handle.get("reason"),
    }


def compensate_latency_ms(device_id: str) -> int:
    """Mock has no HW latency table; always return 0."""
    _ = device_id
    return 0
