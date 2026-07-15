"""Materialize runtime measurable values into MeasurableTensor descriptors."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional

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
    """Wrap a raw measurable field value as a MeasurableTensor (lazy for images)."""
    if field == "camera_image":
        return _materialize_camera_image(
            tag_id,
            value,
            backend_id=backend_id,
            fetch_url=fetch_url,
        )
    if field == "pose":
        return _materialize_pose(tag_id, value, backend_id=backend_id)
    if field == "motor_rotations":
        return _materialize_motor_rotations(tag_id, value, backend_id=backend_id)
    if field in ("output_power_readback_mw", "last_optimization_score"):
        return _materialize_scalar(tag_id, field, value, backend_id=backend_id, unit="mw" if "mw" in field else "1")
    return _materialize_generic(tag_id, field, value, backend_id=backend_id)


def _materialize_camera_image(
    tag_id: str,
    value: Any,
    *,
    backend_id: Optional[str],
    fetch_url: Optional[str],
) -> MeasurableTensor:
    path = ""
    fmt = "png"
    source = "unknown"
    if isinstance(value, dict):
        path = str(value.get("path") or "")
        fmt = str(value.get("format") or "png")
        source = str(value.get("source") or "camera")
    href = fetch_url or (
        f"/api/components/{tag_id}/camera-image" if path or fetch_url is not None else ""
    )
    lazy = LazyRef(kind="url", href=href, format=fmt)
    return MeasurableTensor(
        tag_id=tag_id,
        field="camera_image",
        dtype="uint8",
        shape=(),  # filled on resolve
        axes={"y": "pixel", "x": "pixel", "c": "bgr"},
        units={},
        domain="spatial",
        data=lazy,
        provenance=_provenance(
            tag_id=tag_id,
            field="camera_image",
            backend_id=backend_id,
            source=source,
        ),
    )


def _materialize_pose(
    tag_id: str,
    value: Any,
    *,
    backend_id: Optional[str],
) -> MeasurableTensor:
    pose = value if isinstance(value, dict) else {}
    vec = [
        float(pose.get("x", 0.0)),
        float(pose.get("y", 0.0)),
        float(pose.get("rotation", 0.0)),
    ]
    return MeasurableTensor(
        tag_id=tag_id,
        field="pose",
        dtype="float64",
        shape=(3,),
        axes={"i": "dof"},
        units={"x": "mm", "y": "mm", "rotation": "deg"},
        domain="spatial",
        data=vec,
        provenance=_provenance(tag_id=tag_id, field="pose", backend_id=backend_id),
    )


def _materialize_motor_rotations(
    tag_id: str,
    value: Any,
    *,
    backend_id: Optional[str],
) -> MeasurableTensor:
    raw = value if isinstance(value, dict) else {}
    keys = sorted(raw.keys(), key=lambda k: int(k) if str(k).isdigit() else str(k))
    vec = [float(raw[k]) for k in keys]
    return MeasurableTensor(
        tag_id=tag_id,
        field="motor_rotations",
        dtype="float64",
        shape=(len(vec),),
        axes={"m": "motor_id"},
        units={"m": "deg"},
        domain="scalar",
        data=vec,
        provenance=_provenance(tag_id=tag_id, field="motor_rotations", backend_id=backend_id),
    )


def _materialize_scalar(
    tag_id: str,
    field: str,
    value: Any,
    *,
    backend_id: Optional[str],
    unit: str,
) -> MeasurableTensor:
    numeric = float(value) if isinstance(value, (int, float)) else 0.0
    return MeasurableTensor(
        tag_id=tag_id,
        field=field,
        dtype="float64",
        shape=(),
        axes={},
        units={"value": unit},
        domain="scalar",
        data=numeric,
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
        return _materialize_scalar(tag_id, field, value, backend_id=backend_id, unit="1")
    if isinstance(value, dict):
        keys = sorted(value.keys())
        vec = [float(value[k]) for k in keys if isinstance(value[k], (int, float))]
        if vec:
            return MeasurableTensor(
                tag_id=tag_id,
                field=field,
                dtype="float64",
                shape=(len(vec),),
                axes={"i": "index"},
                units={},
                domain="other",
                data=vec,
                provenance=_provenance(tag_id=tag_id, field=field, backend_id=backend_id),
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


def read_measurable_from_state(
    state: Mapping[str, Any],
    tag_id: str,
    field: str,
) -> Optional[Any]:
    """Return one measurable field from lab state, or None if missing."""
    entry = (state.get("components") or {}).get(tag_id)
    if not isinstance(entry, dict):
        return None
    from lab_model.domain.component import get_measurables  # noqa: PLC0415

    meas = get_measurables(entry)
    if field not in meas:
        return None
    return meas[field]
