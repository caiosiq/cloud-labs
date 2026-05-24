"""Platform integrity checks — registries, primitives, and catalog alignment."""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence

from lab_model.catalog.schema import (
    KNOWN_WIDGETS_MEASURABLE,
    KNOWN_WIDGETS_TUNABLE,
    SOFT_FALLBACK_WIDGET,
)
from lab_model.measurables.registry import MEASURABLE_REGISTRY
from lab_model.primitives.ids import PrimitiveId
from lab_model.primitives.registry import PRIMITIVE_REGISTRY
from lab_model.tunables.registry import TUNABLE_REGISTRY


class PlatformIntegrityError(RuntimeError):
    """Raised when registry / primitive / catalog wiring is inconsistent."""


def validate_platform_integrity(
    catalog_rows: Optional[Sequence[Mapping[str, Any]]] = None,
    *,
    strict_catalog: bool = False,
) -> List[str]:
    """
    Verify tunable/measurable plugins match primitive registry.

    Returns non-fatal warnings. Raises :class:`PlatformIntegrityError` on hard failures.
    Import ``lab_model.tunables`` and ``lab_model.measurables`` before calling so
    decorators have run.
    """
    warnings: List[str] = []

    if PrimitiveId.RECORD_MEASURABLES not in PRIMITIVE_REGISTRY:
        raise PlatformIntegrityError("RECORD_MEASURABLES missing from PRIMITIVE_REGISTRY")

    for field_id, spec in TUNABLE_REGISTRY.items():
        if spec.write_primitive not in PRIMITIVE_REGISTRY:
            raise PlatformIntegrityError(
                f"Tunable {field_id!r} write_primitive {spec.write_primitive!r} not registered"
            )
        entry = PRIMITIVE_REGISTRY[spec.write_primitive]
        if spec.widget not in KNOWN_WIDGETS_TUNABLE and spec.widget != SOFT_FALLBACK_WIDGET:
            warnings.append(f"Tunable {field_id!r}: unknown widget {spec.widget!r}")
        if entry.get("handler") is None:
            warnings.append(
                f"Tunable {field_id!r}: primitive {spec.write_primitive!r} has no handler"
            )

    for field_id, spec in MEASURABLE_REGISTRY.items():
        if spec.widget not in KNOWN_WIDGETS_MEASURABLE and spec.widget != SOFT_FALLBACK_WIDGET:
            warnings.append(f"Measurable {field_id!r}: unknown widget {spec.widget!r}")

    if catalog_rows:
        for row in catalog_rows:
            tag_id = str(row.get("tag_id") or row.get("id") or "?")
            caps = row.get("capabilities") or {}
            if not isinstance(caps, dict):
                continue
            tun_decl = caps.get("tunables") or {}
            if isinstance(tun_decl, dict):
                for fid in tun_decl:
                    if fid not in TUNABLE_REGISTRY:
                        msg = f"Catalog {tag_id}: tunable {fid!r} has no lab_model plugin"
                        if strict_catalog:
                            raise PlatformIntegrityError(msg)
                        warnings.append(msg)
            meas_decl = caps.get("measurables") or {}
            if isinstance(meas_decl, dict):
                for fid in meas_decl:
                    if fid not in MEASURABLE_REGISTRY:
                        msg = f"Catalog {tag_id}: measurable {fid!r} has no lab_model plugin"
                        if strict_catalog:
                            raise PlatformIntegrityError(msg)
                        warnings.append(msg)

    for w in warnings:
        print(f"[PLATFORM] {w}")
    return warnings


def validate_communicator_backend(communicator_id: str) -> None:
    """Ensure ``lab_manifest.communicator`` maps to a registered backend."""
    from lab_communicator.shared.communicator_factory import known_communicator_ids

    key = (communicator_id or "").strip().lower()
    known = known_communicator_ids()
    if key not in known:
        raise PlatformIntegrityError(
            f"Unknown communicator {communicator_id!r} (supported: {', '.join(sorted(known))})"
        )


def export_platform_registries() -> Dict[str, Any]:
    """JSON-serializable snapshot of tunable/measurable/primitive registries."""
    from lab_model import measurables as _measurables  # noqa: F401
    from lab_model import tunables as _tunables  # noqa: F401

    tunables_out: Dict[str, Dict[str, str]] = {}
    for field_id, spec in TUNABLE_REGISTRY.items():
        tunables_out[field_id] = {
            "widget": spec.widget,
            "write_primitive": spec.write_primitive.value,
        }

    measurables_out: Dict[str, Dict[str, str]] = {}
    for field_id, spec in MEASURABLE_REGISTRY.items():
        measurables_out[field_id] = {"widget": spec.widget}

    primitives_out: Dict[str, Dict[str, Any]] = {}
    for pid, meta in PRIMITIVE_REGISTRY.items():
        row: Dict[str, Any] = {
            "kind": meta.get("kind").value if meta.get("kind") is not None else None,
            "read_only": bool(meta.get("read_only")),
            "handler": meta.get("handler"),
        }
        if meta.get("http"):
            row["http"] = meta["http"]
        if meta.get("macro_expands_to"):
            row["macro_expands_to"] = [p.value for p in meta["macro_expands_to"]]
        primitives_out[pid.value] = row

    return {
        "tunables": tunables_out,
        "measurables": measurables_out,
        "primitives": primitives_out,
        "record_measurables_primitive": PrimitiveId.RECORD_MEASURABLES.value,
    }
