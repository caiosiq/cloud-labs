"""CaptureSource protocol — lab implements latched in-process reads."""
from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

from .tensors import EdgeTensor


@runtime_checkable
class CaptureSource(Protocol):
    """Provide tensors for pipeline ``capture`` entries (and optional measurables)."""

    def capture(self, capture_id: str) -> EdgeTensor:
        """Return resident analysis tensor for one capture declaration."""

    def read_measurable(self, path: str, *, tag_id: str) -> float:
        """Read a scalar measurable path (e.g. ``measurables.last_optimization_score``)."""


class StaticCaptureSource:
    """Test/helper source backed by a fixed map of capture_id → EdgeTensor."""

    def __init__(
        self,
        captures: dict[str, EdgeTensor],
        measurables: Optional[dict[tuple[str, str], float]] = None,
    ) -> None:
        self._captures = dict(captures)
        self._measurables = dict(measurables or {})

    def capture(self, capture_id: str) -> EdgeTensor:
        if capture_id not in self._captures:
            raise KeyError(f"unknown capture_id {capture_id!r}")
        return self._captures[capture_id]

    def read_measurable(self, path: str, *, tag_id: str) -> float:
        key = (tag_id, path)
        if key not in self._measurables:
            raise KeyError(f"unknown measurable {path!r} on {tag_id!r}")
        return float(self._measurables[key])


__all__ = ["CaptureSource", "StaticCaptureSource"]
