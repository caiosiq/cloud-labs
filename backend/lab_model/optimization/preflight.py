"""Pre-flight validation for ensemble OPTIMIZE before session lock."""
from __future__ import annotations

from typing import Any, Dict, Mapping

from pydantic import ValidationError

from .errors import EnsemblePreflightError, PathResolveError
from .paths import VariablePathResolver
from .spec import OptimizeEnsembleParameters

# ``OptimizeParameters`` (legacy OPTIMIZE body) defaults ``strategy`` to
# ``NEWTON``; that key must not reach ``OptimizeEnsembleParameters`` (extra=forbid).
_LEGACY_ENSEMBLE_NOISE_KEYS = frozenset({"strategy"})


def strip_legacy_optimize_fields(raw: Mapping[str, Any]) -> Dict[str, Any]:
    """Drop legacy single-tag keys accidentally carried on ensemble payloads."""
    data = dict(raw)
    for key in _LEGACY_ENSEMBLE_NOISE_KEYS:
        data.pop(key, None)
    return data


def parse_ensemble_parameters(raw: Mapping[str, Any]) -> OptimizeEnsembleParameters:
    """Validate JSON shape; raises ``ValidationError`` on schema failure."""
    return OptimizeEnsembleParameters.model_validate(strip_legacy_optimize_fields(raw))


def preflight_ensemble(
    state: Mapping[str, Any],
    raw: Mapping[str, Any],
) -> tuple[OptimizeEnsembleParameters, VariablePathResolver, Dict[str, float]]:
    """
    Parse spec and dry-run resolve every variable path on ``state``.

    Raises :class:`EnsemblePreflightError` with structured detail on failure.
    """
    cleaned = strip_legacy_optimize_fields(raw)
    try:
        spec = parse_ensemble_parameters(cleaned)
    except ValidationError as exc:
        raise EnsemblePreflightError(
            message="ensemble parameters failed schema validation",
            errors=list(exc.errors(include_url=False)),
        ) from exc

    try:
        resolver = VariablePathResolver.resolve(state, spec.variables, writable=False)
    except PathResolveError as exc:
        raise EnsemblePreflightError(
            message="ensemble variable path resolution failed",
            errors=[exc.as_dict()],
        ) from exc

    x0 = resolver.snapshot_x0()
    return spec, resolver, x0


def ensemble_scope_tag_ids(spec: OptimizeEnsembleParameters) -> list[str]:
    """Tags touched by variables or objective sources (for measurables nulling)."""
    tags: set[str] = set()
    for var in spec.variables:
        tags.add(var.tag_id)
        if var.touch_and_go is not None:
            tags.add(var.touch_and_go.gripper_tag)
            home = var.touch_and_go.safe_home_tag or var.touch_and_go.gripper_tag
            tags.add(home)
    for term in spec.objective.terms:
        tags.add(term.source.tag_id)
    if spec.capture is not None:
        for step in spec.capture.before_each_eval:
            tags.add(step.tag_id)
    return sorted(tags)


__all__ = [
    "ensemble_scope_tag_ids",
    "parse_ensemble_parameters",
    "preflight_ensemble",
    "strip_legacy_optimize_fields",
]
