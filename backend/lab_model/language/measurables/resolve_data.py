"""Resolve lazy MeasurableTensor payloads into materialized numpy arrays."""
from __future__ import annotations

import os
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Optional

from .tensor import LazyRef, MeasurableTensor

if TYPE_CHECKING:
    import numpy as np


def resolve_tensor_data(tensor: MeasurableTensor) -> MeasurableTensor:
    """Materialize lazy refs (camera images) into in-memory numpy arrays."""
    if not isinstance(tensor.data, LazyRef):
        return tensor
    if tensor.field != "camera_image":
        return tensor
    arr = _load_image_array(tensor.data)
    if arr is None:
        return tensor
    return replace(tensor, data=arr, shape=tuple(arr.shape))


def _load_image_array(ref: LazyRef) -> Optional["np.ndarray"]:
    """Load camera PNG/JPEG as BGR uint8 HxWx3 (manifest ``bgr_hwc_uint8``)."""
    path = _path_from_lazy(ref)
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError:
        return None
    from lab_model.execution.optimization.metrics.image_features import decode_png_bytes_to_bgr

    return decode_png_bytes_to_bgr(data)


def _path_from_lazy(ref: LazyRef) -> Optional[str]:
    if ref.kind == "file":
        return ref.href if ref.href else None
    if ref.kind == "url" and ref.href.startswith("/api/components/"):
        # Server-side resolve: map camera-image URL back to state path via caller.
        return None
    if os.path.isfile(ref.href):
        return ref.href
    return None


def resolve_tensor_with_state_path(
    tensor: MeasurableTensor,
    *,
    filesystem_path: Optional[str],
) -> MeasurableTensor:
    """Resolve a camera tensor when the on-disk path is known (server-side)."""
    if not isinstance(tensor.data, LazyRef) or not filesystem_path:
        return tensor
    import numpy as np

    arr = _load_image_array(LazyRef(kind="file", href=filesystem_path, format=tensor.data.format))
    if arr is None:
        return tensor
    return replace(tensor, data=arr, shape=tuple(int(x) for x in arr.shape))
