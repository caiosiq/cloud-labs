"""Ensemble / objective validation errors (client-side IR)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class PathResolveError(Exception):
    """A variable or objective path could not be resolved."""

    path: str
    tag_id: str
    reason: str

    def as_dict(self) -> Dict[str, Any]:
        return {"path": self.path, "tag_id": self.tag_id, "reason": self.reason}


@dataclass
class EnsemblePreflightError(Exception):
    """Objective / ensemble spec failed validation."""

    message: str
    errors: List[Dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"message": self.message}
        if self.errors:
            out["errors"] = self.errors
        return out


__all__ = ["EnsemblePreflightError", "PathResolveError"]
