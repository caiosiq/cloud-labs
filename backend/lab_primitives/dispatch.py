"""Parse, validate, execute, and schedule lab commands (single dispatch path)."""
from __future__ import annotations

import logging
from typing import Any, Dict, Union

from fastapi import BackgroundTasks
from pydantic import ValidationError

from lab_communicator.base import LabCommunicator
from lab_model.motor_rotation_store import get_angle

from .ids import READ_PRIMITIVE_IDS, PrimitiveId
from .schemas import (
    AffirmPlacedBody,
    ConfirmHoldingTagBody,
    HoverBody,
    MoveComponentBody,
    MotorSendHomeBody,
    MotorSetZeroBody,
    MoveMotorBody,
    MoveMotorParameters,
    OptimizeBody,
    PickComponentBody,
    PlaceFromHoverBody,
    PlaceFromStorageBody,
    RecenterInStorageBody,
    RecordMeasurablesBody,
    RemoveComponentBody,
    RepackStorageBody,
    ScanBody,
    ScanRotateInPlaceBody,
    StoreComponentBody,
    TagQuery,
    COMMAND_ADAPTER,
)

_LOG = logging.getLogger(__name__)


def _log_primitive(
    primitive_id: str,
    target_id: str | None,
    *,
    macro_parent: str | None = None,
) -> None:
    _LOG.info(
        "lab_primitive primitive_id=%s target_id=%s macro_parent=%s",
        primitive_id,
        target_id or "",
        macro_parent or "",
    )

ValidatedCommand = Union[
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
]


def fetch_read_primitive(
    lab: LabCommunicator,
    primitive_id: PrimitiveId,
    tag_id: str,
) -> Dict[str, Any]:
    """
    Read-only primitives: tunables or measurables for one tag.
    Validates ``tag_id`` via ``TagQuery``. Used by GET routes (not ``POST /api/command``).
    """
    if primitive_id not in READ_PRIMITIVE_IDS:
        raise ValueError(f"Not a read primitive: {primitive_id!r}")
    tq = TagQuery.model_validate({"tag_id": tag_id})
    tid = tq.tag_id
    if primitive_id == PrimitiveId.GET_TUNABLES:
        return lab.return_tunables_for_tag(tid)
    return lab.return_measurables_for_tag(tid)


