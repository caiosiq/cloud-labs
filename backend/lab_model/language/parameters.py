"""UC **parameters** — static identity / manufacturer / capability constants.

Parameters are catalog-authored facts about a component (type geometry hints,
``max_fps``, ``manufacturer``, hardware binding, …). They are **not** tunables
(set via primitives) and **not** measurables (captured observations).

Source of truth: the component-library row's ``parameters`` object (legacy
``properties`` is accepted and normalized). Runtime lab-state entries may
mirror the same bag under ``components[tag].parameters`` for Twin display.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

# Structural catalog fields folded into GET_PARAMETERS for a complete identity view.
_STRUCTURAL_KEYS = (
    "id",
    "type",
    "name",
    "tag_id",
    "size",
    "height_mm",
    "motor_ids",
    "motor_controller",
)


def catalog_parameters_bag(row: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Return the dedicated static bag from a catalog row.

    Prefers ``parameters``; falls back to legacy ``properties``.
    """
    if not isinstance(row, Mapping):
        return {}
    bag = row.get("parameters")
    if not isinstance(bag, dict):
        bag = row.get("properties")
    if not isinstance(bag, dict):
        return {}
    return dict(bag)


def parameters_from_catalog_row(row: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Full parameters view for ``GET_PARAMETERS`` / Wiki / SDK describe.

    Structural fields (``type``, ``size``, …) plus the ``parameters`` bag.
    Bag keys win on collision so manufacturers can override display fields.
    """
    if not isinstance(row, Mapping):
        return {}
    out: Dict[str, Any] = {}
    for key in _STRUCTURAL_KEYS:
        if row.get(key) is not None:
            out[key] = row[key]
    out.update(catalog_parameters_bag(row))
    return out


def normalize_catalog_row_parameters(row: Dict[str, Any]) -> None:
    """In-place: ensure ``parameters`` exists; migrate legacy ``properties``."""
    if not isinstance(row, dict):
        return
    bag = row.get("parameters")
    legacy = row.get("properties")
    if isinstance(bag, dict) and bag:
        if isinstance(legacy, dict) and legacy:
            # Keep legacy key for older readers; prefer parameters as canonical.
            pass
        return
    if isinstance(legacy, dict):
        row["parameters"] = dict(legacy)
        return
    row.setdefault("parameters", {})
