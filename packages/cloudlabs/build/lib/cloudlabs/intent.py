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
    """Match a TorchScript kernel output to a frozen target (edge loss).

    - Scalar target (``float``) → ``squared_error`` on ``torchscript_scalar``
      (or one feature channel when ``feature_index`` is set).
    - Pixel target ``(x, y)`` → ``rms_distance`` on ``torchscript_features``
      (default ``feature_index=[0, 1]``, e.g. ``builtin.roi_centroid``).
    """

    tag_id: str
    field: str
    kernel_id: str
    target: Any
    term_id: str = "match_m0"
    weight: float = 1.0
    metric: Optional[str] = None
    feature_index: Optional[Any] = None

    def to_term(self) -> Dict[str, Any]:
        field = self.field.strip()
        if not field.startswith("measurables."):
            field = f"measurables.{field}"
        kid = self.kernel_id.strip()
        tag = self.tag_id.strip()

        # Centroid / 2D target → rms_distance on a feature pair.
        if isinstance(self.target, (tuple, list)) and len(self.target) == 2:
            idx = self.feature_index if self.feature_index is not None else [0, 1]
            return {
                "id": self.term_id,
                "weight": float(self.weight),
                "source": {
                    "tag_id": tag,
                    "kind": "torchscript_features",
                    "kernel_id": kid,
                    "from": field,
                    "feature_index": idx,
                    "target_px": {
                        "x": float(self.target[0]),
                        "y": float(self.target[1]),
                    },
                },
                "metric": self.metric or "rms_distance",
            }

        # Optional single-feature match (e.g. sigma_x = feature_index 3).
        if self.feature_index is not None:
            return {
                "id": self.term_id,
                "weight": float(self.weight),
                "source": {
                    "tag_id": tag,
                    "kind": "torchscript_features",
                    "kernel_id": kid,
                    "from": field,
                    "feature_index": self.feature_index,
                    "target_scalar": float(self.target),
                },
                "metric": self.metric or "squared_error",
            }

        return {
            "id": self.term_id,
            "weight": float(self.weight),
            "source": {
                "tag_id": tag,
                "kind": "torchscript_scalar",
                "kernel_id": kid,
                "from": field,
                "target_scalar": float(self.target),
            },
            "metric": self.metric or "squared_error",
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
        target = src.get("target_scalar")
        if target is None and isinstance(src.get("target_px"), dict):
            target = (
                float(src["target_px"].get("x", 0.0)),
                float(src["target_px"].get("y", 0.0)),
            )
        elif target is None:
            target = 0.0

    if isinstance(target, (tuple, list)) and len(target) == 2:
        label = session_label or (
            f"torchscript rms_distance -> COBYLA  "
            f"target_px=({float(target[0]):.1f},{float(target[1]):.1f})"
        )
    else:
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
