"""Shared motor-rotation <-> snapshot merge logic.

The lab communicator keeps software-side motor angle tracking in
``lab_model.motor_rotation_store``. Every time we hand a snapshot to the
UI we want those tracked angles to appear on each component's
``measurables.pose.motor_rotations`` (read-only display) and
``tunables.nominal_motor_positions`` (UI editable, source of truth for
"where do you want this motor to go next"). That merge is identical in
the real and mock backends; the only thing they vary in is how they look
up a component's catalog metadata. We pass the lookup in as a callable.

Architectural rule (``communicator_refactor.md`` §5.1): no
``lab_automation`` import, no :mod:`lab_communicator.base` import.
``lab_model`` is fine -- it's the cross-backend data layer.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from lab_model import motor_rotation_store as motor_rot
from lab_model.component_model import default_measurables, default_tunables


CatalogLookup = Callable[[str], Optional[Dict[str, Any]]]
"""``(tag_id) -> catalog meta dict or None``.

Real passes ``lambda t: catalog_map.get(t)`` (O(1) dict lookup).
Mock passes ``self._catalog_meta_for_tag`` (linear scan over the catalog
list it loaded from disk). The shared logic doesn't care which.
"""


def inject_motor_rotations_into_state(
    state: Dict[str, Any],
    catalog_lookup: CatalogLookup,
) -> None:
    """Merge software motor angle tracker into ``state['components']``.

    For every component that has ``motor_ids`` declared in the catalog,
    populate:

    - ``components[tag].measurables.pose.motor_rotations``: dict mapping
      motor_id (str) -> tracked angle (deg). Read-only display.
    - ``components[tag].tunables.nominal_motor_positions``: dict mapping
      motor_id (str) -> tracked angle (deg). The UI's editable slider
      sources its initial value from here.

    Mutates ``state`` in place. Components without ``motor_ids`` are
    untouched. Components missing ``measurables`` / ``tunables`` are
    backfilled with defaults so the merge never raises on a partial
    snapshot.
    """
    components = state.get("components") or {}
    if not isinstance(components, dict):
        return
    for tag_id, comp in components.items():
        if not isinstance(comp, dict):
            continue
        meta = catalog_lookup(tag_id)
        mids = (meta or {}).get("motor_ids") or []
        if not mids:
            continue
        mr = motor_rot.get_rotations_for_motor_ids(tag_id, list(mids))
        meas = comp.setdefault("measurables", default_measurables())
        pose = meas.setdefault("pose", {})
        if isinstance(pose, dict):
            pose["motor_rotations"] = dict(mr)
        tun = comp.setdefault("tunables", default_tunables())
        nm = tun.setdefault("nominal_motor_positions", {})
        for k, v in mr.items():
            nm[str(k)] = float(v)
