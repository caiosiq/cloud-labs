"""Materialize runtime measurable values into MeasurableTensor (registry-driven)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional

from .registry import TensorSchema, get_measurable, is_tensor_envelope
from .tensor import LazyRef, MeasurableTensor


def _provenance(
    *,
    tag_id: str,
    field: str,
    backend_id: Optional[str] = None,
    source: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "capture_id": f"{tag_id}:{field}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "backend_id": backend_id,
        "source": source or "lab_state",
    }


def materialize_measurable(
    tag_id: str,
    field: str,
    value: Any,
    *,
    backend_id: Optional[str] = None,
    fetch_url: Optional[str] = None,
) -> MeasurableTensor:
    """Wrap a stored or freshly observed value as a :class:`MeasurableTensor`.

    Prefers the registered :class:`TensorSchema`. Accepts existing tensor
    envelopes, :class:`MeasurableTensor` instances, or legacy raw values.
    """
    if isinstance(value, MeasurableTensor):
        return value
    if is_tensor_envelope(value):
        return MeasurableTensor.from_api_dict(value)

    spec = get_measurable(field)
    schema = spec.tensor if spec is not None else None
    if schema is not None and schema.layout in ("lazy_image", "spatial_bgr"):
        return _materialize_lazy_image(
            tag_id,
            field,
            value,
            schema=schema,
            backend_id=backend_id,
            fetch_url=fetch_url,
        )
    if schema is not None and schema.domain == "scalar":
        return _materialize_with_schema(
            tag_id,
            field,
            value,
            schema=schema,
            backend_id=backend_id,
        )
    if schema is not None:
        return _materialize_with_schema(
            tag_id,
            field,
            value,
            schema=schema,
            backend_id=backend_id,
        )
    # Unregistered field — best-effort (should be rare).
    return _materialize_generic(tag_id, field, value, backend_id=backend_id)


def _materialize_lazy_image(
    tag_id: str,
    field: str,
    value: Any,
    *,
    schema: TensorSchema,
    backend_id: Optional[str],
    fetch_url: Optional[str],
) -> MeasurableTensor:
    path = ""
    fmt = "png"
    source = "unknown"
    if isinstance(value, dict):
        # Legacy wire dict or nested data.
        path = str(value.get("path") or value.get("href") or "")
        fmt = str(value.get("format") or "png")
        source = str(value.get("source") or "camera")
        nested = value.get("data")
        if isinstance(nested, dict) and nested.get("kind") in ("url", "file"):
            path = str(nested.get("href") or path)
            fmt = str(nested.get("format") or fmt)
    href = fetch_url or (
        f"/api/components/{tag_id}/camera-image" if path or fetch_url is not None else ""
    )
    # Prefer filesystem LazyRef when we have a path (edge-local); URL for HTTP clients.
    if path and not fetch_url:
        lazy: Any = LazyRef(kind="file", href=path, format=fmt)
    else:
        lazy = LazyRef(kind="url", href=href or path, format=fmt)
    return MeasurableTensor(
        tag_id=tag_id,
        field=field,
        dtype=schema.dtype,
        shape=schema.shape,
        axes=dict(schema.axes),
        units=dict(schema.units),
        domain=schema.domain,
        data=lazy,
        provenance=_provenance(
            tag_id=tag_id,
            field=field,
            backend_id=backend_id,
            source=source,
        ),
    )


def _materialize_with_schema(
    tag_id: str,
    field: str,
    value: Any,
    *,
    schema: TensorSchema,
    backend_id: Optional[str],
) -> MeasurableTensor:
    data: Any = value
    shape = schema.shape
    if schema.domain == "scalar" or schema.layout == "scalar":
        data = float(value) if isinstance(value, (int, float)) else 0.0
        shape = ()
    elif isinstance(value, (list, tuple)):
        data = [float(x) for x in value if isinstance(x, (int, float))]
        shape = (len(data),) if data else schema.shape
    return MeasurableTensor(
        tag_id=tag_id,
        field=field,
        dtype=schema.dtype,
        shape=shape,
        axes=dict(schema.axes),
        units=dict(schema.units),
        domain=schema.domain,
        data=data,
        provenance=_provenance(tag_id=tag_id, field=field, backend_id=backend_id),
    )


def _materialize_generic(
    tag_id: str,
    field: str,
    value: Any,
    *,
    backend_id: Optional[str],
) -> MeasurableTensor:
    if isinstance(value, (int, float)):
        return MeasurableTensor(
            tag_id=tag_id,
            field=field,
            dtype="float64",
            shape=(),
            axes={},
            units={"value": "1"},
            domain="scalar",
            data=float(value),
            provenance=_provenance(tag_id=tag_id, field=field, backend_id=backend_id),
        )
    if isinstance(value, dict) and "path" in value:
        # Legacy camera-shaped blob without registration.
        return _materialize_lazy_image(
            tag_id,
            field,
            value,
            schema=TensorSchema(
                dtype="uint8",
                domain="spatial",
                axes={"y": "pixel", "x": "pixel", "c": "bgr"},
                layout="lazy_image",
            ),
            backend_id=backend_id,
            fetch_url=None,
        )
    return MeasurableTensor(
        tag_id=tag_id,
        field=field,
        dtype="object",
        shape=(),
        axes={},
        units={},
        domain="other",
        data=value,
        provenance=_provenance(tag_id=tag_id, field=field, backend_id=backend_id),
    )


def legacy_wire_view(value: Any) -> Any:
    """Project a stored measurable for Twin / legacy readers.

    Tensor envelopes for lazy images become ``{path, format, source, ...}``.
    Scalars unwrap to bare numbers. Unknown shapes pass through.
    """
    if not is_tensor_envelope(value):
        return value
    tensor = MeasurableTensor.from_api_dict(value)
    if isinstance(tensor.data, LazyRef):
        out: Dict[str, Any] = {
            "format": tensor.data.format,
            "source": (tensor.provenance or {}).get("source") or "camera",
        }
        if tensor.data.kind == "file":
            out["path"] = tensor.data.href
        else:
            out["href"] = tensor.data.href
            # Best-effort: some Twin code still looks for path.
            if tensor.data.href and not tensor.data.href.startswith("/api/"):
                out["path"] = tensor.data.href
        return out
    return tensor.data


def read_measurable_from_state(
    state: Mapping[str, Any],
    tag_id: str,
    field: str,
) -> Optional[Any]:
    """Return one measurable field from lab state, or None if missing."""
    entry = (state.get("components") or {}).get(tag_id)
    if not isinstance(entry, dict):
        return None
    from lab_model.language.domain.component import get_measurables  # noqa: PLC0415

    meas = get_measurables(entry)
    if field not in meas:
        return None
    return meas[field]
