"""Unit-hypercube normalization for ensemble search vectors."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Mapping, Sequence

from .spec import VariableRef


@dataclass(frozen=True)
class DimensionBounds:
    variable_id: str
    lo: float
    hi: float

    def normalize(self, physical: float) -> float:
        span = self.hi - self.lo
        if abs(span) < 1e-15:
            return 0.5
        u = (physical - self.lo) / span
        return max(0.0, min(1.0, u))

    def denormalize(self, u: float) -> float:
        span = self.hi - self.lo
        if abs(span) < 1e-15:
            return self.lo
        clamped = max(0.0, min(1.0, u))
        return self.lo + clamped * span


class NormalizedSearchSpace:
    """Map physical variable values ↔ normalized u ∈ [0,1]^N."""

    def __init__(
        self,
        variables: Sequence[VariableRef],
        x0: Mapping[str, float],
    ) -> None:
        self._variables = list(variables)
        self._dims: List[DimensionBounds] = []
        for var in self._variables:
            start = float(x0[var.id])
            lo = float(var.bounds.min)
            hi = float(var.bounds.max)
            if var.delta:
                lo = start + lo
                hi = start + hi
            if lo > hi:
                lo, hi = hi, lo
            self._dims.append(DimensionBounds(variable_id=var.id, lo=lo, hi=hi))

    @property
    def variable_ids(self) -> List[str]:
        return [v.id for v in self._variables]

    def physical_bounds(self, variable_id: str) -> tuple[float, float]:
        dim = self._dim(variable_id)
        return dim.lo, dim.hi

    def normalize_dict(self, physical: Mapping[str, float]) -> List[float]:
        return [self._dim(vid).normalize(float(physical[vid])) for vid in self.variable_ids]

    def denormalize_list(self, u: Sequence[float]) -> Dict[str, float]:
        if len(u) != len(self._variables):
            raise ValueError(
                f"expected {len(self._variables)} normalized values, got {len(u)}"
            )
        return {
            vid: self._dim(vid).denormalize(float(u[i]))
            for i, vid in enumerate(self.variable_ids)
        }

    def normalize_scalar(self, variable_id: str, physical: float) -> float:
        return self._dim(variable_id).normalize(physical)

    def denormalize_scalar(self, variable_id: str, u: float) -> float:
        return self._dim(variable_id).denormalize(u)

    def _dim(self, variable_id: str) -> DimensionBounds:
        for dim in self._dims:
            if dim.variable_id == variable_id:
                return dim
        raise KeyError(variable_id)


__all__ = ["DimensionBounds", "NormalizedSearchSpace"]
