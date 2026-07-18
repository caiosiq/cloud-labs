"""Allowlisted variable path parsing (client-side; no live state binding)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .errors import PathResolveError

_MOTOR_PATH = re.compile(
    r"^tunables\.nominal_motor_positions\.(?P<motor_id>[0-9]+)$"
)
_POSE_AXIS_PATH = re.compile(
    r"^tunables\.nominal_pose\.(?P<axis>x|y|rotation)$"
)

ALLOWED_VARIABLE_PATHS = (
    "tunables.nominal_motor_positions.<motor_id>",
    "tunables.nominal_pose.x|y|rotation",
)


@dataclass(frozen=True)
class ParsedVariablePath:
    path: str
    kind: str  # "motor" | "pose_axis"
    motor_id: Optional[str] = None
    axis: Optional[str] = None


def parse_variable_path(path: str) -> ParsedVariablePath:
    """Parse and allowlist a variable path string."""
    if not isinstance(path, str) or not path.strip():
        raise PathResolveError(
            path=str(path), tag_id="", reason="path must be a non-empty string"
        )

    motor = _MOTOR_PATH.match(path)
    if motor:
        return ParsedVariablePath(
            path=path, kind="motor", motor_id=motor.group("motor_id")
        )

    pose = _POSE_AXIS_PATH.match(path)
    if pose:
        return ParsedVariablePath(
            path=path, kind="pose_axis", axis=pose.group("axis")
        )

    raise PathResolveError(
        path=path,
        tag_id="",
        reason=(
            "path not in allowlist; supported: " + ", ".join(ALLOWED_VARIABLE_PATHS)
        ),
    )


__all__ = [
    "ALLOWED_VARIABLE_PATHS",
    "ParsedVariablePath",
    "parse_variable_path",
]
