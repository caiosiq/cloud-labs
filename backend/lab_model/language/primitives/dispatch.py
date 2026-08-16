"""Parse, validate, execute, and schedule lab commands (single dispatch path)."""
from __future__ import annotations

import logging
from typing import Any, Dict, Union

from fastapi import BackgroundTasks
from pydantic import ValidationError

from .ids import READ_PRIMITIVE_IDS, PrimitiveId
from .macros.apply_tunables_patch import run_apply_tunables_patch
from .macros.motor_send_home import run_motor_send_home
from .macros.sync_runtime import (
    run_localize_as_record,
    run_record_tunables,
    run_sync_runtime,
)
from .schemas import (
    AffirmPlacedBody,
    ApplyTunablesPatchBody,
    ConfirmHoldingTagBody,
    EndTeleopBody,
    EvalKernelBody,
    HoverBody,
    LocalizeComponentsBody,
    MoveComponentBody,
    MotorSendHomeBody,
    MotorSetZeroBody,
    MoveMotorBody,
    OptimizeBody,
    SetExposureBody,
    SetLiveExposureBody,
    SetLaserOutputBody,
    SetMotorSetpointBody,
    PickComponentBody,
    PlaceFromHoverBody,
    PlaceFromStorageBody,
    RecenterInStorageBody,
    RecordMeasurablesBody,
    RecordTunablesBody,
    RemoveComponentBody,
    RepackStorageBody,
    ScanBody,
    StartTeleopBody,
    StoreComponentBody,
    StartLiveFeedBody,
    EndLiveFeedBody,
    SyncRuntimeBody,
    TagQuery,
    TeleopGotoBody,
    TeleopJogBody,
    COMMAND_ADAPTER,
)

_LOG = logging.getLogger(__name__)


def _log_primitive(
    primitive_id: str,
    target_id: str | None,
    *,
    macro_parent: str | None = None,
) -> None:
    # One consistent line for EVERY command the communicator receives â€” mock or
    # real, interactive drag or a reconcile step from checkout/stash. Gives the
    # server log a clear, uniform "received & executing" trace per primitive.
    _LOG.info(
        "lab command received: %s target=%s%s",
        primitive_id,
        target_id or "-",
        f" macro={macro_parent}" if macro_parent else "",
    )

ValidatedCommand = Union[
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
]


def fetch_read_primitive(
    lab: Any,
    primitive_id: PrimitiveId,
    tag_id: str,
) -> Dict[str, Any]:
    """
    Read-only primitives: tunables, measurables, or parameters for one tag.
    Validates ``tag_id`` via ``TagQuery``. Used by GET routes (not ``POST /api/command``).
    """
    if primitive_id not in READ_PRIMITIVE_IDS:
        raise ValueError(f"Not a read primitive: {primitive_id!r}")
    tq = TagQuery.model_validate({"tag_id": tag_id})
    tid = tq.tag_id
    if primitive_id == PrimitiveId.GET_TUNABLES:
        return lab.return_tunables_for_tag(tid)
    if primitive_id == PrimitiveId.GET_MEASURABLES:
        return lab.return_measurables_for_tag(tid)
    return lab.return_parameters_for_tag(tid)


#: Recipe-step action aliases resolved BEFORE Pydantic validation so recipe
#: JSON can use the shorter / historical names. Keep the mapping flat and
#: obvious -- no regex, no case transformations beyond a strip.
RECIPE_ACTION_ALIASES: Dict[str, str] = {
    "PLACE": "MOVE_COMPONENT",
    # In-air manipulation aliases (see new_primitives.md Â§8 / Â§12 Stage 7).
    "PICK": "PICK_COMPONENT",
    "PLACE_HOVER": "PLACE_FROM_HOVER",
    "CONFIRM_HOLDING": "CONFIRM_HOLDING_TAG",
}


