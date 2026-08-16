"""Pydantic command bodies for POST /api/command (discriminated by action)."""
from __future__ import annotations

from typing import Annotated, Any, Dict, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

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


class MoveMotorParameters(BaseModel):
    motor_id: int
    distance: float


class SetMotorSetpointParameters(BaseModel):
    motor_id: int
    angle_deg: float


class SetExposureParameters(BaseModel):
    exposure_time_ms: float = Field(..., gt=0.0)


class SetLiveExposureParameters(BaseModel):
    """Preview exposure while a live feed is armed (does not write science tunable)."""

    exposure_time_ms: float = Field(..., gt=0.0)


class SetLaserOutputParameters(BaseModel):
    output_power_mw: float = Field(..., ge=0.0)


class ApplyTunablesPatchParameters(BaseModel):
    """Partial tunables dict; macro applies fields in fixed order (see macros module)."""

    model_config = ConfigDict(extra="allow")

    patch: Dict[str, Any] = Field(default_factory=dict)


class MotorIdParameters(BaseModel):
    motor_id: int


class OptimizeParameters(BaseModel):
    """OPTIMIZE primitive parameters (ensemble only).

    ``objective`` terms may reference ``kernel_id``; optional top-level
    ``kernels`` allowlists edge artifacts. Full ensemble shape is validated
    as ``OptimizeEnsembleParameters`` during preflight.
    """

    model_config = ConfigDict(extra="allow")

    mode: Literal["ensemble"] = Field(
        default="ensemble",
        description="Ensemble OPTIMIZE (Alignment session / SDK run_optimize).",
    )
    #: Edge kernel ids referenced by the objective (inputs).
    kernels: list[str] | None = Field(
        default=None,
        description="Optional allowlist of kernel ids for ensemble OPTIMIZE.",
    )


# --- Command bodies (discriminated union on action) ---


class MoveComponentBody(BaseModel):
    action: Literal["MOVE_COMPONENT"]
    target_id: str = Field(..., min_length=1)
    parameters: PoseTargetParameters


class MoveMotorBody(BaseModel):
    action: Literal["MOVE_MOTOR"]
    target_id: str = Field(..., min_length=1)
    parameters: MoveMotorParameters


class SetMotorSetpointBody(BaseModel):
    action: Literal["SET_MOTOR_SETPOINT"]
    target_id: str = Field(..., min_length=1)
    parameters: SetMotorSetpointParameters


class SetExposureBody(BaseModel):
    action: Literal["SET_EXPOSURE"]
    target_id: str = Field(..., min_length=1)
    parameters: SetExposureParameters


class SetLiveExposureBody(BaseModel):
    """Adjust Tier B preview exposure without changing science ``exposure_time_ms``."""

    action: Literal["SET_LIVE_EXPOSURE"]
    target_id: str = Field(..., min_length=1)
    parameters: SetLiveExposureParameters


class SetLaserOutputBody(BaseModel):
    action: Literal["SET_LASER_OUTPUT"]
    target_id: str = Field(..., min_length=1)
    parameters: SetLaserOutputParameters


class ApplyTunablesPatchBody(BaseModel):
    action: Literal["APPLY_TUNABLES_PATCH"]
    target_id: str = Field(..., min_length=1)
    parameters: ApplyTunablesPatchParameters = Field(default_factory=ApplyTunablesPatchParameters)


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


class EvalKernelParameters(BaseModel):
    """Inputs for the EVAL_KERNEL authoring-probe primitive."""

    kernel_id: str = Field(..., min_length=1)
    #: Measurable field name (e.g. ``camera_image``); capture uses camera BGR today.
    field: str = Field(default="camera_image", min_length=1)
    #: Optional lease for ``session.*`` package roots (also accepted top-level on /api/command).
    lease_id: str | None = None


class EvalKernelBody(BaseModel):
    """Authoring probe: run a catalog/session TorchScript kernel on a tag's capture.

    Kernels are **inputs** (``parameters.kernel_id``), not peer lab verbs.
    """

    action: Literal["EVAL_KERNEL"]
    target_id: str = Field(..., min_length=1)
    parameters: EvalKernelParameters


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


class ConfirmHoldingTagBody(BaseModel):
    """Operator confirms the tag currently in the gripper (clears HOLDING_UNCONFIRMED flag)."""

    action: Literal["CONFIRM_HOLDING_TAG"]
    target_id: str = Field(..., min_length=1)
    parameters: Dict[str, Any] = Field(default_factory=dict)


# --- Per-component TELEOP bodies (Phase 8 / §16.5) ----------------------------


class TeleopSpeedParameters(BaseModel):
    linear_mm_s: float = Field(default=25.0, gt=0)
    angular_deg_s: float = Field(default=15.0, gt=0)


class TeleopGotoParameters(BaseModel):
    """Release-to-go TeleOp: absolute target + motion speed."""

    model_config = ConfigDict(extra="forbid")

    target_pose: Dict[str, float]
    speed: TeleopSpeedParameters = Field(default_factory=TeleopSpeedParameters)

    @model_validator(mode="after")
    def _validate_target(self) -> "TeleopGotoParameters":
        allowed = {"x", "y", "rotation", "z"}
        extras = set(self.target_pose.keys()) - allowed
        if extras:
            raise ValueError(
                f"TELEOP_GOTO target_pose has unknown keys: {sorted(extras)}; "
                f"allowed: {sorted(allowed)}"
            )
        if not self.target_pose:
            raise ValueError("TELEOP_GOTO requires a non-empty target_pose")
        return self


