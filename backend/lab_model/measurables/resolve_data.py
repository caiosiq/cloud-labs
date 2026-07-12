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
    import numpy as np

    path = _path_from_lazy(ref)
    if not path or not os.path.isfile(path):
        return None
    try:
        from PIL import Image
    except ImportError:
        return None
    with Image.open(path) as img:
        rgb = img.convert("RGB")
        arr = np.asarray(rgb, dtype=np.uint8)
    return arr


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