def parse_command_payload(payload: Dict[str, Any]) -> ValidatedCommand:
    """
    Validate POST /api/command (or recipe-shaped) body.

    Recipe aliases (see :data:`RECIPE_ACTION_ALIASES`) are resolved before
    Pydantic validation, so a recipe step may use ``"action": "PICK"`` and
    get validated as ``PICK_COMPONENT``. This keeps recipe files readable
    without forcing the canonical primitive name on authors.
    """
    raw = dict(payload) if payload else {}
    action = raw.get("action")
    if isinstance(action, str):
        canonical = RECIPE_ACTION_ALIASES.get(action.strip())
        if canonical:
            raw["action"] = canonical
    return COMMAND_ADAPTER.validate_python(raw)


async def _invoke_atomic(
    lab: Any,
    cmd: ValidatedCommand,
    *,
    macro_parent: str | None = None,
) -> Dict[str, Any] | None:
    """One ``LabCommunicator`` call per command. ``MOTOR_SEND_HOME`` must not reach hereâ€”use macro path.

    Returns an optional result dict for sync primitives (e.g. EVAL_KERNEL).
    """
    if isinstance(cmd, MoveComponentBody):
        _log_primitive("MOVE_COMPONENT", cmd.target_id, macro_parent=macro_parent)
        await lab.move_component(cmd.target_id, cmd.parameters.model_dump())
    elif isinstance(cmd, MoveMotorBody):
        _log_primitive("MOVE_MOTOR", cmd.target_id, macro_parent=macro_parent)
        p = cmd.parameters
        await lab.move_motor(cmd.target_id, p.motor_id, p.distance)
    elif isinstance(cmd, SetMotorSetpointBody):
        _log_primitive("SET_MOTOR_SETPOINT", cmd.target_id, macro_parent=macro_parent)
        p = cmd.parameters
        await lab.set_motor_setpoint(cmd.target_id, p.motor_id, p.angle_deg)
    elif isinstance(cmd, SetExposureBody):
        _log_primitive("SET_EXPOSURE", cmd.target_id, macro_parent=macro_parent)
        await lab.set_exposure_time_ms(
            cmd.target_id, cmd.parameters.exposure_time_ms
        )
    elif isinstance(cmd, SetLiveExposureBody):
        _log_primitive("SET_LIVE_EXPOSURE", cmd.target_id, macro_parent=macro_parent)
        await lab.set_live_exposure_time_ms(
            cmd.target_id, cmd.parameters.exposure_time_ms
        )
    elif isinstance(cmd, SetLaserOutputBody):
        _log_primitive("SET_LASER_OUTPUT", cmd.target_id, macro_parent=macro_parent)
        await lab.set_output_power_mw(cmd.target_id, cmd.parameters.output_power_mw)
    elif isinstance(cmd, ApplyTunablesPatchBody):
        raise RuntimeError("APPLY_TUNABLES_PATCH must be handled by macro path")
    elif isinstance(cmd, MotorSendHomeBody):
        raise RuntimeError("MOTOR_SEND_HOME must be handled by _macro_motor_send_home")
    elif isinstance(cmd, MotorSetZeroBody):
        _log_primitive("MOTOR_SET_ZERO", cmd.target_id, macro_parent=macro_parent)
        await lab.motor_set_zero(cmd.target_id, cmd.parameters.motor_id)
    elif isinstance(cmd, OptimizeBody):
        _log_primitive("OPTIMIZE", cmd.target_id, macro_parent=macro_parent)
        opt = cmd.parameters
        params = opt.model_dump()
        if params.get("mode") != "ensemble":
            raise ValueError(
                "OPTIMIZE requires parameters.mode=ensemble "
                "(legacy NEWTON/COBYLA strategy mode removed). "
                "Use Alignment session or SDK run_optimize / run_cobyla."
            )
        strategy = params.get("session_label") or "ensemble"
        # Compile edge pipeline document into the OPTIMIZE payload
        # (remote edges read ``parameters.pipeline``; mock may ignore until Phase 2).
        try:
            from lab_model.execution.optimization.pipeline import (
                PipelineCompileError,
                attach_pipeline,
                optional_catalog_map,
            )

            attach_pipeline(params, catalog=optional_catalog_map(lab))
        except PipelineCompileError as exc:
            _LOG.warning(
                "OPTIMIZE ensemble pipeline compile failed for %s: %s",
                cmd.target_id,
                exc,
            )
            raise
        await lab.optimize_component(cmd.target_id, strategy, params)
    elif isinstance(cmd, StoreComponentBody):
        _log_primitive("STORE_COMPONENT", cmd.target_id, macro_parent=macro_parent)
        await lab.store_component(cmd.target_id)
    elif isinstance(cmd, PlaceFromStorageBody):
        _log_primitive("PLACE_FROM_STORAGE", cmd.target_id, macro_parent=macro_parent)
        await lab.place_from_storage(cmd.target_id, cmd.parameters.model_dump())
    elif isinstance(cmd, AffirmPlacedBody):
        _log_primitive("AFFIRM_PLACED_AT_CURRENT", cmd.target_id, macro_parent=macro_parent)
        await lab.affirm_placed_at_current(cmd.target_id)
    elif isinstance(cmd, RepackStorageBody):
        _log_primitive("REPACK_STORAGE", cmd.target_id, macro_parent=macro_parent)
        await lab.repack_storage_slot(cmd.target_id)
    elif isinstance(cmd, RecenterInStorageBody):
        _log_primitive("RECENTER_IN_STORAGE", cmd.target_id, macro_parent=macro_parent)
        await lab.recenter_stored_in_inventory(cmd.target_id)
    elif isinstance(cmd, RecordMeasurablesBody):
        _log_primitive("RECORD_MEASURABLES", cmd.target_id, macro_parent=macro_parent)
        await lab.record_measurables_for_tag(cmd.target_id)
    elif isinstance(cmd, EvalKernelBody):
        _log_primitive("EVAL_KERNEL", cmd.target_id, macro_parent=macro_parent)
        p = cmd.parameters
        lease_id = p.lease_id or getattr(lab, "_command_lease_id", None)
        backend_id = getattr(lab, "_command_backend_id", None)
        return await lab.eval_kernel_for_tag(
            cmd.target_id,
            p.kernel_id,
            field=p.field,
            lease_id=lease_id,
            backend_id=backend_id,
        )
    elif isinstance(cmd, ScanBody):
        _log_primitive("SCAN", cmd.target_id, macro_parent=macro_parent)
        _LOG.warning(
            "Legacy SCAN primitive is a no-op stub; use TeleOp Rz for rotation."
        )
    elif isinstance(cmd, RemoveComponentBody):
        _log_primitive("REMOVE", cmd.target_id, macro_parent=macro_parent)
        await lab.remove_component(cmd.target_id)
    elif isinstance(cmd, PickComponentBody):
        _log_primitive("PICK_COMPONENT", cmd.target_id, macro_parent=macro_parent)
        await lab.pick_component(cmd.target_id, cmd.parameters)
    elif isinstance(cmd, HoverBody):
        _log_primitive("HOVER", cmd.target_id, macro_parent=macro_parent)
        await lab.hover_component(cmd.target_id, cmd.parameters.model_dump())
    elif isinstance(cmd, PlaceFromHoverBody):
        _log_primitive("PLACE_FROM_HOVER", cmd.target_id, macro_parent=macro_parent)
        await lab.place_from_hover(cmd.target_id, cmd.parameters.model_dump())
    elif isinstance(cmd, ConfirmHoldingTagBody):
        _log_primitive("CONFIRM_HOLDING_TAG", cmd.target_id, macro_parent=macro_parent)
        await lab.confirm_holding_tag(cmd.target_id)
    elif isinstance(cmd, StartTeleopBody):
        _log_primitive("START_TELEOP", cmd.target_id, macro_parent=macro_parent)
        lab._teleop_start_params = dict(cmd.parameters or {})
        await lab.start_teleop(cmd.target_id)
    elif isinstance(cmd, EndTeleopBody):
        _log_primitive("END_TELEOP", cmd.target_id, macro_parent=macro_parent)
        await lab.end_teleop(cmd.target_id)
    elif isinstance(cmd, StartLiveFeedBody):
        _log_primitive("START_LIVE_FEED", cmd.target_id, macro_parent=macro_parent)
        params = dict(cmd.parameters or {})
        await lab.start_live_feed(
            cmd.target_id,
            channel=cmd.channel,
            exposure_time_ms=params.get("exposure_time_ms"),
        )
    elif isinstance(cmd, EndLiveFeedBody):
        _log_primitive("END_LIVE_FEED", cmd.target_id, macro_parent=macro_parent)
        await lab.end_live_feed(cmd.target_id, channel=cmd.channel)
    elif isinstance(cmd, TeleopGotoBody):
        _log_primitive("TELEOP_GOTO", cmd.target_id, macro_parent=macro_parent)
        await lab.teleop_goto(cmd.target_id, cmd.parameters.model_dump())
    elif isinstance(cmd, TeleopJogBody):
        _log_primitive("TELEOP_JOG", cmd.target_id, macro_parent=macro_parent)
        params = cmd.parameters.model_dump(exclude_none=True)
        if params.get("nominal_pose") and not params.get("target_pose"):
            params["target_pose"] = params.pop("nominal_pose")
        await lab.teleop_goto(cmd.target_id, params)
    elif isinstance(cmd, LocalizeComponentsBody):
        raise RuntimeError("LOCALIZE_COMPONENTS must be handled by macro path")
    elif isinstance(cmd, RecordTunablesBody):
        _log_primitive("RECORD_TUNABLES", None, macro_parent=macro_parent)
        return await run_record_tunables(lab, cmd)
    elif isinstance(cmd, SyncRuntimeBody):
        raise RuntimeError("SYNC_RUNTIME must be handled by macro path")
    else:
        raise NotImplementedError(type(cmd))
    return None


