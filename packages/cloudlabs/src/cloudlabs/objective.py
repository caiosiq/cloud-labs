"""SDK helpers for declarative objective graphs."""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Union

from .compiler import compile_objective_graph, compile_objective_payload
from .errors import EnsemblePreflightError
from .graph import ObjectiveGraphSpec, ObjectiveGraphTermSpec
from .spec import BoundsSpec, ObjectiveSourceSpec, ObjectiveSpec

__all__ = [
    "ObjectiveGraphBuilder",
    "compile_objective",
    "compile_objective_graph",
    "objective_term",
    "preflight_compiled_objective",
]


def objective_term(
    *,
    term_id: str,
    tag_id: str,
    field: str,
    weight: float = 1.0,
    metric: Optional[str] = None,
    target_px: Optional[Dict[str, float]] = None,
    normalize: Optional[Union[BoundsSpec, Dict[str, float]]] = None,
) -> Dict[str, Any]:
    """Build one authoring-term dict for :func:`compile_objective`."""
    payload: Dict[str, Any] = {
        "id": term_id,
        "tag_id": tag_id,
        "field": field,
        "weight": weight,
    }
    if metric is not None:
        payload["metric"] = metric
    if target_px is not None:
        payload["target_px"] = target_px
    if normalize is not None:
        if isinstance(normalize, BoundsSpec):
            payload["normalize"] = normalize.model_dump()
        else:
            payload["normalize"] = dict(normalize)
    return payload


def compile_objective(raw: Mapping[str, Any]) -> Dict[str, Any]:
    """Compile authoring graph or validate runtime objective → plain dict."""
    return compile_objective_payload(raw)


def preflight_compiled_objective(
    objective: Union[ObjectiveSpec, Mapping[str, Any]],
    *,
    client: Any = None,
    state: Optional[Mapping[str, Any]] = None,
) -> ObjectiveSpec:
    """Validate compiled objective against live bench state via the server.

    Prefers ``POST /api/optimization/compile`` with ``preflight=true`` when
    ``client`` is a connected :class:`CloudLabsClient`. Offline local
    ``state`` preflight is not supported in the thin client (server-owned).
    """
    if not isinstance(objective, ObjectiveSpec):
        objective = ObjectiveSpec.model_validate(compile_objective_payload(objective))
    if client is None:
        raise EnsemblePreflightError(
            message=(
                "preflight_compiled_objective requires client= (HTTP preflight); "
                "offline state validation stays on the coordinator"
            ),
            errors=[],
        )
    body = {
        "objective": objective.model_dump(by_alias=True),
        "preflight": True,
    }
    if state is not None:
        body["state"] = dict(state)
    resp = client._post_json("/api/optimization/compile", body, include_lease=True)
    if not resp.get("ok", True):
        raise EnsemblePreflightError(
            message="server preflight failed",
            errors=list(resp.get("errors") or []),
        )
    compiled = resp.get("objective") or objective.model_dump(by_alias=True)
    return ObjectiveSpec.model_validate(compiled)


class ObjectiveGraphBuilder:
    """Fluent builder for :class:`ObjectiveGraphSpec` authoring JSON."""

    def __init__(self, *, minimize: bool = True) -> None:
        self._minimize = minimize
        self._terms: List[ObjectiveGraphTermSpec] = []

    def term(
        self,
        *,
        term_id: str,
        tag_id: Optional[str] = None,
        field: Optional[str] = None,
        weight: float = 1.0,
        metric: Optional[str] = None,
        target_px: Optional[Dict[str, float]] = None,
        normalize: Optional[Union[BoundsSpec, Dict[str, float]]] = None,
        source: Optional[Mapping[str, Any]] = None,
    ) -> "ObjectiveGraphBuilder":
        """Add a field-based term or an explicit runtime ``source`` pass-through."""
        if source is not None:
            src = (
                source
                if isinstance(source, ObjectiveSourceSpec)
                else ObjectiveSourceSpec.model_validate(source)
            )
            self._terms.append(
                ObjectiveGraphTermSpec(
                    id=term_id,
                    weight=weight,
                    tag_id=str(src.tag_id),
                    field=None,
                    metric=metric or "squared_error",
                    source=src,
                )
            )
            return self
        if not tag_id or not field:
            raise ValueError("term requires tag_id+field or source=")
        norm: Optional[BoundsSpec] = None
        if normalize is not None:
            norm = (
                normalize
                if isinstance(normalize, BoundsSpec)
                else BoundsSpec.model_validate(normalize)
            )
        self._terms.append(
            ObjectiveGraphTermSpec(
                id=term_id,
                tag_id=tag_id,
                field=field,
                weight=weight,
                metric=metric,
                target_px=target_px,
                normalize=norm,
            )
        )
        return self

    def build(self) -> Dict[str, Any]:
        """Alias for :meth:`compile` (runtime objective dict)."""
        return self.compile()

    def build_graph(self) -> Dict[str, Any]:
        """Authoring graph dict (not yet lowered)."""
        return ObjectiveGraphSpec(
            minimize=self._minimize,
            terms=self._terms,
        ).model_dump()

    def compile(self) -> Dict[str, Any]:
        """Lower to runtime objective dict."""
        return compile_objective_graph(
            ObjectiveGraphSpec(minimize=self._minimize, terms=self._terms)
        ).model_dump(by_alias=True)

    def compile_and_preflight(self, client: Any) -> Dict[str, Any]:
        """Compile and validate against live state via the coordinator."""
        compiled = self.compile()
        spec = preflight_compiled_objective(compiled, client=client)
        return spec.model_dump(by_alias=True)
