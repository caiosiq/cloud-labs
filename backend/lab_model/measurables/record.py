"""Orchestrate RECORD_MEASURABLES from catalog-declared measurable fields."""
from __future__ import annotations

from typing import Any, Dict, Optional

from lab_model.catalog.schema import measurables_decl

from .registry import MEASURABLE_REGISTRY


async def observe_for_tag(
    bridge: Any,
    tag_id: str,
    catalog_meta: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """
    Build ``{"measurables": {field: value, ...}}`` for fields declared on the tag.

    Only runs observers registered in :data:`MEASURABLE_REGISTRY` that appear
    in the component's declared measurable fields (``statecontrol.measurables``).
    """
    decl = measurables_decl(catalog_meta)
    if not decl:
        return None

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
        if val is not None:
            observed[field_id] = val

    if not observed:
        return None
    return {"measurables": observed}