class TeleopJogParameters(BaseModel):
    """One jog frame: an absolute nominal_pose and/or nominal_motor_positions.

    At least one of ``nominal_pose`` / ``nominal_motor_positions`` must be
    provided. Frames are *absolute* (not deltas) so the protocol is
    idempotent under packet loss: dropping a frame just means the next
    frame lands slightly later.

    ``frame_id`` is an optional client-side sequence number for debugging
    and frame-drop accounting; the server never reorders frames by it
    (arrival order wins).
    """

    model_config = ConfigDict(extra="forbid")

    nominal_pose: Optional[Dict[str, float]] = None
    nominal_motor_positions: Optional[Dict[str, float]] = None
    frame_id: Optional[int] = None

    @model_validator(mode="after")
    def _at_least_one_target(self) -> "TeleopJogParameters":
        if self.nominal_pose is None and self.nominal_motor_positions is None:
            raise ValueError(
                "TELEOP_JOG requires at least one of nominal_pose / "
                "nominal_motor_positions"
            )
        if self.nominal_pose is not None:
            allowed = {"x", "y", "rotation", "z"}
            extras = set(self.nominal_pose.keys()) - allowed
            if extras:
                raise ValueError(
                    f"TELEOP_JOG nominal_pose has unknown keys: {sorted(extras)}; "
                    f"allowed: {sorted(allowed)}"
                )
        return self


class StartTeleopBody(BaseModel):
    """Acquire the per-component TELEOP lease for ``target_id``."""

    action: Literal["START_TELEOP"]
    target_id: str = Field(..., min_length=1)
    parameters: Dict[str, Any] = Field(default_factory=dict)


class EndTeleopBody(BaseModel):
    """Release the per-component TELEOP lease for ``target_id`` (idempotent)."""

    action: Literal["END_TELEOP"]
    target_id: str = Field(..., min_length=1)
    parameters: Dict[str, Any] = Field(default_factory=dict)


class StartLiveFeedBody(BaseModel):
    """Connect and start the live feed for ``target_id``."""

    action: Literal["START_LIVE_FEED"]
    target_id: str = Field(..., min_length=1)
    channel: str = "stream"
    parameters: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Optional ``exposure_time_ms`` for preview (VEXP) — does not write "
            "science tunable exposure_time_ms."
        ),
    )


class EndLiveFeedBody(BaseModel):
    """Stop and disconnect the live feed for ``target_id``."""

    action: Literal["END_LIVE_FEED"]
    target_id: str = Field(..., min_length=1)
    channel: str = "all"
    parameters: Dict[str, Any] = Field(default_factory=dict)


class TeleopGotoBody(BaseModel):
    """Move to an absolute target at configured speed under TeleOp."""

    action: Literal["TELEOP_GOTO"]
    target_id: str = Field(..., min_length=1)
    parameters: TeleopGotoParameters


class TeleopJogBody(BaseModel):
    """Push one jog frame to the component currently under TELEOP."""

    action: Literal["TELEOP_JOG"]
    target_id: str = Field(..., min_length=1)
    parameters: TeleopJogParameters


class LocalizeComponentsParameters(BaseModel):
    """Optional explicit tag list; when omitted the edge uses inventory defaults."""

    model_config = ConfigDict(extra="forbid")

    tag_ids: list[str] | None = None
    force_rescan: bool = True


class LocalizeComponentsBody(BaseModel):
    """Deprecated alias of RECORD_TUNABLES for ``nominal_pose`` (LOCALIZE_COMPONENTS)."""

    action: Literal["LOCALIZE_COMPONENTS"]
    target_id: str | None = None
    parameters: LocalizeComponentsParameters = Field(
        default_factory=LocalizeComponentsParameters
    )


class RecordTunablesParameters(BaseModel):
    """Overwrite tunables from world/hardware for the given tags and paths."""

    model_config = ConfigDict(extra="forbid")

    tag_ids: list[str] = Field(..., min_length=1)
    tunable_paths: list[str] = Field(
        ...,
        min_length=1,
        description="Tunable field ids, e.g. nominal_pose, exposure_time_ms.",
    )
    force_rescan: bool = True


class RecordTunablesBody(BaseModel):
    """RECORD_TUNABLES — overwrite specific recordable tunables from the world."""

    action: Literal["RECORD_TUNABLES"]
    target_id: str | None = None
    parameters: RecordTunablesParameters


class SyncRuntimeParameters(BaseModel):
    """Optional tag scope; when omitted, sync all inventory-tracked components."""

    model_config = ConfigDict(extra="forbid")

    tag_ids: list[str] | None = None
    force_rescan: bool = True


class SyncRuntimeBody(BaseModel):
    """SYNC_RUNTIME — RECORD(recordable) + SET(set_at_init) before READY."""

    action: Literal["SYNC_RUNTIME"]
    target_id: str | None = None
    parameters: SyncRuntimeParameters = Field(default_factory=SyncRuntimeParameters)


ValidatedCommand = Annotated[
    Union[
        MoveComponentBody,
        MoveMotorBody,
        SetMotorSetpointBody,
        SetExposureBody,
        SetLiveExposureBody,
        SetLaserOutputBody,
        ApplyTunablesPatchBody,
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
        EvalKernelBody,
        PickComponentBody,
        HoverBody,
        PlaceFromHoverBody,
        ConfirmHoldingTagBody,
        StartTeleopBody,
        EndTeleopBody,
        StartLiveFeedBody,
        EndLiveFeedBody,
        TeleopGotoBody,
        TeleopJogBody,
        LocalizeComponentsBody,
        RecordTunablesBody,
        SyncRuntimeBody,
    ],
    Field(discriminator="action"),
]

COMMAND_ADAPTER = TypeAdapter(ValidatedCommand)
