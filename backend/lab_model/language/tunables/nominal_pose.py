"""Tunable plugin: ``nominal_pose`` (table placement intent)."""
from __future__ import annotations

from typing import Any, Dict, Mapping

from lab_model.language.primitives.ids import PrimitiveId

from ._commit import commit_nominal_pose
from .registry import register_tunable


@register_tunable(
    field_id="nominal_pose",
    widget="TablePose",
    write_primitive=PrimitiveId.MOVE_COMPONENT,
)
async def apply(bridge: Any, tag_id: str, pose: Mapping[str, Any]) -> None:
    """Commit placement intent (recipes / APPLY_TUNABLES_PATCH).

    Canvas drag and explicit moves use :meth:`LabCommunicator.move_component`
    (hardware + commit). This ``apply`` is intent-only.
    """
    if not commit_nominal_pose(bridge, tag_id, pose):
        print(f"{bridge.log_prefix} nominal_pose: tag {tag_id!r} not in state")


async def apply_from_move_command(
    bridge: Any, tag_id: str, target_pose: Dict[str, float]
) -> Dict[str, float]:
    """Normalize MOVE_COMPONENT pose dict for commits."""
    tx = float(target_pose.get("target_x", target_pose.get("x", 0.0)))
    ty = float(target_pose.get("target_y", target_pose.get("y", 0.0)))
    trot = float(target_pose.get("rotation", 0.0))
    pose = {"x": tx, "y": ty, "rotation": trot}
    await apply(bridge, tag_id, pose)
    return pose
