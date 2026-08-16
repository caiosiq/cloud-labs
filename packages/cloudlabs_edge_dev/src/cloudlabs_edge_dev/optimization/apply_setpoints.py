"""Trivial actuate wave for OPTIMIZE inner-loop setpoints (edge-local).

Continuous tunables only (motors, exposure). Breadboard seat moves (MOVE /
STORE / Park) stay on the coordinator ``plan_batch`` path — never fork a seat
DAG inside the hot loop. See ``docs/BATCH_DAG_AND_MATRIX.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

# Keys / substrings that imply seat / inventory motion — refuse in apply_setpoints.
_SPATIAL_KEY_FRAGMENTS = (
    "target_x",
    "target_y",
    "nominal_pose",
    "slot_i",
    "slot_j",
    "in_storage",
    "park",
    "unpark",
    "staging",
    "move_component",
    "store_component",
    "place_from_storage",
)


class SpatialSetpointError(ValueError):
    """Raised when an OPTIMIZE actuate wave includes seat / spatial ops."""


@dataclass(frozen=True)
class SetpointWave:
    """Parsed continuous setpoints ready for parallel apply (trivial DAG)."""

    motors: Dict[str, float] = field(default_factory=dict)
    """Map ``\"{tag_id}.motor.{motor_id}\"`` or raw variable id → angle_deg."""

    exposure_ms: Dict[str, float] = field(default_factory=dict)
    """Map tag_id (or variable id) → exposure_time_ms."""

    other: Dict[str, float] = field(default_factory=dict)
    """Remaining continuous scalars (lab-specific; still non-spatial)."""


def _looks_spatial(key: str) -> bool:
    k = str(key or "").strip().lower()
    if not k:
        return False
    return any(frag in k for frag in _SPATIAL_KEY_FRAGMENTS)


def assert_no_spatial_setpoints(physical_values: Mapping[str, Any]) -> None:
    """Fail closed if any key looks like seat / inventory motion."""
    bad = [str(k) for k in physical_values.keys() if _looks_spatial(str(k))]
    if bad:
        raise SpatialSetpointError(
            "OPTIMIZE inner loop refuses spatial / seat setpoints "
            f"{bad}; use coordinator plan_batch for MOVE/STORE/Park"
        )


def assert_tunables_only_variables(variables: Sequence[Any]) -> None:
    """Refuse pipeline variables whose actuator is breadboard pose (seat motion).

    Continuous motor actuators are allowed. Pose-axis variables imply invasive
    table moves and must not drive the trivial ``apply_setpoints`` wave; keep
    them on touch-and-go router blocks or outside OPTIMIZE entirely.
    """
    bad: List[str] = []
    for var in variables or ():
        vid = str(getattr(var, "id", "") or "")
        act = getattr(var, "actuator", None)
        kind = None
        if act is not None:
            kind = getattr(act, "kind", None)
            if kind is None and isinstance(act, Mapping):
                kind = act.get("kind")
        if str(kind or "").strip().lower() == "pose":
            bad.append(vid or "<unknown>")
    if bad:
        raise SpatialSetpointError(
            "OPTIMIZE tunables-only actuate wave refuses pose variables "
            f"{bad}; breadboard seat motion belongs to plan_batch, not the "
            "inner eval loop"
        )


def parse_setpoint_wave(physical_values: Mapping[str, Any]) -> SetpointWave:
    """Classify a physical map into motors / exposure / other; refuse spatial."""
    assert_no_spatial_setpoints(physical_values)
    motors: Dict[str, float] = {}
    exposure_ms: Dict[str, float] = {}
    other: Dict[str, float] = {}
    for raw_key, raw_val in physical_values.items():
        key = str(raw_key)
        try:
            val = float(raw_val)
        except (TypeError, ValueError) as exc:
            raise SpatialSetpointError(
                f"setpoint {key!r} must be numeric, got {raw_val!r}"
            ) from exc
        kl = key.lower()
        if "exposure" in kl:
            exposure_ms[key] = val
        elif "motor" in kl:
            motors[key] = val
        else:
            other[key] = val
    return SetpointWave(motors=motors, exposure_ms=exposure_ms, other=other)


ApplyFn = Callable[[str, float], None]


def apply_setpoints(
    physical_values: Mapping[str, Any],
    *,
    apply_motor: Optional[ApplyFn] = None,
    apply_exposure: Optional[ApplyFn] = None,
    apply_other: Optional[ApplyFn] = None,
    router: Any = None,
    block_id: str = "setpoints",
) -> SetpointWave:
    """Apply one continuous setpoint wave (trivial DAG — no seat ordering).

    Prefer explicit ``apply_motor`` / ``apply_exposure`` callables. When
    ``router`` is given and implements ``apply_eval``, the full wave is also
    forwarded there (RecordingRouter / lab routers).

    Raises :class:`SpatialSetpointError` on seat / MOVE / STORE-shaped keys.
    """
    wave = parse_setpoint_wave(physical_values)

    if apply_motor is not None:
        for key, val in wave.motors.items():
            apply_motor(key, val)
    if apply_exposure is not None:
        for key, val in wave.exposure_ms.items():
            apply_exposure(key, val)
    if apply_other is not None:
        for key, val in wave.other.items():
            apply_other(key, val)

    if router is not None and hasattr(router, "apply_eval"):
        merged = {**wave.motors, **wave.exposure_ms, **wave.other}
        router.apply_eval(merged, block_id=block_id)

    return wave


__all__ = [
    "SetpointWave",
    "SpatialSetpointError",
    "apply_setpoints",
    "assert_no_spatial_setpoints",
    "assert_tunables_only_variables",
    "parse_setpoint_wave",
]
