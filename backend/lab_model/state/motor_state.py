"""Shared motor-rotation <-> snapshot merge logic.

Software-side motor angle tracking lives in
``lab_model.domain.motor_rotation_store``. On each lab-state export the
tracker **recalculates** ``tunables.nominal_motor_positions`` (same DOF
family as motor setpoints). Motor angles are not a measurable.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from lab_model import motor_rotation_store as motor_rot
from lab_model.domain.component import (
    get_tunables,
    tunables_bucket,
)


CatalogLookup = Callable[[str], Optional[Dict[str, Any]]]


def set_nominal_motor_angle(
    entry: Dict[str, Any], motor_id: int, angle_deg: float
) -> None:
    """Write one motor angle into ``tunables.nominal_motor_positions``."""
    tun = tunables_bucket(entry)
    nmp = tun.setdefault("nominal_motor_positions", {})
    if not isinstance(nmp, dict):
        nmp = {}
        tun["nominal_motor_positions"] = nmp
    nmp[str(int(motor_id))] = float(angle_deg)


def inject_motor_rotations_into_state(
    state: Dict[str, Any],
    catalog_lookup: CatalogLookup,
) -> None:
    """Recalculate ``tunables.nominal_motor_positions`` from the motor store."""
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
        tun = tunables_bucket(comp)
        nm = tun.setdefault("nominal_motor_positions", {})
        if not isinstance(nm, dict):
            nm = {}
            tun["nominal_motor_positions"] = nm
        for k, v in mr.items():
            nm[str(k)] = float(v)


def persist_motor_rotations_from_component(
    tag_id: str, comp: Dict[str, Any], motor_ids: List[int]
) -> None:
    """Write motor angles from ``nominal_motor_positions`` into the store."""
    mid_list = []
    for m in motor_ids:
        try:
            mid_list.append(int(m))
        except (TypeError, ValueError):
            continue
    if not mid_list:
        return
    tun_nm = get_tunables(comp).get("nominal_motor_positions") or {}
    out: Dict[str, float] = {}
    for mid in mid_list:
        ks = str(mid)
        v = tun_nm.get(ks)
        if v is None:
            continue
        try:
            out[ks] = float(v)
        except (TypeError, ValueError):
            continue
    if out:
        motor_rot.set_rotations_for_tag(tag_id, out)
