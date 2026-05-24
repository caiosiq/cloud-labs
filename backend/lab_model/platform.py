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


def audit_primitive_handlers(
    communicator_cls: type,
    *,
    label: str = "LabCommunicator",
) -> List[Dict[str, Any]]:
    """Return a per-primitive report: registry handler vs class method."""
    from lab_model.primitives.ids import PrimitiveKind

    rows: List[Dict[str, Any]] = []
    for pid, meta in PRIMITIVE_REGISTRY.items():
        kind = meta.get("kind")
        handler = meta.get("handler")
        row: Dict[str, Any] = {
            "primitive": pid.value,
            "kind": kind.value if kind is not None else None,
            "handler": handler,
            "status": "ok",
            "detail": "",
        }
        if kind == PrimitiveKind.MACRO:
            row["status"] = "macro"
            row["detail"] = "expanded in dispatch, not direct handler"
        elif handler is None:
            row["status"] = "stub"
            row["detail"] = "no LabCommunicator handler (intentional stub)"
        else:
            fn = getattr(communicator_cls, str(handler), None)
            if fn is None or not callable(fn):
                row["status"] = "missing"
                row["detail"] = f"{label} has no callable {handler!r}"
            else:
                row["detail"] = f"{label}.{handler}"
        rows.append(row)
    return rows


def audit_real_hardware_hooks(real_cls: type) -> List[Dict[str, Any]]:
    """Report ``_primitive_*`` hooks on ``RealLabCommunicator``."""
    from lab_communicator.base import LabCommunicator

    expected = (
        "_primitive_move_component",
        "_primitive_move_motor",
        "_primitive_prepare_optimization_run",
        "_primitive_optimize_component",
        "_primitive_finalize_optimization_run",
        "_primitive_add_component_to_state",
        "_primitive_record_measurables",
        "_primitive_pick_component",
        "_primitive_hover_component",
        "_primitive_place_from_hover",
        "_primitive_scan_rotate_in_place",
        "_primitive_prepare_teleop",
        "_primitive_start_live_feed",
        "_primitive_end_live_feed",
    )
    rows: List[Dict[str, Any]] = []
    for name in expected:
        on_real = getattr(real_cls, name, None)
        on_base = getattr(LabCommunicator, name, None)
        if on_real is None:
            status = "missing"
        elif on_real is on_base:
            status = "base_default"
        else:
            status = "real_override"
        rows.append({"hook": name, "status": status})
    return rows


def assert_primitive_handlers_wired(communicator_cls: type, *, label: str = "LabCommunicator") -> None:
    """Raise :class:`PlatformIntegrityError` when any atomic handler is missing."""
    missing = [
        r
        for r in audit_primitive_handlers(communicator_cls, label=label)
        if r["status"] == "missing"
    ]
    if missing:
        lines = ", ".join(f"{r['primitive']}→{r['handler']}" for r in missing)
        raise PlatformIntegrityError(f"Missing primitive handlers on {label}: {lines}")


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
