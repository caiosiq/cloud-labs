"""MOTOR_SEND_HOME macro: tracked angle → MOVE_MOTOR by -angle."""
from __future__ import annotations

from lab_communicator.base import LabCommunicator
from lab_model.motor_rotation_store import get_angle

from ..schemas import MoveMotorBody, MoveMotorParameters, MotorSendHomeBody


async def run_motor_send_home(lab: LabCommunicator, cmd: MotorSendHomeBody) -> None:
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
    from ..dispatch import _invoke_atomic

    await _invoke_atomic(lab, child, macro_parent="MOTOR_SEND_HOME")
