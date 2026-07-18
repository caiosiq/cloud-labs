"""Initialization policies for scripted jobs and SDK reconcile (Phase G)."""
from __future__ import annotations

from typing import Literal, Optional

InitializationPolicy = Literal["force_reconcile", "stash_and_start", "strict"]

_DEFAULT: InitializationPolicy = "force_reconcile"
_ALLOWED = frozenset({"force_reconcile", "stash_and_start", "strict"})


def normalize_initialization_policy(value: Optional[str]) -> InitializationPolicy:
    """Return a valid policy; unknown values default to ``force_reconcile``."""
    if value is None:
        return _DEFAULT
    normalized = str(value).strip().lower().replace("-", "_")
    if normalized in _ALLOWED:
        return normalized  # type: ignore[return-value]
    return _DEFAULT