async def execute_validated_command(
    lab: Any, cmd: ValidatedCommand
) -> Dict[str, Any] | None:
    """Await one command (used by recipe executor). Macros expand into atomic steps.

    Returns an optional result dict for sync primitives such as EVAL_KERNEL.
    """
    from lab_model.coordinator.lab_initialization import (
        LabNotInitializedError,
        ensure_action_allowed,
    )

    action = str(getattr(cmd, "action", "") or "")
    state = lab.get_lab_state() if hasattr(lab, "get_lab_state") else None
    backend_id = str(getattr(lab, "backend_id", "") or "").strip()
    edge_attached = None
    edge_offline = None
    if isinstance(state, dict):
        if not backend_id:
            backend_id = str(state.get("active_backend_id") or "")
        if "edge_attached" in state:
            edge_attached = bool(state.get("edge_attached"))
        if "edge_offline" in state:
            edge_offline = bool(state.get("edge_offline"))
    try:
        ensure_action_allowed(
            backend_id,
            action,
            state,
            edge_attached=edge_attached,
            edge_offline=edge_offline,
        )
    except LabNotInitializedError:
        raise
    except Exception:  # noqa: BLE001
        _LOG.debug("lab_init execute gate failed", exc_info=True)

    if isinstance(cmd, MotorSendHomeBody):
        _log_primitive("MOTOR_SEND_HOME", cmd.target_id)
        await run_motor_send_home(lab, cmd)
        return None
    if isinstance(cmd, ApplyTunablesPatchBody):
        _log_primitive("APPLY_TUNABLES_PATCH", cmd.target_id)
        await run_apply_tunables_patch(lab, cmd)
        return None
    if isinstance(cmd, SyncRuntimeBody):
        _log_primitive("SYNC_RUNTIME", None)
        return await run_sync_runtime(lab, cmd)
    if isinstance(cmd, LocalizeComponentsBody):
        _log_primitive("LOCALIZE_COMPONENTS", None)
        return await run_localize_as_record(lab, cmd)
    return await _invoke_atomic(lab, cmd)


