"""MeasurableTensor — typed numerical carrier at the analysis boundary (Phase D)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Literal, Mapping, Optional, Sequence, Tuple, Union

Domain = Literal["spatial", "time", "scalar", "spectrum", "other"]


@dataclass(frozen=True)
class LazyRef:
    """Edge-resident payload referenced by URL or filesystem path."""

    kind: Literal["url", "file"]
    href: str
    format: str = "png"

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "href": self.href, "format": self.format}


@dataclass(frozen=True)
class MeasurableTensor:
    """Canonical numerical carrier for a measurable at analysis boundary."""

    tag_id: str
    field: str
    dtype: str
    shape: Tuple[int, ...]
    axes: Dict[str, str]
    units: Dict[str, str]
    domain: Domain
    data: Union[float, int, Sequence[float], LazyRef, Any]
    provenance: Dict[str, Any] = field(default_factory=dict)

    def to_api_dict(self, *, include_data: bool = True) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "tag_id": self.tag_id,
            "field": self.field,
            "dtype": self.dtype,
            "shape": list(self.shape),
            "axes": dict(self.axes),
            "units": dict(self.units),
            "domain": self.domain,
            "provenance": dict(self.provenance),
        }
        if include_data:
            if isinstance(self.data, LazyRef):
                out["data"] = self.data.to_dict()
            elif hasattr(self.data, "tolist"):
                out["data"] = self.data.tolist()
            else:
                out["data"] = self.data
        return out

    @classmethod
    def from_api_dict(cls, payload: Mapping[str, Any]) -> "MeasurableTensor":
        raw_data = payload.get("data")
        data: Any = raw_data
        if isinstance(raw_data, dict) and raw_data.get("kind") in ("url", "file"):
            data = LazyRef(
                kind=str(raw_data["kind"]),  # type: ignore[arg-type]
                href=str(raw_data.get("href") or ""),
                format=str(raw_data.get("format") or "png"),
            )
        shape_raw = payload.get("shape") or []
        shape = tuple(int(x) for x in shape_raw) if isinstance(shape_raw, (list, tuple)) else ()
        return cls(
            tag_id=str(payload.get("tag_id") or ""),
            field=str(payload.get("field") or ""),
            dtype=str(payload.get("dtype") or "float64"),
            shape=shape,
            axes=dict(payload.get("axes") or {}),
            units=dict(payload.get("units") or {}),
            domain=str(payload.get("domain") or "other"),  # type: ignore[arg-type]
            data=data,
            provenance=dict(payload.get("provenance") or {}),
        )
