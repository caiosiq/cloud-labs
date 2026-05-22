"""Pydantic command bodies for POST /api/command (discriminated by action)."""
from __future__ import annotations

from typing import Annotated, Any, Dict, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

# --- Shared parameter shapes ---


class TagQuery(BaseModel):
    """Path/query tag for read primitives (tunables / measurables per component)."""

    tag_id: str = Field(..., min_length=1)


class PoseTargetParameters(BaseModel):
    """Breadboard pose in lab mm / degrees (MOVE_COMPONENT, PLACE_FROM_STORAGE)."""

    model_config = ConfigDict(extra="allow")

    target_x: float
    target_y: float
    rotation: float = 0.0


class HoverParameters(PoseTargetParameters):
    """
    Mid-air pose for HOVER: same fields as PoseTargetParameters, plus ``z``
    (clearance above table in mm). ``z`` is **required** here because HOVER is
    explicitly about where the part sits in the air.
    """

    z: float


class ScanRotateParameters(BaseModel):
    """Constant-rate angular sweep for SCAN_ROTATE_IN_PLACE."""

    model_config = ConfigDict(extra="allow")

    theta_min: float
    theta_max: float
    speed_deg_per_s: float = Field(..., gt=0.0)
    axis: Literal["z"] = "z"


class MoveMotorParameters(BaseModel):
    motor_id: int
    distance: float


class MotorIdParameters(BaseModel):
    motor_id: int


class OptimizeParameters(BaseModel):
    model_config = ConfigDict(extra="allow")

    strategy: str = "NEWTON"


# --- Command bodies (discriminated union on action) ---


class MoveComponentBody(BaseModel):
    action: Literal["MOVE_COMPONENT"]
    target_id: str = Field(..., min_length=1)
    parameters: PoseTargetParameters


class MoveMotorBody(BaseModel):
    action: Literal["MOVE_MOTOR"]
    target_id: str = Field(..., min_length=1)
    parameters: MoveMotorParameters


class MotorSendHomeBody(BaseModel):
    action: Literal["MOTOR_SEND_HOME"]
    target_id: str = Field(..., min_length=1)
    parameters: MotorIdParameters


class MotorSetZeroBody(BaseModel):
    action: Literal["MOTOR_SET_ZERO"]
    target_id: str = Field(..., min_length=1)
    parameters: MotorIdParameters


class OptimizeBody(BaseModel):
    action: Literal["OPTIMIZE"]
    target_id: str = Field(..., min_length=1)
    parameters: OptimizeParameters = Field(default_factory=OptimizeParameters)


class StoreComponentBody(BaseModel):
    action: Literal["STORE_COMPONENT"]
    target_id: str = Field(..., min_length=1)
    parameters: Dict[str, Any] = Field(default_factory=dict)


class PlaceFromStorageBody(BaseModel):
    action: Literal["PLACE_FROM_STORAGE"]
    target_id: str = Field(..., min_length=1)
    parameters: PoseTargetParameters


class AffirmPlacedBody(BaseModel):
    action: Literal["AFFIRM_PLACED_AT_CURRENT"]
    target_id: str = Field(..., min_length=1)
    parameters: Dict[str, Any] = Field(default_factory=dict)


class RepackStorageBody(BaseModel):
    action: Literal["REPACK_STORAGE"]
    target_id: str = Field(..., min_length=1)
    parameters: Dict[str, Any] = Field(default_factory=dict)


class RecenterInStorageBody(BaseModel):
    action: Literal["RECENTER_IN_STORAGE"]
    target_id: str = Field(..., min_length=1)
    parameters: Dict[str, Any] = Field(default_factory=dict)


class ScanBody(BaseModel):
    action: Literal["SCAN"]
    target_id: str | None = None
    parameters: Dict[str, Any] = Field(default_factory=dict)


class RemoveComponentBody(BaseModel):
    action: Literal["REMOVE"]
    target_id: str = Field(..., min_length=1)
    parameters: Dict[str, Any] = Field(default_factory=dict)


class RecordMeasurablesBody(BaseModel):
    action: Literal["RECORD_MEASURABLES"]
    target_id: str = Field(..., min_length=1)
    parameters: Dict[str, Any] = Field(default_factory=dict)


# --- In-air manipulation bodies (see new_primitives.md) -----------------------


class PickComponentBody(BaseModel):
    """Grasp part ``target_id`` on the breadboard; transitions to HOLDING."""

    action: Literal["PICK_COMPONENT"]
    target_id: str = Field(..., min_length=1)
    parameters: Dict[str, Any] = Field(default_factory=dict)


class HoverBody(BaseModel):
    """Re-position an already held part in mid-air (does NOT grasp)."""

    action: Literal["HOVER"]
    target_id: str = Field(..., min_length=1)
    parameters: HoverParameters


class PlaceFromHoverBody(BaseModel):
    """Place a held part on the breadboard; HOLDING → IDLE."""

    action: Literal["PLACE_FROM_HOVER"]
    target_id: str = Field(..., min_length=1)
    parameters: PoseTargetParameters


class ScanRotateInPlaceBody(BaseModel):
    """Sweep the held part's rotation from theta_min to theta_max at constant speed."""

    action: Literal["SCAN_ROTATE_IN_PLACE"]
    target_id: str = Field(..., min_length=1)
    parameters: ScanRotateParameters


class ConfirmHoldingTagBody(BaseModel):
    """Operator confirms the tag currently in the gripper (clears HOLDING_UNCONFIRMED flag)."""

    action: Literal["CONFIRM_HOLDING_TAG"]
    target_id: str = Field(..., min_length=1)
    parameters: Dict[str, Any] = Field(default_factory=dict)


ValidatedCommand = Annotated[
    Union[
        MoveComponentBody,
        MoveMotorBody,
        MotorSendHomeBody,
        MotorSetZeroBody,
        OptimizeBody,
        StoreComponentBody,
        PlaceFromStorageBody,
        AffirmPlacedBody,
        RepackStorageBody,
        RecenterInStorageBody,
        ScanBody,
        RemoveComponentBody,
        RecordMeasurablesBody,
        PickComponentBody,
        HoverBody,
        PlaceFromHoverBody,
        ScanRotateInPlaceBody,
        ConfirmHoldingTagBody,
    ],
    Field(discriminator="action"),
]

COMMAND_ADAPTER = TypeAdapter(ValidatedCommand)
