"""Pydantic models for ensemble OPTIMIZE payloads."""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class BoundsSpec(BaseModel):
    min: float
    max: float

    @model_validator(mode="after")
    def _min_le_max(self) -> "BoundsSpec":
        if self.min > self.max:
            raise ValueError("bounds.min must be <= bounds.max")
        return self


class TouchAndGoSpec(BaseModel):
    required: bool = True
    gripper_tag: str = Field(..., min_length=1)
    safe_home_tag: Optional[str] = None
    settle_ms_after_move: int = Field(default=250, ge=0)
    settle_ms_after_release: int = Field(default=450, ge=0)
    settle_ms_after_reengage: int = Field(default=150, ge=0)
    re_engage_on_demand: bool = True
    re_engage_after_measure: bool = False
    measure_only_while_released: bool = True


class VariableRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1)
    tag_id: str = Field(..., min_length=1)
    path: str = Field(..., min_length=1)
    kind: Literal["continuous"] = "continuous"
    physical_type: Literal["continuous", "invasive_discrete"]
    unit: str = Field(..., min_length=1)
    bounds: BoundsSpec
    delta: bool = True
    step_hint: Optional[float] = None
    touch_and_go: Optional[TouchAndGoSpec] = None

    @model_validator(mode="after")
    def _invasive_requires_touch_and_go(self) -> "VariableRef":
        if self.physical_type == "invasive_discrete" and self.touch_and_go is None:
            raise ValueError(
                f"variable {self.id!r}: invasive_discrete requires touch_and_go"
            )
        if self.physical_type == "continuous" and self.touch_and_go is not None:
            raise ValueError(
                f"variable {self.id!r}: touch_and_go is only valid for invasive_discrete"
            )
        return self


class ObjectiveSourceSpec(BaseModel):
    model_config = ConfigDict(extra="allow")

    tag_id: str = Field(..., min_length=1)
    kind: str
    path: Optional[str] = None
    from_: Optional[str] = Field(default=None, alias="from")
    target_px: Optional[Dict[str, float]] = None
    normalize: Optional[BoundsSpec] = None
    #: Approved TorchScript kernel id (``kind == \"torchscript_scalar\"`` / features).
    kernel_id: Optional[str] = None
    #: Target scalar ``M0`` for ``squared_error`` metric (baked into the job).
    target_scalar: Optional[float] = None
    #: Feature channel index (int) or pair (list) for vector kernels.
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


class SolverConstraintSpec(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str
    enabled: bool = True


class NormalizationSpec(BaseModel):
    space: Literal["unit_hypercube"] = "unit_hypercube"
    per_dimension: Optional[List[float]] = None


class SolverBlockSpec(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str = Field(..., min_length=1)
    variable_ids: List[str] = Field(..., min_length=1)
    max_evals: int = Field(default=30, ge=1)
    trust_region_u: float = Field(default=0.2, gt=0.0, le=1.0)
    rhobeg_u: float = Field(default=0.05, gt=0.0, le=1.0)
    rhoend_u: float = Field(default=0.002, gt=0.0, le=1.0)
    passes: int = Field(default=1, ge=1)


class SolverSpec(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: Literal["block_cobyla"] = "block_cobyla"
    max_total_evals: int = Field(default=80, ge=1)
    keep_best: bool = True
    rollback_on_fail: bool = False
    settle_ms: int = Field(default=120, ge=0)
    normalization: NormalizationSpec = Field(default_factory=NormalizationSpec)
    blocks: List[SolverBlockSpec] = Field(..., min_length=1)
    constraints: List[SolverConstraintSpec] = Field(default_factory=list)


class CaptureStepSpec(BaseModel):
    model_config = ConfigDict(extra="allow")

    tag_id: str = Field(..., min_length=1)
    kind: str
    duration_ms: Optional[int] = None


class CaptureSpec(BaseModel):
    before_each_eval: List[CaptureStepSpec] = Field(default_factory=list)


class TelemetrySpec(BaseModel):
    stream: str = "optimization-ensemble"
    include: List[str] = Field(default_factory=list)


class OptimizeEnsembleParameters(BaseModel):
    """Full ensemble payload under ``parameters`` when ``mode == \"ensemble\"``."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["ensemble"] = "ensemble"
    session_label: Optional[str] = None
    variables: List[VariableRef] = Field(..., min_length=1)
    objective: ObjectiveSpec
    solver: SolverSpec
    capture: Optional[CaptureSpec] = None
    telemetry: Optional[TelemetrySpec] = None
    #: Edge kernel ids (same catalog as job ``kernels[]``); threaded into eval.
    kernels: List[str] = Field(default_factory=list)

    @field_validator("kernels", mode="before")
    @classmethod
    def _validate_kernels(cls, value: Any) -> List[str]:
        from lab_model.optimization.kernels import validate_kernel_ids

        return validate_kernel_ids(value)

    @model_validator(mode="after")
    def _solver_references_known_variables(self) -> "OptimizeEnsembleParameters":
        known = {v.id for v in self.variables}
        for block in self.solver.blocks:
            missing = [vid for vid in block.variable_ids if vid not in known]
            if missing:
                raise ValueError(
                    f"solver block {block.id!r} references unknown variable_ids: {missing}"
                )
        return self


__all__ = [
    "BoundsSpec",
    "CaptureSpec",
    "CaptureStepSpec",
    "ObjectiveSpec",
    "ObjectiveSourceSpec",
    "ObjectiveTermSpec",
    "OptimizeEnsembleParameters",
    "SolverBlockSpec",
    "SolverConstraintSpec",
    "SolverSpec",
    "TelemetrySpec",
    "TouchAndGoSpec",
    "VariableRef",
]
