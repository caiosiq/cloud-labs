"""Catalog-shaped lookups (size, height, motor validation) usable by any backend.

The "catalog" is a per-tag dict of metadata loaded at startup from
``schemas/component_catalog.<backend>.json``: width/height (UI footprint),
``height_mm`` (physical Z extent), ``motor_ids``, ``type``, etc. The real
backend stores it as ``catalog_map: Dict[str, Dict]`` (O(1) lookups);
mock stores it as a list and provides its own scan helper. Both are
expressed here as a generic ``catalog_get: (tag_id) -> meta`` callable
so this module never has to know which shape it's dealing with.

Architectural rule (``communicator_refactor.md`` §5.1): pure stdlib
imports only -- no ``lab_automation``, no :mod:`lab_communicator.base`,
no real/mock backends.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Tuple


CatalogLookup = Callable[[str], Optional[Dict[str, Any]]]
"""``(tag_id) -> meta dict (or None)``.

Real passes ``catalog_map.get`` (O(1)). Mock passes its linear-scan
helper. The shared logic in this module doesn't care.
"""


def catalog_wh(catalog_get: CatalogLookup, tag_id: str) -> Tuple[float, float]:
    """Return the UI footprint ``(width, height)`` in pixels for a catalog tag.

    The catalog ``size`` field can be either a ``{"width", "height"}``
    dict or a single number meaning "square N x N". Falls back to
    ``(90.0, 90.0)`` when the field is missing or malformed -- the value
    is only used for UI overlay rendering, so a wrong-but-finite default
    is preferable to a hard crash.
    """
    meta = catalog_get(tag_id) or {}
    s = meta.get("size")
    if isinstance(s, dict):
        return float(s.get("width", 62)), float(s.get("height", 62))
    if isinstance(s, (int, float)):
        v = float(s)
        return v, v
    return 90.0, 90.0


def component_height_mm(
    catalog_get: CatalogLookup,
    tag_id: str,
    *,
    default_mm: float,
    log_prefix: str = "[CATALOG]",
) -> float:
    """Physical height of a component (base to top, mm).

    Read from the catalog entry's top-level ``height_mm`` field. Distinct
    from ``size.height`` (which is the 2D UI footprint). Falls back to
    ``default_mm`` *with a logged warning* if the field is missing or
    malformed -- callers (in particular the Z transform) need a number
    no matter what, but a bogus z_robot for a wrongly-configured tag is
    a real safety concern, hence the visible warning. Real passes
    ``DEFAULT_COMPONENT_HEIGHT_MM`` (env-tunable) as the default.
    """
    meta = catalog_get(tag_id) or {}
    raw = meta.get("height_mm")
    if raw is None:
        print(
            f"{log_prefix} Warning: catalog entry for {tag_id} has no "
            f"'height_mm'; using default {default_mm:.1f} mm. "
            f"Add it to the catalog JSON."
        )
        return float(default_mm)
    try:
        return float(raw)
    except (TypeError, ValueError):
        print(
            f"{log_prefix} Warning: catalog entry for {tag_id} has invalid "
            f"height_mm={raw!r}; using default {default_mm:.1f} mm."
        )
        return float(default_mm)


def motor_catalog_ok(
    catalog_get: CatalogLookup,
    target_id: str,
    motor_id: int,
) -> bool:
    """``True`` iff ``target_id`` is in the catalog and declares ``motor_id``.

    Used as a guard before dispatching ``move_motor`` / ``motor_send_home``
    so we never send a motor command to a hardware ID that doesn't
    exist on a given component.
    """
    meta = catalog_get(target_id)
    if not meta:
        return False
    mids = meta.get("motor_ids") or []
    return motor_id in mids
