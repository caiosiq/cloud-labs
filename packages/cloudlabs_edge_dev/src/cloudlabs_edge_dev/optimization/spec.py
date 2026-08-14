"""Pydantic models for the edge optimization pipeline document."""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BoundsSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min: float
    max: float


class ActuatorMotor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["motor"] = "motor"
    controller: str = Field(..., min_length=1)
    motor_id: Union[int, str]


class ActuatorPose(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["pose"] = "pose"
    axis: Literal["x", "y", "rotation"]


class PipelineVariable(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1)
    tag_id: str = Field(..., min_length=1)
    actuator: Union[ActuatorMotor, ActuatorPose] = Field(..., discriminator="kind")
    physical_type: Literal["continuous", "invasive_discrete"]
    unit: str = Field(..., min_length=1)
    bounds: BoundsSpec
    delta: bool = True
    step_hint: Optional[float] = None
    touch_and_go: Optional[Dict[str, Any]] = None


class CaptureDecl(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1)
    tag_id: str = Field(..., min_length=1)
    field: str = Field(..., min_length=1)
    exposure_s: Optional[float] = None


class ObjectiveTerm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1)
    weight: float
    metric: str = Field(..., min_length=1)
    capture_id: Optional[str] = None
    kernel_id: Optional[str] = None
    measurable_path: Optional[str] = None
    params: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _kernel_or_measurable(self) -> "ObjectiveTerm":
        has_kernel = bool(self.kernel_id and self.capture_id)
        has_meas = bool(self.measurable_path)
        if not has_kernel and not has_meas:
            raise ValueError(
                f"term {self.id!r}: require kernel_id+capture_id or measurable_path"
            )
        return self


class ObjectiveDecl(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["weighted_sum"] = "weighted_sum"
    minimize: bool = True
    terms: List[ObjectiveTerm] = Field(..., min_length=1)


class SolverBlock(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str = Field(..., min_length=1)
    variable_ids: List[str] = Field(..., min_length=1)
    max_evals: int = Field(default=30, ge=1)
    trust_region_u: float = Field(default=0.2, gt=0.0, le=1.0)
    rhobeg_u: float = Field(default=0.05, gt=0.0, le=1.0)
    rhoend_u: float = Field(default=0.002, gt=0.0, le=1.0)
    passes: int = Field(default=1, ge=1)


class SolverDecl(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: Literal["block_cobyla"] = "block_cobyla"
    max_total_evals: int = Field(default=80, ge=1)
    keep_best: bool = True
    rollback_on_fail: bool = False
    settle_ms: int = Field(default=120, ge=0)
    #: Stop when an eval's total loss is ≤ this (normalized units, e.g. 0.1 ≈ 10% FOV).
    stop_loss: Optional[float] = Field(default=None, ge=0.0)
    #: When beam presence fails, add ``weight * ||Δu||`` (normalized) on top of the
    #: absent penalty, then actuate back to the last present point.
    absent_step_barrier: Optional[float] = Field(default=None, ge=0.0)
    blocks: List[SolverBlock] = Field(..., min_length=1)
    constraints: List[Dict[str, Any]] = Field(default_factory=list)
    normalization: Optional[Dict[str, Any]] = None


class KernelPackage(BaseModel):
    model_config = ConfigDict(extra="allow")

    kernel_id: str = Field(..., min_length=1)
    digest: str = Field(..., min_length=1)
    artifact_b64: Optional[str] = None
    output_kind: Optional[Literal["scalar", "features"]] = None
    feature_names: List[str] = Field(default_factory=list)


class OptimizationPipeline(BaseModel):
    """Edge-executable optimization pipeline (contract schema_version 1)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    session_label: Optional[str] = None
    variables: List[PipelineVariable] = Field(..., min_length=1)
    capture: List[CaptureDecl] = Field(default_factory=list)
    objective: ObjectiveDecl
    solver: SolverDecl
    kernel_packages: List[KernelPackage] = Field(default_factory=list)

    @model_validator(mode="after")
    def _solver_refs_known(self) -> "OptimizationPipeline":
        known = {v.id for v in self.variables}
        for block in self.solver.blocks:
            missing = [vid for vid in block.variable_ids if vid not in known]
            if missing:
                raise ValueError(
                    f"solver block {block.id!r} references unknown variable_ids: {missing}"
                )
        capture_ids = {c.id for c in self.capture}
        for term in self.objective.terms:
            if term.capture_id and term.capture_id not in capture_ids:
                raise ValueError(
                    f"term {term.id!r} references unknown capture_id {term.capture_id!r}"
                )
        return self


def parse_pipeline(raw: Dict[str, Any] | OptimizationPipeline) -> OptimizationPipeline:
    if isinstance(raw, OptimizationPipeline):
        return raw
    return OptimizationPipeline.model_validate(raw)


__all__ = [
    "ActuatorMotor",
    "ActuatorPose",
    "BoundsSpec",
    "CaptureDecl",
    "KernelPackage",
    "ObjectiveDecl",
    "ObjectiveTerm",
    "OptimizationPipeline",
    "PipelineVariable",
    "SolverBlock",
    "SolverDecl",
    "parse_pipeline",
]
