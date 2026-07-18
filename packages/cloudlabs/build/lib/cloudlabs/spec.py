"""Pydantic models for objective IR (client authoring; no ensemble kernel validation)."""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BoundsSpec(BaseModel):
    min: float
    max: float

    @model_validator(mode="after")
    def _min_le_max(self) -> "BoundsSpec":
        if self.min > self.max:
            raise ValueError("bounds.min must be <= bounds.max")
        return self


class ObjectiveSourceSpec(BaseModel):
    model_config = ConfigDict(extra="allow")

    tag_id: str = Field(..., min_length=1)
    kind: str
    path: Optional[str] = None
    from_: Optional[str] = Field(default=None, alias="from")
    target_px: Optional[Dict[str, float]] = None
    normalize: Optional[BoundsSpec] = None
    kernel_id: Optional[str] = None
    target_scalar: Optional[float] = None
    feature_index: Optional[Any] = None
    feature_names: Optional[List[str]] = None
    target: Optional[Any] = None
    use_abs: Optional[bool] = None


class ObjectiveTermSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1)
    weight: float
    source: ObjectiveSourceSpec
    metric: str = Field(..., min_length=1)


class ObjectiveSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["weighted_sum"] = "weighted_sum"
    minimize: bool = True
    terms: List[ObjectiveTermSpec] = Field(..., min_length=1)


__all__ = [
    "BoundsSpec",
    "ObjectiveSpec",
    "ObjectiveSourceSpec",
    "ObjectiveTermSpec",
]
