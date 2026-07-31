"""TorchScript-shaped kernel host for the mock edge (teaching stub)."""

from __future__ import annotations

from typing import Any, Dict

_CACHE: Dict[str, Any] = {}


def load_kernel(kernel_id: str) -> Any:
    """Return a cached stub module object for ``kernel_id``."""
    if kernel_id not in _CACHE:
        _CACHE[kernel_id] = {"kernel_id": kernel_id, "kind": "mock_stub"}
    return _CACHE[kernel_id]


def eval_on_bgr(
    kernel_id: str,
    bgr: Any,
    *,
    args: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Score a BGR frame with a deterministic teaching stub.

    Returns a scalar in ``[0, 1]`` derived from mean pixel intensity when
    ``bgr`` looks like an array; otherwise returns ``0.0``.
    """
    _ = args
    load_kernel(kernel_id)
    score = 0.0
    try:
        import numpy as np

        arr = np.asarray(bgr)
        if arr.size:
            score = float(np.clip(arr.mean() / 255.0, 0.0, 1.0))
    except Exception:  # noqa: BLE001
        score = 0.0
    return {
        "kernel_id": kernel_id,
        "scalar": score,
        "score": score,
        "features": [score],
        "passed": True,
    }


def unload_kernel(kernel_id: str) -> None:
    """Drop a cached stub kernel."""
    _CACHE.pop(kernel_id, None)
