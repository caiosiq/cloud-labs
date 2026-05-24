"""Shared motor-rotation <-> snapshot merge logic.

Software-side motor angle tracking lives in
``lab_model.domain.motor_rotation_store``. On each lab-state export:

- **Measurables** ``pose.motor_rotations`` — tracked encoder readback (θ).
- **Tunables** ``nominal_motor_positions`` — operator intent (setpoints).
  Existing setpoint keys are never overwritten by the tracker; only missing
  motor ids are seeded from tracked θ so first-load UI has a value.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from lab_model import motor_rotation_store as motor_rot
from lab_model.domain.component import (
    get_measurables,
    get_tunables,
    measurables_bucket,
    tunables_bucket,
)


CatalogLookup = Callable[[str], Optional[Dict[str, Any]]]


def set_nominal_motor_angle(
    entry: Dict[str, Any], motor_id: int, angle_deg: float
) -> None:
    """Write one motor setpoint into component tunables intent."""
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
    """Merge motor_rotation_store into component measurables (and seed tunables)."""
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
        meas = measurables_bucket(comp)
        pose = meas.setdefault("pose", {})
        if isinstance(pose, dict):
            pose["motor_rotations"] = dict(mr)
        tun = tunables_bucket(comp)
        nm = tun.setdefault("nominal_motor_positions", {})
        if not isinstance(nm, dict):
            nm = {}
            tun["nominal_motor_positions"] = nm
        for k, v in mr.items():
            ks = str(k)
            if ks not in nm:
                nm[ks] = float(v)


def persist_motor_rotations_from_component(
    tag_id: str, comp: Dict[str, Any], motor_ids: List[int]
) -> None:
    """Write motor angles from checkpoint merge into motor_rotation_store."""
    mid_list = []
    for m in motor_ids:
        try:
            mid_list.append(int(m))
        except (TypeError, ValueError):
            continue
    if not mid_list:
        return
    tun_nm = get_tunables(comp).get("nominal_motor_positions") or {}
    mr_pose = (get_measurables(comp).get("pose") or {}).get("motor_rotations") or {}
    out: Dict[str, float] = {}
    for mid in mid_list:
        ks = str(mid)
        v = tun_nm.get(ks)
        if v is None:
            v = mr_pose.get(ks)
        if v is None:
            continue
        try:
            out[ks] = float(v)
        except (TypeError, ValueError):
            continue
    if out:
        motor_rot.set_rotations_for_tag(tag_id, out)
