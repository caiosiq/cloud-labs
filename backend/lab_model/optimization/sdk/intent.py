"""Composable intent builders for clean authoring scripts (no raw ensemble IR)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

Bounds = Union[Tuple[float, float], Mapping[str, float]]


def _bounds_dict(bounds: Bounds) -> Dict[str, float]:
    if isinstance(bounds, Mapping):
        return {"min": float(bounds["min"]), "max": float(bounds["max"])}
    lo, hi = bounds
    return {"min": float(lo), "max": float(hi)}


@dataclass(frozen=True)
class VariableSpec:
    """One continuous ensemble variable."""

    tag_id: str
    path: str
    bounds: Bounds
    variable_id: Optional[str] = None
    unit: str = "deg"
    delta: bool = False
    physical_type: str = "continuous"

    def to_dict(self) -> Dict[str, Any]:
        vid = self.variable_id or f"v_{self.tag_id}_{self.path.rsplit('.', 1)[-1]}"
        return {
            "id": vid,
            "tag_id": self.tag_id.strip(),
            "path": self.path.strip(),
            "physical_type": self.physical_type,
            "unit": self.unit,
            "bounds": _bounds_dict(self.bounds),
            "delta": bool(self.delta),
        }


@dataclass(frozen=True)
class KernelMatchSpec:
    """Match a TorchScript kernel scalar to a frozen target (edge loss)."""

    tag_id: str
    field: str
    kernel_id: str
    target: float
    term_id: str = "match_m0"
    weight: float = 1.0
    metric: str = "squared_error"

    def to_term(self) -> Dict[str, Any]:
        field = self.field.strip()
        if not field.startswith("measurables."):
            field = f"measurables.{field}"
        return {
            "id": self.term_id,
            "weight": float(self.weight),
            "source": {
                "tag_id": self.tag_id.strip(),
                "kind": "torchscript_scalar",
                "kernel_id": self.kernel_id.strip(),
                "from": field,
                "target_scalar": float(self.target),
            },
            "metric": self.metric,
        }


def build_cobyla_ensemble(
    *,
    variables: Sequence[Union[VariableSpec, Mapping[str, Any]]],
    match_kernel: Union[KernelMatchSpec, Mapping[str, Any]],
    max_evals: int = 25,
    session_label: Optional[str] = None,
    settle_ms: int = 50,
) -> Dict[str, Any]:
    """Build ensemble OPTIMIZE parameters for block COBYLA + kernel match."""
    if isinstance(match_kernel, KernelMatchSpec):
        term = match_kernel.to_term()
        kernel_id = match_kernel.kernel_id
        target = match_kernel.target
    else:
        term = dict(match_kernel)
        src = term.get("source") or {}
        kernel_id = str(src.get("kernel_id") or "")
        target = float(src.get("target_scalar") or 0.0)

    label = session_label or (
        f"torchscript (M-M0)^2 -> COBYLA  M0={float(target):.4f}"
    )
    return build_ensemble_parameters(
        variables=variables,
        objective={
            "type": "weighted_sum",
            "minimize": True,
            "terms": [term],
        },
        max_evals=max_evals,
        session_label=label,
        settle_ms=settle_ms,
        kernels=[kernel_id] if kernel_id else None,
    )


def build_ensemble_parameters(
    *,
    variables: Sequence[Union[VariableSpec, Mapping[str, Any]]],
    objective: Mapping[str, Any],
    max_evals: int = 25,
    session_label: Optional[str] = None,
    settle_ms: int = 50,
    kernels: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Build ensemble OPTIMIZE parameters from variables + runtime objective."""
    var_dicts: List[Dict[str, Any]] = []
    for v in variables:
        if isinstance(v, VariableSpec):
            var_dicts.append(v.to_dict())
        else:
            var_dicts.append(dict(v))
    if not var_dicts:
        raise ValueError("variables must be non-empty")

    obj = dict(objective)
    obj.setdefault("type", "weighted_sum")
    obj.setdefault("minimize", True)
    if not obj.get("terms"):
        raise ValueError("objective.terms must be non-empty")

    var_ids = [str(v["id"]) for v in var_dicts]
    kernel_ids = collect_kernel_ids(obj, extra=kernels)
    label = session_label or "ensemble COBYLA"
    # ASCII-only labels (Windows consoles choke on unicode arrows).
    label = str(label).replace("\u2192", "->").replace("→", "->")
    return {
        "mode": "ensemble",
        "session_label": label,
        "kernels": kernel_ids,
        "variables": var_dicts,
        "objective": obj,
        "solver": {
            "type": "block_cobyla",
            "max_total_evals": int(max_evals),
            "keep_best": True,
            "settle_ms": int(settle_ms),
            "blocks": [
                {
                    "id": "block_0",
                    "variable_ids": var_ids,
                    "max_evals": int(max_evals),
                    "trust_region_u": 0.25,
                    "rhobeg_u": 0.08,
                    "rhoend_u": 0.002,
                    "passes": 1,
                }
            ],
        },
    }


def collect_kernel_ids(
    objective: Mapping[str, Any],
    *,
    extra: Optional[Sequence[str]] = None,
) -> List[str]:
    """Collect unique kernel ids from objective terms + optional extras."""
    out: List[str] = []
    for term in objective.get("terms") or []:
        if not isinstance(term, Mapping):
            continue
        src = term.get("source") or {}
        if not isinstance(src, Mapping):
            continue
        kid = str(src.get("kernel_id") or "").strip()
        if kid and kid not in out:
            out.append(kid)
    for raw in extra or []:
        kid = str(raw).strip()
        if kid and kid not in out:
            out.append(kid)
    return out
