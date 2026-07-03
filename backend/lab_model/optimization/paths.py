"""Resolve variable dot-paths against live runtime state."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from lab_model.domain.component import get_tunables

from .errors import PathResolveError
from .spec import VariableRef

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


@dataclass
class VariableBinding:
    """Typed getter/setter for one optimization variable."""

    variable: VariableRef
    parsed: ParsedVariablePath
    get_value: Callable[[], float]
    set_value: Callable[[float], None]

    @property
    def tag_id(self) -> str:
        return self.variable.tag_id

    @property
    def path(self) -> str:
        return self.variable.path


def parse_variable_path(path: str) -> ParsedVariablePath:
    """Parse and allowlist a variable path string."""
    if not isinstance(path, str) or not path.strip():
        raise PathResolveError(path=str(path), tag_id="", reason="path must be a non-empty string")

    motor = _MOTOR_PATH.match(path)
    if motor:
        return ParsedVariablePath(path=path, kind="motor", motor_id=motor.group("motor_id"))

    pose = _POSE_AXIS_PATH.match(path)
    if pose:
        return ParsedVariablePath(path=path, kind="pose_axis", axis=pose.group("axis"))

    raise PathResolveError(
        path=path,
        tag_id="",
        reason=(
            "path not in allowlist; supported: "
            + ", ".join(ALLOWED_VARIABLE_PATHS)
        ),
    )


def _component_entry(state: Mapping[str, Any], tag_id: str) -> Optional[Dict[str, Any]]:
    components = state.get("components") or {}
    if not isinstance(components, dict):
        return None
    entry = components.get(tag_id)
    return entry if isinstance(entry, dict) else None


def _require_numeric(value: Any, *, path: str, tag_id: str) -> float:
    if value is None:
        raise PathResolveError(path=path, tag_id=tag_id, reason="path resolves to null")
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise PathResolveError(
            path=path,
            tag_id=tag_id,
            reason=f"path value is not numeric: {value!r}",
        ) from exc
    if not (out == out and abs(out) != float("inf")):  # NaN / inf guard
        raise PathResolveError(path=path, tag_id=tag_id, reason="path value is not finite")
    return out


def _bind_one(
    state: Mapping[str, Any],
    variable: VariableRef,
    *,
    writable: bool,
) -> VariableBinding:
    parsed = parse_variable_path(variable.path)
    entry = _component_entry(state, variable.tag_id)
    if entry is None:
        raise PathResolveError(
            path=variable.path,
            tag_id=variable.tag_id,
            reason=f"tag {variable.tag_id!r} not found in runtime state",
        )

    tunables = get_tunables(entry)

    if parsed.kind == "motor":
        assert parsed.motor_id is not None
        motors = tunables.get("nominal_motor_positions")
        if not isinstance(motors, dict):
            raise PathResolveError(
                path=variable.path,
                tag_id=variable.tag_id,
                reason="tunables.nominal_motor_positions is missing or not a dict",
            )
        key = str(parsed.motor_id)
        if key not in motors:
            raise PathResolveError(
                path=variable.path,
                tag_id=variable.tag_id,
                reason=f"motor_id {parsed.motor_id!r} not present in nominal_motor_positions",
            )

        def get_value() -> float:
            return _require_numeric(
                get_tunables(_component_entry(state, variable.tag_id) or {}).get(
                    "nominal_motor_positions", {}
                ).get(key),
                path=variable.path,
                tag_id=variable.tag_id,
            )

        def set_value(val: float) -> None:
            if not writable:
                return
            live = _component_entry(state, variable.tag_id)
            if live is None:
                return
            bucket = get_tunables(live)
            motors_live = bucket.setdefault("nominal_motor_positions", {})
            if isinstance(motors_live, dict):
                motors_live[key] = float(val)

    else:
        assert parsed.axis is not None
        pose = tunables.get("nominal_pose")
        if not isinstance(pose, dict):
            raise PathResolveError(
                path=variable.path,
                tag_id=variable.tag_id,
                reason="tunables.nominal_pose is missing or not a dict",
            )
        if parsed.axis not in pose:
            raise PathResolveError(
                path=variable.path,
                tag_id=variable.tag_id,
                reason=f"axis {parsed.axis!r} not present in nominal_pose",
            )
        _require_numeric(pose.get(parsed.axis), path=variable.path, tag_id=variable.tag_id)

        def get_value() -> float:
            live = _component_entry(state, variable.tag_id)
            if live is None:
                raise PathResolveError(
                    path=variable.path,
                    tag_id=variable.tag_id,
                    reason=f"tag {variable.tag_id!r} not found in runtime state",
                )
            pose_live = get_tunables(live).get("nominal_pose") or {}
            return _require_numeric(
                pose_live.get(parsed.axis),
                path=variable.path,
                tag_id=variable.tag_id,
            )

        def set_value(val: float) -> None:
            if not writable:
                return
            live = _component_entry(state, variable.tag_id)
            if live is None:
                return
            bucket = get_tunables(live)
            pose_live = bucket.setdefault("nominal_pose", {})
            if isinstance(pose_live, dict):
                pose_live[parsed.axis] = float(val)

    # Dry-run get once to prove readability at bind time.
    get_value()

    return VariableBinding(
        variable=variable,
        parsed=parsed,
        get_value=get_value,
        set_value=set_value,
    )


class VariablePathResolver:
    """Bind all ensemble variables against one runtime snapshot."""

    def __init__(self, bindings: Sequence[VariableBinding]) -> None:
        self._bindings = list(bindings)
        self._by_id = {b.variable.id: b for b in self._bindings}

    @classmethod
    def resolve(
        cls,
        state: Mapping[str, Any],
        variables: Sequence[VariableRef],
        *,
        writable: bool = False,
    ) -> "VariablePathResolver":
        bindings: List[VariableBinding] = []
        seen_ids: set[str] = set()
        for var in variables:
            if var.id in seen_ids:
                raise PathResolveError(
                    path=var.path,
                    tag_id=var.tag_id,
                    reason=f"duplicate variable id {var.id!r}",
                )
            seen_ids.add(var.id)
            try:
                bindings.append(_bind_one(state, var, writable=writable))
            except PathResolveError as exc:
                if not exc.tag_id:
                    exc.tag_id = var.tag_id
                raise
        return cls(bindings)

    @property
    def bindings(self) -> Tuple[VariableBinding, ...]:
        return tuple(self._bindings)

    def get(self, variable_id: str) -> float:
        return self._by_id[variable_id].get_value()

    def set(self, variable_id: str, value: float) -> None:
        self._by_id[variable_id].set_value(value)

    def snapshot_x0(self) -> Dict[str, float]:
        return {b.variable.id: b.get_value() for b in self._bindings}


__all__ = [
    "ALLOWED_VARIABLE_PATHS",
    "ParsedVariablePath",
    "VariableBinding",
    "VariablePathResolver",
    "parse_variable_path",
]
