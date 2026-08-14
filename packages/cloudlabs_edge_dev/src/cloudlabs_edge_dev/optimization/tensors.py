"""Edge-resident tensor carrier for in-loop optimization (no LazyRef)."""
from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple, Union

ArrayLike = Any  # numpy ndarray when available; kept untyped to avoid hard dep


@dataclass
class EdgeTensor:
    """Canonical numerical carrier at the edge analysis boundary.

    Mirrors ``measurable_tensor.schema.json`` metadata while keeping the array
    resident in process memory (OPTIMIZE inner loop never JPEG-encodes).
    """

    dtype: str
    shape: Tuple[Union[int, str], ...]
    axes: Dict[str, str]
    units: Dict[str, str]
    domain: str
    data: ArrayLike
    tag_id: Optional[str] = None
    field: Optional[str] = None
    provenance: Dict[str, Any] = dc_field(default_factory=dict)

    def to_api_dict(self, *, inline: bool = False) -> Dict[str, Any]:
        """Serialize metadata (+ optional inline scalar/vector). Images stay resident."""
        out: Dict[str, Any] = {
            "dtype": self.dtype,
            "shape": list(self.shape),
            "axes": dict(self.axes),
            "units": dict(self.units),
            "domain": self.domain,
        }
        if self.tag_id:
            out["tag_id"] = self.tag_id
        if self.field:
            out["field"] = self.field
        if self.provenance:
            out["provenance"] = dict(self.provenance)
        if inline:
            out["data"] = _inline_data(self.data)
        else:
            out["data"] = {"kind": "resident", "href": "memory://edge"}
        return out


def camera_bgr_tensor(
    bgr: ArrayLike,
    *,
    tag_id: Optional[str] = None,
    field: str = "camera_image",
) -> EdgeTensor:
    """Wrap an HxWx3 uint8 BGR frame as an EdgeTensor."""
    import numpy as np

    arr = np.asarray(bgr)
    if arr.ndim != 3 or arr.shape[2] not in (1, 3, 4):
        raise ValueError(f"expected HxWxC BGR image, got shape {getattr(arr, 'shape', None)}")
    if arr.shape[2] == 4:
        arr = arr[:, :, :3]
    if arr.shape[2] == 1:
        arr = np.repeat(arr, 3, axis=2)
    if arr.dtype != np.uint8:
        arr = arr.astype(np.uint8)
    h, w, c = int(arr.shape[0]), int(arr.shape[1]), int(arr.shape[2])
    return EdgeTensor(
        dtype="uint8",
        shape=(h, w, c),
        axes={"y": "pixel", "x": "pixel", "c": "bgr"},
        units={"y": "px", "x": "px", "c": "channel"},
        domain="spatial",
        data=arr,
        tag_id=tag_id,
        field=field,
    )


def scalar_tensor(
    value: float,
    *,
    tag_id: Optional[str] = None,
    field: str = "scalar",
) -> EdgeTensor:
    return EdgeTensor(
        dtype="float64",
        shape=(),
        axes={},
        units={},
        domain="scalar",
        data=float(value),
        tag_id=tag_id,
        field=field,
    )


def _inline_data(data: Any) -> Any:
    if isinstance(data, (int, float)):
        return float(data)
    try:
        import numpy as np

        arr = np.asarray(data)
        if arr.ndim == 0:
            return float(arr)
        if arr.size <= 64:
            return arr.reshape(-1).astype(float).tolist()
    except Exception:
        pass
    return {"kind": "resident", "href": "memory://edge"}


__all__ = ["EdgeTensor", "camera_bgr_tensor", "scalar_tensor"]
