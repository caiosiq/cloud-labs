"""Objective graph compiler — authoring IR → ensemble ObjectiveSpec (Phase E)."""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Union

from pydantic import ValidationError

from .errors import EnsemblePreflightError
from .graph import (
    ALLOWED_FIELD_METRICS,
    DEFAULT_FIELD_METRICS,
    ObjectiveGraphSpec,
    ObjectiveGraphTermSpec,
)
from .spec import BoundsSpec, ObjectiveSpec, ObjectiveSourceSpec, ObjectiveTermSpec


class ObjectiveCompileError(ValueError):
    """Graph term could not be lowered to runtime IR."""

    def __init__(self, term_id: str, reason: str) -> None:
        self.term_id = term_id
        self.reason = reason
        super().__init__(f"term {term_id!r}: {reason}")

    def as_dict(self) -> Dict[str, str]:
        return {"term_id": self.term_id, "reason": self.reason}


def _default_target_px() -> Dict[str, float]:
    return {"x": 512.0, "y": 384.0}


def _resolve_metric(term: ObjectiveGraphTermSpec) -> str:
    if term.metric:
        return term.metric
    if not term.field:
        raise ObjectiveCompileError(term.id, "metric required when source is explicit")
    default = DEFAULT_FIELD_METRICS.get(term.field)
    if not default:
        raise ObjectiveCompileError(
            term.id,
            f"no default metric for field {term.field!r}; set metric explicitly",
        )
    return default


def _validate_field_metric(term: ObjectiveGraphTermSpec, metric: str) -> None:
    if not term.field:
        return
    allowed = ALLOWED_FIELD_METRICS.get(term.field)
    if allowed is not None and metric not in allowed:
        raise ObjectiveCompileError(
            term.id,
            f"metric {metric!r} not allowed for field {term.field!r}; "
            f"allowed: {sorted(allowed)}",
        )


def lower_graph_term(term: ObjectiveGraphTermSpec) -> ObjectiveTermSpec:
    """Lower one authoring term to runtime :class:`ObjectiveTermSpec`."""
    if term.source is not None:
        metric = term.metric
        if not metric:
            raise ObjectiveCompileError(term.id, "metric required with explicit source")
        return ObjectiveTermSpec(
            id=term.id,
            weight=term.weight,
            source=term.source,
            metric=metric,
        )

    field = str(term.field or "").strip()
    if not field:
        raise ObjectiveCompileError(term.id, "field is required")

    metric = _resolve_metric(term)
    _validate_field_metric(term, metric)

    if field == "camera_image":
        if metric != "rms_distance_px":
            raise ObjectiveCompileError(
                term.id,
                "camera_image only supports metric rms_distance_px in MVP",
            )
        target = term.target_px if term.target_px is not None else _default_target_px()
        source = ObjectiveSourceSpec(
            tag_id=term.tag_id,
            kind="derived_centroid",
            **{"from": "measurables.camera_image"},
            target_px=dict(target),
        )
        return ObjectiveTermSpec(
            id=term.id,
            weight=term.weight,
            source=source,
            metric=metric,
        )

    path = f"measurables.{field}"
    normalize: Optional[BoundsSpec] = term.normalize
    if normalize is None and field == "output_power_readback_mw":
        normalize = BoundsSpec(min=0.0, max=1.0)

    source = ObjectiveSourceSpec(
        tag_id=term.tag_id,
        kind="measurable_scalar",
        path=path,
        normalize=normalize,
    )
    return ObjectiveTermSpec(
        id=term.id,
        weight=term.weight,
        source=source,
        metric=metric,
    )


def compile_objective_graph(graph: Union[ObjectiveGraphSpec, Mapping[str, Any]]) -> ObjectiveSpec:
    """Compile authoring graph JSON to runtime :class:`ObjectiveSpec`."""
    if not isinstance(graph, ObjectiveGraphSpec):
        try:
            graph = ObjectiveGraphSpec.model_validate(graph)
        except ValidationError as exc:
            raise EnsemblePreflightError(
                message="objective graph failed schema validation",
                errors=list(exc.errors(include_url=False)),
            ) from exc

    terms: list[ObjectiveTermSpec] = []
    errors: list[Dict[str, str]] = []
    for term in graph.terms:
        try:
            terms.append(lower_graph_term(term))
        except ObjectiveCompileError as exc:
            errors.append(exc.as_dict())

    if errors:
        raise EnsemblePreflightError(
            message="objective graph compilation failed",
            errors=errors,
        )

    return ObjectiveSpec(
        type=graph.type,
        minimize=graph.minimize,
        terms=terms,
    )


def _is_authoring_graph(data: Mapping[str, Any]) -> bool:
    if data.get("version") == 1:
        return True
    terms = data.get("terms")
    if not isinstance(terms, list) or not terms:
        return False
    first = terms[0]
    return isinstance(first, dict) and "field" in first


def compile_objective_payload(raw: Mapping[str, Any]) -> Dict[str, Any]:
    """
    Accept authoring graph or already-runtime objective dict.

    Returns ``objective`` as a plain dict suitable for ``OptimizeEnsembleParameters``.
    """
    if not isinstance(raw, Mapping):
        raise EnsemblePreflightError(
            message="objective payload must be an object",
            errors=[],
        )

    data = dict(raw)
    if _is_authoring_graph(data):
        spec = compile_objective_graph(data)
        return spec.model_dump(by_alias=True)

    try:
        spec = ObjectiveSpec.model_validate(data)
    except ValidationError as exc:
        raise EnsemblePreflightError(
            message="objective failed schema validation",
            errors=list(exc.errors(include_url=False)),
        ) from exc
    return spec.model_dump(by_alias=True)


__all__ = [
    "ObjectiveCompileError",
    "compile_objective_graph",
    "compile_objective_payload",
    "lower_graph_term",
]
