"""Helpers for shaping Edge Contract ``POST /execute`` JSON responses.

Schema validation for request and response bodies lives in
``cloudlabs-edge-dev``; this module only builds the small envelopes that
adapters and ``dispatch`` should return after a primitive runs. Do not add
new verbs or Twin-only side doors here — every mutation still enters through
a ``PrimitiveId`` string on ``/execute``.
"""

from __future__ import annotations

from typing import Any

CONTRACT_VERSION = "1.1.0"


def completed(
    result: dict[str, Any] | None = None,
    *,
    epoch_ms: int,
    latch_quality: str | None = None,
) -> dict[str, Any]:
    """Build a successful execute response.

    Parameters
    ----------
    result:
        Optional JSON-serializable object describing what the primitive did
        (for example ``{"tag_id": "tag_20", "pose": {...}}``). May be omitted
        when success itself is enough.
    epoch_ms:
        Monotonic bench clock in milliseconds since the Unix epoch (or an
        edge-local monotonic substitute). Required on completed RECORD/EVAL
        and recommended on motion that changes observable state.
    latch_quality:
        Optional string such as ``"hardware_trigger"`` or
        ``"software_approx"`` describing how tightly the epoch matches the
        captured sensors.

    Returns
    -------
    dict
        ``{"status": "completed", "epoch_ms": int, ...}`` with optional
        ``result`` and ``latch_quality`` keys, matching
        ``execute_response.schema.json``.
    """
    out: dict[str, Any] = {"status": "completed", "epoch_ms": int(epoch_ms)}
    if result is not None:
        out["result"] = result
    if latch_quality is not None:
        out["latch_quality"] = latch_quality
    return out


def refused(code: str, message: str) -> dict[str, Any]:
    """Build a structured refusal (policy, lease, or not-yet-implemented).

    Parameters
    ----------
    code:
        Stable machine code (for example ``"TELEOP_NOT_STARTED"`` or
        ``"NOT_IMPLEMENTED"``).
    message:
        Human-readable explanation safe to log and show in Twin/SDK errors.

    Returns
    -------
    dict
        ``{"status": "refused", "error": {"code": ..., "message": ...}}``.
    """
    return {"status": "refused", "error": {"code": code, "message": message}}


def failed(code: str, message: str) -> dict[str, Any]:
    """Build a structured failure after the edge attempted the primitive.

    Use this when hardware or software threw during execution, as opposed to
    a clean policy refusal. Same shape as :func:`refused` but
    ``status="failed"``.
    """
    return {"status": "failed", "error": {"code": code, "message": message}}
