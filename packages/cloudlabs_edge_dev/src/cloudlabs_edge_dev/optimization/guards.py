"""Phase 6 hardening guards: clearance + max-delta from applied start (x0)."""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence


class ClearanceError(RuntimeError):
    """Arm / gripper still occludes the optical path."""


class MaxDeltaError(RuntimeError):
    """Candidate step exceeds max_delta_from_start vs applied VC / x0."""


def max_delta_limits(constraints: Sequence[Mapping[str, Any]] | None) -> Optional[Dict[str, float]]:
    """Parse ``solver.constraints`` for an enabled ``max_delta_from_start`` entry.

    Expected shape::

        {"type": "max_delta_from_start", "enabled": true,
         "limits": {"deg": 1.0, "mm": 5.0}}

    Returns ``None`` when the constraint is absent or disabled.
    """
    for raw in constraints or ():
        if not isinstance(raw, Mapping):
            continue
        if str(raw.get("type") or "").strip() != "max_delta_from_start":
            continue
        if raw.get("enabled") is False:
            return None
        limits_raw = raw.get("limits") if isinstance(raw.get("limits"), Mapping) else {}
        out: Dict[str, float] = {}
        for key in ("deg", "mm", "rad"):
            if key in limits_raw:
                try:
                    out[key] = float(limits_raw[key])
                except (TypeError, ValueError):
                    continue
        # Defaults when enabled without explicit limits (tight safety).
        if not out:
            out = {"deg": 1.0, "mm": 5.0}
        return out
    return None


def _unit_family(unit: str) -> str:
    u = (unit or "").strip().lower()
    if u in {"deg", "degree", "degrees", "°"}:
        return "deg"
    if u in {"rad", "radian", "radians"}:
        return "rad"
    if u in {"mm", "millimeter", "millimeters"}:
        return "mm"
    if u in {"m", "meter", "meters"}:
        return "mm"  # convert below
    return u


def check_max_delta(
    physical: Mapping[str, float],
    x0: Mapping[str, float],
    variables: Sequence[Any],
    limits: Mapping[str, float],
) -> None:
    """Raise :class:`MaxDeltaError` if any variable exceeds its unit limit vs ``x0``."""
    by_id = {getattr(v, "id", None): v for v in variables}
    for vid, value in physical.items():
        var = by_id.get(vid)
        if var is None:
            continue
        start = float(x0.get(vid, value))
        delta = abs(float(value) - start)
        family = _unit_family(str(getattr(var, "unit", "") or ""))
        unit = str(getattr(var, "unit", "") or "").strip().lower()
        limit = limits.get(family)
        if limit is None and family == "mm" and "m" in limits:
            limit = float(limits["m"]) * 1000.0
        if unit in {"m", "meter", "meters"} and "mm" in limits:
            # physical is meters; limit is mm
            limit = float(limits["mm"]) / 1000.0
            family = "m"
        if limit is None:
            continue
        if delta > float(limit) + 1e-9:
            raise MaxDeltaError(
                f"variable {vid!r} delta {delta:.6g} {unit or family} "
                f"exceeds max_delta_from_start limit {float(limit):.6g}"
            )


def settle_final_values(
    *,
    x0: Mapping[str, float],
    current: Mapping[str, float],
    best: Mapping[str, float],
    keep_best: bool,
    rollback_on_fail: bool,
    aborted: bool,
) -> Dict[str, float]:
    """Choose the physical point the session should leave actuators at."""
    if aborted and rollback_on_fail:
        return {k: float(v) for k, v in x0.items()}
    if keep_best:
        return {k: float(v) for k, v in best.items()}
    return {k: float(v) for k, v in current.items()}


__all__ = [
    "ClearanceError",
    "MaxDeltaError",
    "check_max_delta",
    "max_delta_limits",
    "settle_final_values",
]
