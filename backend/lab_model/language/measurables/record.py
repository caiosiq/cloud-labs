"""Orchestrate RECORD_MEASURABLES from catalog-declared measurable fields."""
from __future__ import annotations

from typing import Any, Dict, Optional

from lab_model.coordinator.catalog.schema import measurables_decl

from .materialize import materialize_measurable
from .registry import MEASURABLE_REGISTRY
from .tensor import MeasurableTensor


async def observe_for_tag(
    bridge: Any,
    tag_id: str,
    catalog_meta: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """
    Build ``{"measurables": {field: tensor_envelope, ...}}`` for declared fields.

    Only runs observers registered in :data:`MEASURABLE_REGISTRY` that appear
    in the component's declared measurable fields (``statecontrol.measurables``).
    Each value is stored as a :class:`MeasurableTensor` API dict (tensor-native).
    """
    decl = measurables_decl(catalog_meta)
    if not decl:
        return None

    backend_id = getattr(bridge, "backend_id", None) or getattr(bridge, "name", None)
    observed: Dict[str, Any] = {}
    for field_id in decl:
        spec = MEASURABLE_REGISTRY.get(field_id)
        if spec is None:
            continue
        try:
            val = await spec.observe(bridge, tag_id, catalog_meta)
        except Exception as exc:  # noqa: BLE001
            print(f"{getattr(bridge, 'log_prefix', 'LAB')} observe {field_id} failed: {exc}")
            continue
        if val is None:
            continue
        if isinstance(val, MeasurableTensor):
            observed[field_id] = val.to_api_dict()
        else:
            observed[field_id] = materialize_measurable(
                tag_id,
                field_id,
                val,
                backend_id=str(backend_id) if backend_id else None,
            ).to_api_dict()

    if not observed:
        return None
    return {"measurables": observed}
