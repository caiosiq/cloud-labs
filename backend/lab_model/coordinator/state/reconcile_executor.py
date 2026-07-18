"""Execute configuration reconcile plans through the primitive dispatch path."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Sequence

from pydantic import ValidationError

from lab_model.language.primitives.dispatch import execute_validated_command, parse_command_payload
from lab_model.language.primitives.ids import PrimitiveId


class ReconcilePlanError(ValueError):
    """Raised when a reconcile plan cannot be validated or executed."""


RECONCILE_ACTIONS: frozenset[str] = frozenset(
    {
        PrimitiveId.MOVE_COMPONENT,
        PrimitiveId.SET_EXPOSURE,
        PrimitiveId.SET_MOTOR_SETPOINT,
        PrimitiveId.STORE_COMPONENT,
        PrimitiveId.PLACE_FROM_STORAGE,
    }
)


def validate_reconcile_plan(plan: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Validate envelopes and return normalized command dicts."""
    normalized: List[Dict[str, Any]] = []
    for index, envelope in enumerate(plan):
        action = envelope.get("action")
        if not isinstance(action, str) or action not in RECONCILE_ACTIONS:
            raise ReconcilePlanError(
                f"Step {index + 1}: unsupported reconcile action {action!r}"
            )
        try:
            parse_command_payload(dict(envelope))
        except ValidationError as exc:
            raise ReconcilePlanError(
                f"Step {index + 1}: invalid command envelope ({exc})"
            ) from exc
        normalized.append(dict(envelope))
    return normalized


async def execute_reconcile_plan(
    lab: Any,
    plan: Sequence[Mapping[str, Any]],
    *,
    source: str = "checkout",
) -> Dict[str, Any]:
    """Run each reconcile envelope through ``execute_validated_command``."""
    steps = validate_reconcile_plan(plan)
    executed: List[Dict[str, Any]] = []
    for index, envelope in enumerate(steps):
        try:
            cmd = parse_command_payload(envelope)
        except ValidationError as exc:
            raise ReconcilePlanError(
                f"Step {index + 1}: invalid command envelope ({exc})"
            ) from exc
        action = envelope.get("action")
        target = envelope.get("target_id")
        try:
            await execute_validated_command(lab, cmd)
        except Exception as exc:
            # Surface which step failed so the operator can see *why* an
            # "apply on bench" reconcile could not be completed (e.g. a move
            # that would collide, or a place from an empty storage slot).
            raise ReconcilePlanError(
                f"Step {index + 1} of {len(steps)} failed "
                f"({action} on {target}): {exc}"
            ) from exc
        executed.append(envelope)
    return {
        "source": source,
        "steps_executed": len(executed),
        "plan": executed,
    }