def schedule_validated_command(
    lab: Any,
    cmd: ValidatedCommand,
    background_tasks: BackgroundTasks,
) -> Dict[str, Any]:
    """
    Queue async lab work like the legacy main.py handlers; return HTTP JSON body.
    SCAN is accepted without scheduling lab work (stub).
    """
    from lab_model.coordinator.lab_initialization import (
        LabNotInitializedError,
        ensure_action_allowed,
    )

    action = str(getattr(cmd, "action", "") or "")
    state = lab.get_lab_state() if hasattr(lab, "get_lab_state") else None
    backend_id = str(getattr(lab, "backend_id", "") or "").strip()
    edge_attached = None
    edge_offline = None
    if isinstance(state, dict):
        if not backend_id:
            backend_id = str(state.get("active_backend_id") or "")
        if "edge_attached" in state:
            edge_attached = bool(state.get("edge_attached"))
        if "edge_offline" in state:
            edge_offline = bool(state.get("edge_offline"))
    try:
        ensure_action_allowed(
            backend_id,
            action,
            state,
            edge_attached=edge_attached,
            edge_offline=edge_offline,
        )
    except LabNotInitializedError:
        raise
    except Exception:  # noqa: BLE001
        _LOG.debug("lab_init schedule gate failed", exc_info=True)

    if isinstance(cmd, MoveComponentBody):
        background_tasks.add_task(execute_validated_command, lab, cmd)
        return {"status": "accepted", "message": f"Robot dispatched to move {cmd.target_id}"}

    if isinstance(cmd, MoveMotorBody):
        p = cmd.parameters
        background_tasks.add_task(execute_validated_command, lab, cmd)
        return {
            "status": "accepted",
            "message": f"Motor {p.motor_id} on {cmd.target_id} moving by {p.distance}",
        }

    if isinstance(cmd, SetMotorSetpointBody):
        p = cmd.parameters
        background_tasks.add_task(execute_validated_command, lab, cmd)
        return {
            "status": "accepted",
            "message": (
                f"Motor {p.motor_id} on {cmd.target_id}: setpoint {p.angle_deg:g}Â°"
            ),
        }

    if isinstance(cmd, SetExposureBody):
        p = cmd.parameters
        background_tasks.add_task(execute_validated_command, lab, cmd)
        return {
            "status": "accepted",
            "message": (
                f"Exposure for {cmd.target_id} set to {p.exposure_time_ms:g} ms"
            ),
        }

    if isinstance(cmd, SetLiveExposureBody):
        p = cmd.parameters
        background_tasks.add_task(execute_validated_command, lab, cmd)
        return {
            "status": "accepted",
            "message": (
                f"Live preview exposure for {cmd.target_id} set to "
                f"{p.exposure_time_ms:g} ms"
            ),
        }

    if isinstance(cmd, SetLaserOutputBody):
        p = cmd.parameters
        background_tasks.add_task(execute_validated_command, lab, cmd)
        return {
            "status": "accepted",
            "message": (
                f"Laser output for {cmd.target_id} set to {p.output_power_mw:g} mW"
            ),
        }

    if isinstance(cmd, ApplyTunablesPatchBody):
        background_tasks.add_task(execute_validated_command, lab, cmd)
        return {
            "status": "accepted",
            "message": f"Tunables patch queued for {cmd.target_id}",
        }

    if isinstance(cmd, MotorSendHomeBody):
        mid = cmd.parameters.motor_id
        background_tasks.add_task(execute_validated_command, lab, cmd)
        return {
            "status": "accepted",
            "message": f"Motor {mid} on {cmd.target_id}: send to home (tracked â†’ 0)",
        }

    if isinstance(cmd, MotorSetZeroBody):
        mid = cmd.parameters.motor_id
        background_tasks.add_task(execute_validated_command, lab, cmd)
        return {
            "status": "accepted",
            "message": f"Motor {mid} on {cmd.target_id}: set current position as 0",
        }

    if isinstance(cmd, OptimizeBody):
        opt = cmd.parameters
        params = opt.model_dump()
        if params.get("mode") != "ensemble":
            raise ValueError(
                "OPTIMIZE requires parameters.mode=ensemble "
                "(legacy NEWTON/COBYLA strategy mode removed)."
            )
        label = params.get("session_label") or "ensemble"
        background_tasks.add_task(execute_validated_command, lab, cmd)
        return {
            "status": "accepted",
            "message": f"Ensemble optimization ({label}) started for {cmd.target_id}",
        }

    if isinstance(cmd, StoreComponentBody):
        background_tasks.add_task(execute_validated_command, lab, cmd)
        return {
            "status": "accepted",
            "message": f"Storing {cmd.target_id} in inventory quadrant (packed)",
        }

    if isinstance(cmd, PlaceFromStorageBody):
        background_tasks.add_task(execute_validated_command, lab, cmd)
        return {
            "status": "accepted",
            "message": f"Placing {cmd.target_id} from storage onto breadboard",
        }

    if isinstance(cmd, AffirmPlacedBody):
        background_tasks.add_task(execute_validated_command, lab, cmd)
        return {
            "status": "accepted",
            "message": f"Marking {cmd.target_id} as PLACED at current pose",
        }

    if isinstance(cmd, RepackStorageBody):
        background_tasks.add_task(execute_validated_command, lab, cmd)
        return {
            "status": "accepted",
            "message": f"Repacking {cmd.target_id} into inventory grid",
        }

    if isinstance(cmd, RecenterInStorageBody):
        background_tasks.add_task(execute_validated_command, lab, cmd)
        return {
            "status": "accepted",
            "message": f"Re-centering {cmd.target_id} in its inventory cell at 0Â°",
        }

    if isinstance(cmd, ScanBody):
        return {"status": "accepted", "message": "Scan started"}

    if isinstance(cmd, LocalizeComponentsBody):
        background_tasks.add_task(execute_validated_command, lab, cmd)
        scope = cmd.parameters.tag_ids or []
        note = f" ({', '.join(scope)})" if scope else ""
        return {
            "status": "accepted",
            "message": f"LOCALIZE_COMPONENTS started{note} (alias of RECORD_TUNABLES)",
        }

    if isinstance(cmd, RecordTunablesBody):
        background_tasks.add_task(execute_validated_command, lab, cmd)
        return {
            "status": "accepted",
            "message": (
                f"RECORD_TUNABLES started "
                f"(tags={cmd.parameters.tag_ids}, paths={cmd.parameters.tunable_paths})"
            ),
        }

    if isinstance(cmd, SyncRuntimeBody):
        background_tasks.add_task(execute_validated_command, lab, cmd)
        scope = cmd.parameters.tag_ids or []
        note = f" ({', '.join(scope)})" if scope else ""
        return {
            "status": "accepted",
            "message": f"SYNC_RUNTIME started{note}",
        }

    if isinstance(cmd, RemoveComponentBody):
        background_tasks.add_task(execute_validated_command, lab, cmd)
        return {"status": "accepted", "message": f"Removing {cmd.target_id}"}

    if isinstance(cmd, RecordMeasurablesBody):
        background_tasks.add_task(execute_validated_command, lab, cmd)
        return {
            "status": "accepted",
            "message": f"Measurables recording queued for {cmd.target_id}",
        }

    if isinstance(cmd, EvalKernelBody):
        # Prefer sync path in receive_command; scheduling loses the scalar/features.
        background_tasks.add_task(execute_validated_command, lab, cmd)
        return {
            "status": "accepted",
            "message": f"EVAL_KERNEL queued for {cmd.target_id} ({cmd.parameters.kernel_id})",
        }

    if isinstance(cmd, PickComponentBody):
        background_tasks.add_task(execute_validated_command, lab, cmd)
        return {
            "status": "accepted",
            "message": f"Picking up {cmd.target_id} (will enter HOLDING)",
        }

    if isinstance(cmd, HoverBody):
        p = cmd.parameters
        background_tasks.add_task(execute_validated_command, lab, cmd)
        return {
            "status": "accepted",
            "message": f"Hovering {cmd.target_id} to ({p.target_x:.1f}, {p.target_y:.1f}) "
            f"rot={p.rotation:.1f} z={p.z:.1f}",
        }

    if isinstance(cmd, PlaceFromHoverBody):
        p = cmd.parameters
        background_tasks.add_task(execute_validated_command, lab, cmd)
        return {
            "status": "accepted",
            "message": f"Placing {cmd.target_id} from hover at ({p.target_x:.1f}, {p.target_y:.1f})",
        }

    if isinstance(cmd, ConfirmHoldingTagBody):
        background_tasks.add_task(execute_validated_command, lab, cmd)
        return {
            "status": "accepted",
            "message": f"Operator confirmed held tag: {cmd.target_id}",
        }

    raise NotImplementedError(type(cmd))


def validation_error_detail(exc: ValidationError) -> Any:
    return exc.errors(include_url=False)