#: Recipe-step action aliases resolved BEFORE Pydantic validation so recipe
#: JSON can use the shorter / historical names. Keep the mapping flat and
#: obvious -- no regex, no case transformations beyond a strip.
RECIPE_ACTION_ALIASES: Dict[str, str] = {
    "PLACE": "MOVE_COMPONENT",
    # In-air manipulation aliases (see new_primitives.md §8 / §12 Stage 7).
    "PICK": "PICK_COMPONENT",
    "PLACE_HOVER": "PLACE_FROM_HOVER",
    "SCAN_ROTATE": "SCAN_ROTATE_IN_PLACE",
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


async def _macro_motor_send_home(lab: LabCommunicator, cmd: MotorSendHomeBody) -> None:
    """
    Tracked cumulative angle → ``MOVE_MOTOR`` by ``-angle`` (same semantics as mock/real
    ``motor_send_home``, but routed through the atomic primitive so the stack is visible).
    """
    tid = cmd.target_id
    mid = cmd.parameters.motor_id
    cur = get_angle(tid, mid)
    if abs(cur) < 1e-12:
        return
    child = MoveMotorBody(
        action="MOVE_MOTOR",
        target_id=tid,
        parameters=MoveMotorParameters(motor_id=mid, distance=-cur),
    )
    await _invoke_atomic(lab, child, macro_parent="MOTOR_SEND_HOME")


async def _invoke_atomic(
    lab: LabCommunicator,
    cmd: ValidatedCommand,
    *,
    macro_parent: str | None = None,
) -> None:
    """One ``LabCommunicator`` call per command. ``MOTOR_SEND_HOME`` must not reach here—use macro path."""
    if isinstance(cmd, MoveComponentBody):
        _log_primitive("MOVE_COMPONENT", cmd.target_id, macro_parent=macro_parent)
        await lab.move_component(cmd.target_id, cmd.parameters.model_dump())
    elif isinstance(cmd, MoveMotorBody):
        _log_primitive("MOVE_MOTOR", cmd.target_id, macro_parent=macro_parent)
        p = cmd.parameters
        await lab.move_motor(cmd.target_id, p.motor_id, p.distance)
    elif isinstance(cmd, MotorSendHomeBody):
        raise RuntimeError("MOTOR_SEND_HOME must be handled by _macro_motor_send_home")
    elif isinstance(cmd, MotorSetZeroBody):
        _log_primitive("MOTOR_SET_ZERO", cmd.target_id, macro_parent=macro_parent)
        await lab.motor_set_zero(cmd.target_id, cmd.parameters.motor_id)
    elif isinstance(cmd, OptimizeBody):
        _log_primitive("OPTIMIZE", cmd.target_id, macro_parent=macro_parent)
        opt = cmd.parameters
        await lab.optimize_component(cmd.target_id, opt.strategy, opt.model_dump())
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
    elif isinstance(cmd, ScanBody):
        _log_primitive("SCAN", cmd.target_id, macro_parent=macro_parent)
        _LOG.warning(
            "Legacy SCAN primitive is a no-op stub; use SCAN_ROTATE_IN_PLACE instead."
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
    elif isinstance(cmd, ScanRotateInPlaceBody):
        _log_primitive("SCAN_ROTATE_IN_PLACE", cmd.target_id, macro_parent=macro_parent)
        await lab.scan_rotate_in_place(cmd.target_id, cmd.parameters.model_dump())
    elif isinstance(cmd, ConfirmHoldingTagBody):
        _log_primitive("CONFIRM_HOLDING_TAG", cmd.target_id, macro_parent=macro_parent)
        await lab.confirm_holding_tag(cmd.target_id)
    else:
        raise NotImplementedError(type(cmd))


async def execute_validated_command(lab: LabCommunicator, cmd: ValidatedCommand) -> None:
    """Await one command (used by recipe executor). Macros expand into atomic steps."""
    if isinstance(cmd, MotorSendHomeBody):
        _log_primitive("MOTOR_SEND_HOME", cmd.target_id)
        await _macro_motor_send_home(lab, cmd)
        return
    await _invoke_atomic(lab, cmd)


def schedule_validated_command(
    lab: LabCommunicator,
    cmd: ValidatedCommand,
    background_tasks: BackgroundTasks,
) -> Dict[str, Any]:
    """
    Queue async lab work like the legacy main.py handlers; return HTTP JSON body.
    SCAN is accepted without scheduling lab work (stub).
    """
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

    if isinstance(cmd, MotorSendHomeBody):
        mid = cmd.parameters.motor_id
        background_tasks.add_task(execute_validated_command, lab, cmd)
        return {
            "status": "accepted",
            "message": f"Motor {mid} on {cmd.target_id}: send to home (tracked → 0)",
        }

    if isinstance(cmd, MotorSetZeroBody):
        mid = cmd.parameters.motor_id
        background_tasks.add_task(execute_validated_command, lab, cmd)
        return {
            "status": "accepted",
            "message": f"Motor {mid} on {cmd.target_id}: set current position as 0",
        }

    if isinstance(cmd, OptimizeBody):
        strat = cmd.parameters.strategy
        background_tasks.add_task(execute_validated_command, lab, cmd)
        return {
            "status": "accepted",
            "message": f"Optimization ({strat}) started for {cmd.target_id}",
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
            "message": f"Re-centering {cmd.target_id} in its inventory cell at 0°",
        }

    if isinstance(cmd, ScanBody):
        return {"status": "accepted", "message": "Scan started"}

    if isinstance(cmd, RemoveComponentBody):
        background_tasks.add_task(execute_validated_command, lab, cmd)
        return {"status": "accepted", "message": f"Removing {cmd.target_id}"}

    if isinstance(cmd, RecordMeasurablesBody):
        background_tasks.add_task(execute_validated_command, lab, cmd)
        return {
            "status": "accepted",
            "message": f"Measurables recording queued for {cmd.target_id}",
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

    if isinstance(cmd, ScanRotateInPlaceBody):
        p = cmd.parameters
        background_tasks.add_task(execute_validated_command, lab, cmd)
        return {
            "status": "accepted",
            "message": (
                f"Scan-rotate {cmd.target_id}: {p.theta_min:.1f}° → {p.theta_max:.1f}° "
                f"@ {p.speed_deg_per_s:g}°/s (axis={p.axis})"
            ),
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
