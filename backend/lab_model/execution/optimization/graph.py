"""Declarative objective graph authoring IR (Phase E).

Authoring terms reference catalog **measurable fields** (``camera_image``,
``output_power_readback_mw``, …). The compiler lowers them to runtime
:class:`~lab_model.execution.optimization.spec.ObjectiveSourceSpec` shapes consumed by
ensemble backends and the metric registry.
"""
from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .spec import BoundsSpec, ObjectiveSourceSpec

# Default metric per measurable field (mirrors frontend MEASURABLE_OBJECTIVE_CONFIG).
DEFAULT_FIELD_METRICS: Dict[str, str] = {
    "camera_image": "rms_distance_px",
    "output_power_readback_mw": "one_minus_normalized",
}

# Metrics allowed per field when ``metric`` is omitted.
ALLOWED_FIELD_METRICS: Dict[str, frozenset[str]] = {
    "camera_image": frozenset({"rms_distance_px"}),
    "output_power_readback_mw": frozenset({"one_minus_normalized"}),
}


class ObjectiveGraphTermSpec(BaseModel):
    """One weighted objective term in authoring form."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1)
    weight: float
    tag_id: str = Field(..., min_length=1)
    field: Optional[str] = Field(default=None, min_length=1)
    metric: Optional[str] = Field(default=None, min_length=1)
    target_px: Optional[Dict[str, float]] = None
    normalize: Optional[BoundsSpec] = None
    source: Optional[ObjectiveSourceSpec] = None

    @model_validator(mode="after")
    def _field_or_source(self) -> "ObjectiveGraphTermSpec":
        if self.source is None and not self.field:
            raise ValueError("term requires field or explicit source")
        if self.source is not None and self.field is not None:
            raise ValueError("term cannot set both field and source")
        return self


class ObjectiveGraphSpec(BaseModel):
    """Authoring-time objective graph (flat weighted-sum terms in MVP)."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    type: Literal["weighted_sum"] = "weighted_sum"
    minimize: bool = True
    terms: List[ObjectiveGraphTermSpec] = Field(..., min_length=1)


__all__ = [
    "ALLOWED_FIELD_METRICS",
    "DEFAULT_FIELD_METRICS",
    "ObjectiveGraphSpec",
    "ObjectiveGraphTermSpec",
]
