"""MeasurableTensor handle for imperative SDK (Phase D)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, Optional, Union

from .tensor import LazyRef, MeasurableTensor

from .client import _normalize_measurable_path
from .exceptions import CloudLabsCommandError, CloudLabsPathError

if TYPE_CHECKING:
    from .client import CloudLabsClient


@dataclass
class MeasurableHandle:
    """Lazy reference to a measurable field; call :meth:`resolve` to materialize."""

    client: "CloudLabsClient"
    tag_id: str
    field: str

    def resolve(self, *, record: bool = False) -> MeasurableTensor:
        """Fetch tensor metadata and materialize lazy image payloads client-side."""
        field = _normalize_measurable_path(self.field)
        params: Dict[str, Any] = {}
        if record:
            params["record"] = "true"
        params["resolve"] = "true"
        path = (
            f"/api/components/{self.tag_id}/measurables/{field}/tensor"
        )
        payload = self.client._get_json(path, params=params)
        tensor_raw = payload.get("tensor")
        if not isinstance(tensor_raw, dict):
            raise CloudLabsCommandError(
                "tensor endpoint did not return tensor",
                action="GET_MEASURABLE_TENSOR",
                target_id=self.tag_id,
            )
        tensor = MeasurableTensor.from_api_dict(tensor_raw)
        return _resolve_lazy_on_client(self.client, tensor)

    def resolve_torch(self, *, record: bool = False, dtype: Any = None) -> Any:
        """Resolve and return the values directly as a ``torch.Tensor``.

        Convenience for scripting::

            t = lab.measurable("tag_22", "camera_image").resolve_torch(record=True)
            # t is a torch.Tensor, HxWx3 uint8 BGR (values 0..255)

        Equivalent to ``resolve(record=record).to_torch(dtype=dtype)``.
        """
        return self.resolve(record=record).to_torch(dtype=dtype)

    def peek(self) -> MeasurableTensor:
        """Return tensor descriptor from current lab state (no capture, no image decode)."""
        field = _normalize_measurable_path(self.field)
        path = f"/api/components/{self.tag_id}/measurables/{field}/tensor"
        payload = self.client._get_json(path)
        tensor_raw = payload.get("tensor")
        if not isinstance(tensor_raw, dict):
            raise CloudLabsCommandError(
                "tensor endpoint did not return tensor",
                action="GET_MEASURABLE_TENSOR",
                target_id=self.tag_id,
            )
        return MeasurableTensor.from_api_dict(tensor_raw)


def _resolve_lazy_on_client(
    client: "CloudLabsClient",
    tensor: MeasurableTensor,
) -> MeasurableTensor:
    if not isinstance(tensor.data, LazyRef):
        return tensor
    if tensor.field != "camera_image":
        return tensor

    href = tensor.data.href
    if not href:
        raise CloudLabsPathError(
            tensor.field,
            tensor.tag_id,
            "camera_image tensor missing fetch href",
        )

    url = href if href.startswith("http") else f"{client.base_url}{href}"
    try:
        resp = client._session.get(url, timeout=client.timeout_s)
    except Exception as exc:  # noqa: BLE001
        raise CloudLabsCommandError(
            f"Failed to fetch camera image: {exc}",
            action="RESOLVE_MEASURABLE",
            target_id=tensor.tag_id,
        ) from exc
    if resp.status_code != 200:
        raise CloudLabsCommandError(
            f"Camera image fetch HTTP {resp.status_code}",
            status_code=resp.status_code,
            action="RESOLVE_MEASURABLE",
            target_id=tensor.tag_id,
        )

    try:
        import numpy as np
        import io

        raw = resp.content
        arr = None
        try:
            import cv2

            buf = np.frombuffer(raw, dtype=np.uint8)
            decoded = cv2.imdecode(buf, cv2.IMREAD_COLOR)
            if decoded is not None:
                arr = decoded
        except ImportError:
            pass
        if arr is None:
            from PIL import Image

            # Pillow loads RGB; convert to BGR to match kernel layout.
            rgb = np.asarray(Image.open(io.BytesIO(raw)).convert("RGB"), dtype=np.uint8)
            arr = rgb[:, :, ::-1].copy()
    except ImportError as exc:
        raise CloudLabsCommandError(
            "resolve() requires numpy and (opencv or Pillow)",
            action="RESOLVE_MEASURABLE",
            target_id=tensor.tag_id,
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise CloudLabsCommandError(
            f"Failed to decode camera image: {exc}",
            action="RESOLVE_MEASURABLE",
            target_id=tensor.tag_id,
        ) from exc

    from dataclasses import replace

    return replace(tensor, data=arr, shape=tuple(int(x) for x in arr.shape))
