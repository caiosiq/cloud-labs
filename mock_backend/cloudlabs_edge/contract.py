"""Helpers for shaping Edge Contract ``POST /execute`` JSON responses (mock edge)."""

from __future__ import annotations

from typing import Any

CONTRACT_VERSION = "1.1.0"


def completed(
    result: dict[str, Any] | None = None,
    *,
    epoch_ms: int,
    latch_quality: str | None = None,
) -> dict[str, Any]:
    """Build a successful execute response."""
    out: dict[str, Any] = {"status": "completed", "epoch_ms": int(epoch_ms)}
    if result is not None:
        out["result"] = result
    if latch_quality is not None:
        out["latch_quality"] = latch_quality
    return out


def refused(code: str, message: str) -> dict[str, Any]:
    """Build a structured refusal."""
    return {"status": "refused", "error": {"code": code, "message": message}}


def failed(code: str, message: str) -> dict[str, Any]:
    """Build a structured failure after an attempted primitive."""
    return {"status": "failed", "error": {"code": code, "message": message}}
