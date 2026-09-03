"""Shared seat-DAG planner for batch reconcile / group-move.

Owns Park/Unpark via layout ``reconcile_staging_seats``, predecessor edges,
and fail-closed capacity reports. Command Matrix only drains the resulting
envelopes (see ``docs/BATCH_DAG_AND_MATRIX.md``).

DAG edges use the Twin/part-part footprint rule (circumscribed circle + pad),
not arm-path planning and not quantized seat identity alone.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple, Union

from lab_model.language.domain.component import PRESENCE_BREADBOARD, PRESENCE_STORAGE
from lab_model.language.primitives.ids import PrimitiveId

from .diff import configuration_diff

# Envelope keys the matrix / Twin may read; stripped before primitive validation
# when needed (Pydantic ignores unknowns by default).
PLAN_META_KEYS = frozenset({"plan_step_id", "predecessors", "plan_role"})

_SEAT_QUANT_MM = 1.0
_ROT_QUANT_DEG = 1.0
_STAGING_MATCH_MM = 2.0
# Match Twin ``checkCollision`` (frontend/js/canvas/interaction.js).
_DEFAULT_FOOTPRINT_PAD_MM = 5.0
_DEFAULT_FOOTPRINT_WH_MM = (90.0, 90.0)

SizeFn = Callable[[str], Tuple[float, float]]
SupportsSetExposureFn = Callable[[str], bool]


class BatchPlanError(ValueError):
    """Raised when a batch cannot be planned fail-closed."""

    def __init__(self, report: Mapping[str, Any], message: Optional[str] = None) -> None:
        self.report = dict(report)
        super().__init__(message or self.report.get("message") or "batch plan not ready")


SeatKey = Union[Tuple[str, int, int, int], Tuple[str, int, int], Tuple[str, int]]


def circ_radius_mm(width_mm: float, height_mm: float) -> float:
    """Circumscribed radius of an axis-aligned rectangle (Twin collision model)."""
    return math.sqrt(float(width_mm) ** 2 + float(height_mm) ** 2) / 2.0


def footprints_collide(
    x1: float,
    y1: float,
    w1: float,
    h1: float,
    x2: float,
    y2: float,
    w2: float,
    h2: float,
    *,
    pad_mm: float = _DEFAULT_FOOTPRINT_PAD_MM,
) -> bool:
    """True when two component footprints would collide (circle + pad).

    Same rule as Twin ``checkCollision`` / ``storage_region._collides``:
    ``hypot(dx, dy) < r1 + r2 + pad``.
    """
    min_dist = circ_radius_mm(w1, h1) + circ_radius_mm(w2, h2) + float(pad_mm)
    return math.hypot(float(x1) - float(x2), float(y1) - float(y2)) < min_dist


def _default_size(_tag_id: str) -> Tuple[float, float]:
    return _DEFAULT_FOOTPRINT_WH_MM


def _layout_pad_mm() -> float:
    try:
        from lab_model.language.domain.storage_region import get_lab_layout_snapshot

        return float(get_lab_layout_snapshot().padding_mm)
    except Exception:
        return _DEFAULT_FOOTPRINT_PAD_MM


def _q_xy(x: float, y: float) -> Tuple[int, int]:
    return (int(round(float(x) / _SEAT_QUANT_MM)), int(round(float(y) / _SEAT_QUANT_MM)))


def _q_rot(rotation: float) -> int:
    return int(round(float(rotation) / _ROT_QUANT_DEG))


def bench_seat_key(x: float, y: float, rotation: float = 0.0) -> SeatKey:
    qx, qy = _q_xy(x, y)
    return ("bench", qx, qy, _q_rot(rotation))


def storage_seat_key(i: int, j: int) -> SeatKey:
    return ("storage", int(i), int(j))


def staging_seat_key(index: int) -> SeatKey:
    return ("staging", int(index))


def parse_staging_seats(layout_or_seats: Any) -> List[Dict[str, float]]:
    """Normalize ``reconcile_staging_seats`` from a layout doc or raw list."""
    raw = layout_or_seats
    if isinstance(layout_or_seats, Mapping):
        raw = layout_or_seats.get("reconcile_staging_seats")
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, float]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        try:
            out.append(
                {
                    "x": float(item["x"]),
                    "y": float(item["y"]),
                    "rotation": float(item.get("rotation", 0.0)),
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    return out


def pose_matches_staging(
    pose: Mapping[str, Any],
    staging_seats: Sequence[Mapping[str, Any]],
    *,
    tol_mm: float = _STAGING_MATCH_MM,
) -> Optional[int]:
    """Return staging index if ``pose`` sits on a declared staging seat."""
    try:
        x = float(pose.get("x", 0.0))
        y = float(pose.get("y", 0.0))
    except (TypeError, ValueError):
        return None
    for idx, seat in enumerate(staging_seats):
        try:
            sx = float(seat["x"])
            sy = float(seat["y"])
        except (KeyError, TypeError, ValueError):
            continue
        if abs(x - sx) <= tol_mm and abs(y - sy) <= tol_mm:
            return idx
    return None


def strip_staging_from_configuration(
    configuration: Mapping[str, Any],
    staging_seats: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Drop breadboard components whose nominal pose is a staging seat.

    Staging is reconcile scratch only — never versioned in VC commits.
    """
    seats = parse_staging_seats(list(staging_seats))
    cfg = dict(configuration)
    comps_in = cfg.get("components") or {}
    if not isinstance(comps_in, dict) or not seats:
        return cfg
    comps_out: Dict[str, Any] = {}
    for tag_id, entry in comps_in.items():
        if not isinstance(tag_id, str) or not isinstance(entry, dict):
            continue
        tun = ((entry.get("statecontrol") or {}).get("tunables") or {})
        if not isinstance(tun, dict):
            comps_out[tag_id] = entry
            continue
        presence = tun.get("presence", PRESENCE_BREADBOARD)
        storage = tun.get("storage") or {}
        in_storage = presence == PRESENCE_STORAGE or (
            isinstance(storage, dict) and storage.get("in_storage")
        )
        pose = tun.get("nominal_pose")
        if (
            not in_storage
            and isinstance(pose, dict)
            and pose_matches_staging(pose, seats) is not None
        ):
            continue
        comps_out[tag_id] = entry
    cfg["components"] = comps_out
    return cfg


def staging_commit_issues(
    configuration: Mapping[str, Any],
    staging_seats: Sequence[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    """Doctor issues when a commit would version a staging seat pose."""
    seats = parse_staging_seats(list(staging_seats))
    issues: List[Dict[str, Any]] = []
    comps = configuration.get("components") or {}
    if not isinstance(comps, dict) or not seats:
        return issues
    for tag_id, entry in comps.items():
        if not isinstance(tag_id, str) or not isinstance(entry, dict):
            continue
        tun = ((entry.get("statecontrol") or {}).get("tunables") or {})
        if not isinstance(tun, dict):
            continue
        presence = tun.get("presence", PRESENCE_BREADBOARD)
        storage = tun.get("storage") or {}
        in_storage = presence == PRESENCE_STORAGE or (
            isinstance(storage, dict) and storage.get("in_storage")
        )
        pose = tun.get("nominal_pose")
        if in_storage or not isinstance(pose, dict):
            continue
        idx = pose_matches_staging(pose, seats)
        if idx is not None:
            issues.append(
                {
                    "kind": "staging_seat_in_commit",
                    "tag_id": tag_id,
                    "staging_index": idx,
                    "blocking": True,
                    "message": (
                        f"{tag_id} sits on reconcile staging seat {idx}; "
                        "staging poses are not versioned"
                    ),
                }
            )
    return issues


def strip_plan_meta(envelope: Mapping[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in envelope.items() if k not in PLAN_META_KEYS}


@dataclass
class BatchPlanResult:
    ready: bool
    commands: List[Dict[str, Any]] = field(default_factory=list)
    report: Dict[str, Any] = field(default_factory=dict)

    def reconcile_plan(self) -> List[Dict[str, Any]]:
        """Sequential envelopes safe for ``execute_reconcile_plan``."""
        return [strip_plan_meta(c) for c in self.commands]


def _entry_tunables(cfg: Mapping[str, Any], tag_id: str) -> Dict[str, Any]:
    comp = (cfg.get("components") or {}).get(tag_id) or {}
    tun = (comp.get("statecontrol") or {}).get("tunables") or {}
    return tun if isinstance(tun, dict) else {}


def _cfg_presence(cfg: Mapping[str, Any], tag_id: str) -> str:
    tun = _entry_tunables(cfg, tag_id)
    presence = tun.get("presence", PRESENCE_BREADBOARD)
    if presence == PRESENCE_STORAGE:
        return PRESENCE_STORAGE
    storage = tun.get("storage") or {}
    if isinstance(storage, dict) and storage.get("in_storage"):
        return PRESENCE_STORAGE
    return PRESENCE_BREADBOARD


def _slot_ij(storage_val: Any) -> Optional[Tuple[int, int]]:
    if not isinstance(storage_val, dict):
        return None
    slot = storage_val.get("slot")
    if not isinstance(slot, dict):
        return None
    if slot.get("i") is None or slot.get("j") is None:
        return None
    try:
        return int(slot["i"]), int(slot["j"])
    except (TypeError, ValueError):
        return None


def _pose_from_tunables(tun: Mapping[str, Any]) -> Optional[Dict[str, float]]:
    pose = tun.get("nominal_pose")
    if not isinstance(pose, dict):
        return None
    try:
        return {
            "x": float(pose.get("x", 0.0)),
            "y": float(pose.get("y", 0.0)),
            "rotation": float(pose.get("rotation", 0.0)),
        }
    except (TypeError, ValueError):
        return None


def _bench_key_from_pose(pose: Mapping[str, float]) -> SeatKey:
    return bench_seat_key(pose["x"], pose["y"], pose.get("rotation", 0.0))


def _pose_params(pose: Mapping[str, Any]) -> Dict[str, Any]:
    params: Dict[str, Any] = {
        "target_x": float(pose.get("x", 0.0)),
        "target_y": float(pose.get("y", 0.0)),
        "rotation": float(pose.get("rotation", 0.0)),
    }
    if pose.get("z") is not None:
        params["z"] = float(pose["z"])
    return params


def _store_params_from_storage(storage_val: Any) -> Dict[str, Any]:
    ij = _slot_ij(storage_val)
    if ij is None:
        return {}
    return {"slot_i": ij[0], "slot_j": ij[1]}


def _component_ids(cfg: Mapping[str, Any]) -> Set[str]:
    comps = cfg.get("components") or {}
    if not isinstance(comps, dict):
        return set()
    return {str(t) for t in comps.keys() if isinstance(t, str) and not t.startswith("<")}


@dataclass
class _SpatialIntent:
    tag_id: str
    kind: str  # remove | add | move | stay | storage_slot
    from_seat: Optional[SeatKey]
    to_seat: Optional[SeatKey]
    from_pose: Optional[Dict[str, float]] = None
    to_pose: Optional[Dict[str, float]] = None
    store_params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class _PlanStep:
    step_id: str
    action: str
    target_id: str
    parameters: Dict[str, Any]
    role: str
    predecessors: List[str] = field(default_factory=list)
    phase: int = 0


def _classify_spatial(
    current_cfg: Mapping[str, Any],
    target_cfg: Mapping[str, Any],
    *,
    size_fn: SizeFn,
    pad_mm: float,
) -> Tuple[List[_SpatialIntent], List[Dict[str, Any]]]:
    """Return spatial intents + early issues (target footprint overlap, illegal)."""
    issues: List[Dict[str, Any]] = []
    intents: List[_SpatialIntent] = []
    tags = _component_ids(current_cfg) | _component_ids(target_cfg)

    target_poses: Dict[str, Dict[str, float]] = {}
    for tag_id in sorted(tags):
        in_cur = tag_id in _component_ids(current_cfg)
        in_tgt = tag_id in _component_ids(target_cfg)
        cur_pres = _cfg_presence(current_cfg, tag_id) if in_cur else None
        tgt_pres = _cfg_presence(target_cfg, tag_id) if in_tgt else None
        cur_tun = _entry_tunables(current_cfg, tag_id) if in_cur else {}
        tgt_tun = _entry_tunables(target_cfg, tag_id) if in_tgt else {}
        cur_pose = _pose_from_tunables(cur_tun) if cur_pres == PRESENCE_BREADBOARD else None
        tgt_pose = _pose_from_tunables(tgt_tun) if tgt_pres == PRESENCE_BREADBOARD else None
        cur_slot = _slot_ij(cur_tun.get("storage")) if cur_pres == PRESENCE_STORAGE else None
        tgt_slot = _slot_ij(tgt_tun.get("storage")) if tgt_pres == PRESENCE_STORAGE else None

        from_seat: Optional[SeatKey] = None
        to_seat: Optional[SeatKey] = None
        if cur_pose is not None:
            from_seat = _bench_key_from_pose(cur_pose)
        elif cur_slot is not None:
            from_seat = storage_seat_key(*cur_slot)
        if tgt_pose is not None:
            to_seat = _bench_key_from_pose(tgt_pose)
            target_poses[tag_id] = tgt_pose
        elif tgt_slot is not None:
            to_seat = storage_seat_key(*tgt_slot)

        # Membership / presence classification
        if in_cur and not in_tgt:
            if cur_pres == PRESENCE_BREADBOARD:
                intents.append(
                    _SpatialIntent(
                        tag_id=tag_id,
                        kind="remove",
                        from_seat=from_seat,
                        to_seat=None,
                        from_pose=cur_pose,
                        store_params={},
                    )
                )
            # Already stored and dropped from target: no hardware (legacy).
            continue

        if in_tgt and not in_cur:
            if tgt_pres == PRESENCE_STORAGE:
                intents.append(
                    _SpatialIntent(
                        tag_id=tag_id,
                        kind="remove",
                        from_seat=from_seat,
                        to_seat=to_seat,
                        from_pose=cur_pose,
                        to_pose=tgt_pose,
                        store_params=_store_params_from_storage(tgt_tun.get("storage")),
                    )
                )
            else:
                intents.append(
                    _SpatialIntent(
                        tag_id=tag_id,
                        kind="add",
                        from_seat=from_seat,
                        to_seat=to_seat,
                        to_pose=tgt_pose,
                    )
                )
            continue

        if not in_cur or not in_tgt:
            continue

        if cur_pres == PRESENCE_BREADBOARD and tgt_pres == PRESENCE_STORAGE:
            intents.append(
                _SpatialIntent(
                    tag_id=tag_id,
                    kind="remove",
                    from_seat=from_seat,
                    to_seat=to_seat,
                    from_pose=cur_pose,
                    store_params=_store_params_from_storage(tgt_tun.get("storage")),
                )
            )
            continue

        if cur_pres == PRESENCE_STORAGE and tgt_pres == PRESENCE_BREADBOARD:
            intents.append(
                _SpatialIntent(
                    tag_id=tag_id,
                    kind="add",
                    from_seat=from_seat,
                    to_seat=to_seat,
                    to_pose=tgt_pose,
                )
            )
            continue

        if cur_pres == PRESENCE_STORAGE and tgt_pres == PRESENCE_STORAGE:
            if cur_slot != tgt_slot and tgt_slot is not None:
                intents.append(
                    _SpatialIntent(
                        tag_id=tag_id,
                        kind="storage_slot",
                        from_seat=from_seat,
                        to_seat=to_seat,
                        store_params=_store_params_from_storage(tgt_tun.get("storage")),
                    )
                )
            continue

        if cur_pres == PRESENCE_BREADBOARD and tgt_pres == PRESENCE_BREADBOARD:
            if from_seat != to_seat and tgt_pose is not None:
                intents.append(
                    _SpatialIntent(
                        tag_id=tag_id,
                        kind="move",
                        from_seat=from_seat,
                        to_seat=to_seat,
                        from_pose=cur_pose,
                        to_pose=tgt_pose,
                    )
                )
            else:
                intents.append(
                    _SpatialIntent(
                        tag_id=tag_id,
                        kind="stay",
                        from_seat=from_seat,
                        to_seat=to_seat,
                        from_pose=cur_pose,
                        to_pose=tgt_pose,
                    )
                )

    # Target–target footprint collisions (Twin circle + pad), including near-miss.
    tgt_ids = sorted(target_poses.keys())
    for i, a in enumerate(tgt_ids):
        pa = target_poses[a]
        wa, ha = size_fn(a)
        for b in tgt_ids[i + 1 :]:
            pb = target_poses[b]
            wb, hb = size_fn(b)
            if footprints_collide(
                pa["x"], pa["y"], wa, ha, pb["x"], pb["y"], wb, hb, pad_mm=pad_mm
            ):
                issues.append(
                    {
                        "kind": "target_overlap",
                        "tag_id": a,
                        "other_tag_id": b,
                        "blocking": True,
                        "message": (
                            f"{a} and {b} target footprints collide "
                            f"(pad={pad_mm:g} mm)"
                        ),
                    }
                )

    return intents, issues


def _current_bench_poses(
    current_cfg: Mapping[str, Any],
) -> Dict[str, Dict[str, float]]:
    poses: Dict[str, Dict[str, float]] = {}
    for tag_id in _component_ids(current_cfg):
        if _cfg_presence(current_cfg, tag_id) != PRESENCE_BREADBOARD:
            continue
        pose = _pose_from_tunables(_entry_tunables(current_cfg, tag_id))
        if pose is not None:
            poses[tag_id] = pose
    return poses


def _current_bench_occupation(
    current_cfg: Mapping[str, Any],
) -> Dict[SeatKey, str]:
    occ: Dict[SeatKey, str] = {}
    for tag_id, pose in _current_bench_poses(current_cfg).items():
        occ[_bench_key_from_pose(pose)] = tag_id
    return occ


def _find_cycles(deps: Mapping[str, Set[str]]) -> List[List[str]]:
    """Return simple cycles among move tags (nodes = tag ids)."""
    nodes = set(deps.keys())
    for dsts in deps.values():
        nodes |= set(dsts)
    index = 0
    stack: List[str] = []
    on_stack: Set[str] = set()
    indices: Dict[str, int] = {}
    lowlink: Dict[str, int] = {}
    sccs: List[List[str]] = []

    def strongconnect(v: str) -> None:
        nonlocal index
        indices[v] = index
        lowlink[v] = index
        index += 1
        stack.append(v)
        on_stack.add(v)
        for w in deps.get(v, ()):
            if w not in indices:
                strongconnect(w)
                lowlink[v] = min(lowlink[v], lowlink[w])
            elif w in on_stack:
                lowlink[v] = min(lowlink[v], indices[w])
        if lowlink[v] == indices[v]:
            comp: List[str] = []
            while True:
                w = stack.pop()
                on_stack.discard(w)
                comp.append(w)
                if w == v:
                    break
            if len(comp) > 1:
                sccs.append(comp)
            elif comp and comp[0] in deps.get(comp[0], ()):
                sccs.append(comp)

    for n in sorted(nodes):
        if n not in indices:
            strongconnect(n)
    return sccs


def _build_move_deps(
    moves: Sequence[_SpatialIntent],
    *,
    current_poses: Mapping[str, Mapping[str, float]],
    size_fn: SizeFn,
    pad_mm: float,
    leaving: Set[str],
) -> Tuple[Dict[str, Set[str]], List[Dict[str, Any]]]:
    """Build move deps from footprint collision with current occupants.

    preds[B].add(A) when B's *target* collides with A's *current* footprint
    (Twin circle + pad), so A must leave first. Stayers that collide → issue.
    """
    by_tag = {m.tag_id: m for m in moves if m.kind == "move"}
    preds: Dict[str, Set[str]] = {t: set() for t in by_tag}
    issues: List[Dict[str, Any]] = []

    for tag, intent in by_tag.items():
        dest = intent.to_pose
        if dest is None:
            continue
        tw, th = size_fn(tag)
        for other, opose in current_poses.items():
            if other == tag:
                continue
            ow, oh = size_fn(other)
            if not footprints_collide(
                dest["x"],
                dest["y"],
                tw,
                th,
                float(opose["x"]),
                float(opose["y"]),
                ow,
                oh,
                pad_mm=pad_mm,
            ):
                continue
            if other in by_tag or other in leaving:
                preds[tag].add(other)
            else:
                issues.append(
                    {
                        "kind": "target_blocked_by_stay",
                        "tag_id": tag,
                        "other_tag_id": other,
                        "blocking": True,
                        "message": (
                            f"{tag} target footprint collides with staying {other}"
                        ),
                    }
                )
    return preds, issues


def _tag_supports_set_exposure(tag_id: str) -> bool:
    """True only when the catalog declares ``SET_EXPOSURE`` for ``tag_id``.

    Spurious ``exposure_time_ms`` values appear on old VC commits for lenses
    and other non-cameras; reconcile must not emit SET_EXPOSURE for those.
    If lab_view is not bootstrapped (unit tests), keep legacy emit behavior.
    Prefer an injected ``supports_set_exposure`` from the edge library for
    HTTP backends where ``lab_view`` is none (e.g. ``real.default``).
    """
    try:
        from lab_model.coordinator.catalog.bundle import library_by_tag
        from lab_model.coordinator.catalog.schema import catalog_declared_primitives

        by_tag = library_by_tag()
    except Exception:
        return True
    row = by_tag.get(str(tag_id))
    if not isinstance(row, dict):
        return False
    return PrimitiveId.SET_EXPOSURE.value in catalog_declared_primitives(row)


def _place_extras(
    target_cfg: Mapping[str, Any],
    tag_id: str,
    *,
    supports_set_exposure: SupportsSetExposureFn,
) -> List[Dict[str, Any]]:
    extras: List[Dict[str, Any]] = []
    tun = _entry_tunables(target_cfg, tag_id)
    motors = tun.get("nominal_motor_positions")
    if isinstance(motors, dict):
        for motor_id, angle in motors.items():
            try:
                extras.append(
                    {
                        "action": PrimitiveId.SET_MOTOR_SETPOINT.value,
                        "target_id": tag_id,
                        "parameters": {
                            "motor_id": int(motor_id),
                            "angle_deg": float(angle),
                        },
                    }
                )
            except (TypeError, ValueError):
                continue
    exposure = tun.get("exposure_time_ms")
    if exposure is not None and supports_set_exposure(tag_id):
        try:
            extras.append(
                {
                    "action": PrimitiveId.SET_EXPOSURE.value,
                    "target_id": tag_id,
                    "parameters": {"exposure_time_ms": float(exposure)},
                }
            )
        except (TypeError, ValueError):
            pass
    return extras


def _emit_tunes(
    current_cfg: Mapping[str, Any],
    target_cfg: Mapping[str, Any],
    *,
    skip_tags: Set[str],
    supports_set_exposure: SupportsSetExposureFn,
) -> List[Dict[str, Any]]:
    """Non-spatial tunable diffs (motors / exposure / pose already handled)."""
    commands: List[Dict[str, Any]] = []
    changes = configuration_diff(current_cfg, target_cfg)
    presence_transition = {
        str(c.get("tag_id"))
        for c in changes
        if str(c.get("path") or "") == "tunables.presence"
        and c.get("tag_id") != "<holding>"
    }
    for change in changes:
        tag_id = change.get("tag_id")
        path = str(change.get("path") or "")
        new_val = change.get("to")
        if not isinstance(tag_id, str) or tag_id.startswith("<"):
            continue
        if tag_id in skip_tags or tag_id in presence_transition:
            # Place extras already cover motors/exposure for adds.
            if path in (
                "tunables.exposure_time_ms",
                "tunables.nominal_motor_positions",
            ) and tag_id in skip_tags:
                pass
            elif path.startswith("tunables.") and tag_id in presence_transition:
                if path != "tunables.exposure_time_ms" and path != "tunables.nominal_motor_positions":
                    continue

        if path == "tunables.exposure_time_ms" and new_val is not None:
            if tag_id in skip_tags:
                continue
            if not supports_set_exposure(tag_id):
                continue
            commands.append(
                {
                    "action": PrimitiveId.SET_EXPOSURE.value,
                    "target_id": tag_id,
                    "parameters": {"exposure_time_ms": float(new_val)},
                }
            )
            continue
        if path == "tunables.nominal_motor_positions" and isinstance(new_val, dict):
            if tag_id in skip_tags:
                continue
            old_val = change.get("from")
            old_map = old_val if isinstance(old_val, dict) else {}
            for motor_id, angle in new_val.items():
                if old_map.get(motor_id) == angle:
                    continue
                commands.append(
                    {
                        "action": PrimitiveId.SET_MOTOR_SETPOINT.value,
                        "target_id": tag_id,
                        "parameters": {
                            "motor_id": int(motor_id),
                            "angle_deg": float(angle),
                        },
                    }
                )
    return commands


def plan_batch(
    current_cfg: Mapping[str, Any],
    target_cfg: Mapping[str, Any],
    *,
    staging_seats: Optional[Sequence[Mapping[str, Any]]] = None,
    free_storage_slots: Optional[int] = None,
    storage_capacity: Optional[int] = None,
    size_fn: Optional[SizeFn] = None,
    pad_mm: Optional[float] = None,
    supports_set_exposure: Optional[SupportsSetExposureFn] = None,
) -> BatchPlanResult:
    """Plan a seat DAG from ``current_cfg`` toward ``target_cfg``.

    ``staging_seats``: layout-declared Park buffers (never in VC commits).
    ``free_storage_slots``: currently empty inventory cells (fail-closed peak).
    ``storage_capacity``: total cells; used with occupation when free is omitted.
    ``size_fn``: ``tag_id -> (width_mm, height_mm)`` for footprint collision
    (defaults to Twin's 90×90 mm). ``pad_mm`` defaults to layout padding or 5 mm.
    ``supports_set_exposure``: ``tag_id -> bool``; when omitted, fall back to
    lab_view ``library_by_tag`` (or legacy emit if lab_view is unbound).
    """
    seats = parse_staging_seats(list(staging_seats or ()))
    sizes = size_fn or _default_size
    pad = float(pad_mm) if pad_mm is not None else _layout_pad_mm()
    exposure_ok = supports_set_exposure or _tag_supports_set_exposure
    intents, issues = _classify_spatial(
        current_cfg, target_cfg, size_fn=sizes, pad_mm=pad
    )
    blocking = [i for i in issues if i.get("blocking")]
    report: Dict[str, Any] = {
        "ready": False,
        "issues": list(issues),
        "needs_staging_n": 0,
        "cycle_tags": [],
        "staging_available": len(seats),
        "message": "",
        "pad_mm": pad,
    }

    if blocking:
        report["message"] = blocking[0].get("message") or "blocking spatial issues"
        return BatchPlanResult(ready=False, commands=[], report=report)

    removes = [i for i in intents if i.kind == "remove"]
    adds = [i for i in intents if i.kind == "add"]
    moves = [i for i in intents if i.kind == "move"]
    slot_moves = [i for i in intents if i.kind == "storage_slot"]

    # Storage peak: all removes before adds (phase order) need free cells.
    if free_storage_slots is None and storage_capacity is not None:
        occupied = 0
        for tag_id in _component_ids(current_cfg):
            if _cfg_presence(current_cfg, tag_id) == PRESENCE_STORAGE:
                occupied += 1
        free_storage_slots = max(0, int(storage_capacity) - occupied)
    if free_storage_slots is not None and len(removes) > int(free_storage_slots):
        issue = {
            "kind": "storage_peak",
            "blocking": True,
            "needed": len(removes),
            "free_storage_slots": int(free_storage_slots),
            "message": (
                f"need {len(removes)} free storage slots for removes; "
                f"have {int(free_storage_slots)}"
            ),
        }
        report["issues"].append(issue)
        report["message"] = issue["message"]
        return BatchPlanResult(ready=False, commands=[], report=report)

    current_poses = _current_bench_poses(current_cfg)
    occupation = _current_bench_occupation(current_cfg)
    leaving = {r.tag_id for r in removes} | {m.tag_id for m in moves}

    move_preds, dep_issues = _build_move_deps(
        moves,
        current_poses=current_poses,
        size_fn=sizes,
        pad_mm=pad,
        leaving=leaving,
    )
    report["issues"].extend(dep_issues)
    if any(i.get("blocking") for i in dep_issues):
        report["message"] = dep_issues[0].get("message") or "blocking footprint issues"
        return BatchPlanResult(ready=False, commands=[], report=report)

    cycles = _find_cycles(move_preds)
    park_tags: List[str] = []
    for cycle in cycles:
        # Park one move-class tag per cycle (deterministic: sorted).
        candidates = sorted(t for t in cycle if any(m.tag_id == t for m in moves))
        if not candidates:
            continue
        park_tags.append(candidates[0])

    report["cycle_tags"] = list(park_tags)
    report["needs_staging_n"] = len(park_tags)
    if len(park_tags) > len(seats):
        issue = {
            "kind": "needs_staging",
            "blocking": True,
            "needs_staging_n": len(park_tags),
            "staging_available": len(seats),
            "cycle_tags": list(park_tags),
            "message": (
                f"need {len(park_tags)} staging seats to break cycles; "
                f"layout declares {len(seats)}"
            ),
        }
        report["issues"].append(issue)
        report["message"] = issue["message"]
        return BatchPlanResult(ready=False, commands=[], report=report)

    park_of: Dict[str, int] = {tag: idx for idx, tag in enumerate(park_tags)}
    effective_moves = [m for m in moves if m.tag_id not in park_of]
    poses_after_park = {
        tid: pose for tid, pose in current_poses.items() if tid not in park_of
    }
    leaving_after = (leaving - set(park_of.keys())) | {r.tag_id for r in removes}

    move_preds2, dep_issues2 = _build_move_deps(
        effective_moves,
        current_poses=poses_after_park,
        size_fn=sizes,
        pad_mm=pad,
        leaving=leaving_after,
    )
    report["issues"].extend(dep_issues2)
    if any(i.get("blocking") for i in dep_issues2):
        report["message"] = dep_issues2[0].get("message") or "blocking footprint issues"
        return BatchPlanResult(ready=False, commands=[], report=report)
    if _find_cycles(move_preds2):
        issue = {
            "kind": "unresolved_cycle",
            "blocking": True,
            "message": "staging break did not eliminate all move cycles",
        }
        report["issues"].append(issue)
        report["message"] = issue["message"]
        return BatchPlanResult(ready=False, commands=[], report=report)

    steps: List[_PlanStep] = []
    step_counter = 0

    def _alloc(action: str, target_id: str, parameters: Dict[str, Any], role: str, phase: int) -> _PlanStep:
        nonlocal step_counter
        sid = f"s{step_counter}"
        step_counter += 1
        # Always store plain action strings. On Python <3.11 the StrEnum polyfill
        # used to stringify as ``PrimitiveId.STORE_COMPONENT``, which Pydantic
        # tagged unions reject during in-process init reconcile.
        action_s = getattr(action, "value", action)
        step = _PlanStep(
            step_id=sid,
            action=str(action_s),
            target_id=target_id,
            parameters=dict(parameters),
            role=role,
            phase=phase,
        )
        steps.append(step)
        return step

    # Phase 1: Park
    park_steps: Dict[str, str] = {}
    for tag, sidx in sorted(park_of.items(), key=lambda kv: kv[1]):
        seat = seats[sidx]
        intent = next(m for m in moves if m.tag_id == tag)
        st = _alloc(
            PrimitiveId.MOVE_COMPONENT,
            tag,
            _pose_params(seat),
            "park",
            phase=1,
        )
        park_steps[tag] = st.step_id

    # Phase 2: Remove (STORE)
    remove_steps: Dict[str, str] = {}
    for rem in sorted(removes, key=lambda r: r.tag_id):
        st = _alloc(
            PrimitiveId.STORE_COMPONENT,
            rem.tag_id,
            dict(rem.store_params),
            "remove",
            phase=2,
        )
        remove_steps[rem.tag_id] = st.step_id

    # Phase 2b: storage slot reshuffle
    for sm in sorted(slot_moves, key=lambda r: r.tag_id):
        _alloc(
            PrimitiveId.STORE_COMPONENT,
            sm.tag_id,
            dict(sm.store_params),
            "storage_slot",
            phase=2,
        )

    # Phase 3: Moves (topo over effective_moves). Pred ids are other step_ids.
    move_step_ids: Dict[str, str] = {}
    remaining = {m.tag_id: m for m in effective_moves}
    pred_steps: Dict[str, Set[str]] = {t: set() for t in remaining}

    for tag, intent in remaining.items():
        for holder in move_preds2.get(tag, ()):
            if holder in remove_steps:
                pred_steps[tag].add(remove_steps[holder])
            elif holder in park_of and holder in park_steps:
                pred_steps[tag].add(park_steps[holder])
            else:
                # holder is another effective move tag
                pred_steps[tag].add(f"__move__:{holder}")
        dest = intent.to_pose
        if dest is None:
            continue
        tw, th = sizes(tag)
        # Wait on removes whose current footprint blocks this target.
        for rem in removes:
            if rem.tag_id not in remove_steps or rem.from_pose is None:
                continue
            rw, rh = sizes(rem.tag_id)
            if footprints_collide(
                dest["x"],
                dest["y"],
                tw,
                th,
                rem.from_pose["x"],
                rem.from_pose["y"],
                rw,
                rh,
                pad_mm=pad,
            ):
                pred_steps[tag].add(remove_steps[rem.tag_id])
        # Wait on parks whose *old* footprint blocked this target.
        for ptag, pintent in ((m.tag_id, m) for m in moves if m.tag_id in park_of):
            if ptag not in park_steps or pintent.from_pose is None:
                continue
            pw, ph = sizes(ptag)
            if footprints_collide(
                dest["x"],
                dest["y"],
                tw,
                th,
                pintent.from_pose["x"],
                pintent.from_pose["y"],
                pw,
                ph,
                pad_mm=pad,
            ):
                pred_steps[tag].add(park_steps[ptag])

    def _pred_satisfied(token: str) -> bool:
        if token.startswith("__move__:"):
            return token.split(":", 1)[1] in move_step_ids
        return True  # concrete step_id already exists (park/remove)

    def _resolve_pred(token: str) -> str:
        if token.startswith("__move__:"):
            return move_step_ids[token.split(":", 1)[1]]
        return token

    while remaining:
        ready_tags = [
            t
            for t in sorted(remaining.keys())
            if all(_pred_satisfied(p) for p in pred_steps.get(t, ()))
        ]
        if not ready_tags:
            issue = {
                "kind": "move_order_deadlock",
                "blocking": True,
                "message": "could not order remaining moves",
            }
            report["issues"].append(issue)
            report["message"] = issue["message"]
            return BatchPlanResult(ready=False, commands=[], report=report)
        for tag in ready_tags:
            intent = remaining.pop(tag)
            assert intent.to_pose is not None
            st = _alloc(
                PrimitiveId.MOVE_COMPONENT,
                tag,
                _pose_params(intent.to_pose),
                "move",
                phase=3,
            )
            st.predecessors = [_resolve_pred(p) for p in sorted(pred_steps.get(tag, ()))]
            move_step_ids[tag] = st.step_id

    # Phase 4: Adds (PLACE) — wait until dest footprint is clear
    add_step_ids: Dict[str, str] = {}
    for add in sorted(adds, key=lambda a: a.tag_id):
        pred_ids: List[str] = []
        assert add.to_pose is not None
        dest = add.to_pose
        aw, ah = sizes(add.tag_id)
        for rem in removes:
            if rem.tag_id not in remove_steps or rem.from_pose is None:
                continue
            rw, rh = sizes(rem.tag_id)
            if footprints_collide(
                dest["x"], dest["y"], aw, ah,
                rem.from_pose["x"], rem.from_pose["y"], rw, rh,
                pad_mm=pad,
            ):
                pred_ids.append(remove_steps[rem.tag_id])
        for mtag, mid in move_step_ids.items():
            mintent = next((m for m in moves if m.tag_id == mtag), None)
            if mintent is None or mintent.from_pose is None:
                continue
            mw, mh = sizes(mtag)
            if footprints_collide(
                dest["x"], dest["y"], aw, ah,
                mintent.from_pose["x"], mintent.from_pose["y"], mw, mh,
                pad_mm=pad,
            ):
                pred_ids.append(mid)
        for ptag in park_of:
            pintent = next(m for m in moves if m.tag_id == ptag)
            if pintent.from_pose is None or ptag not in park_steps:
                continue
            pw, ph = sizes(ptag)
            if footprints_collide(
                dest["x"], dest["y"], aw, ah,
                pintent.from_pose["x"], pintent.from_pose["y"], pw, ph,
                pad_mm=pad,
            ):
                pred_ids.append(park_steps[ptag])
        st = _alloc(
            PrimitiveId.PLACE_FROM_STORAGE,
            add.tag_id,
            _pose_params(add.to_pose),
            "add",
            phase=4,
        )
        st.predecessors = list(dict.fromkeys(pred_ids))
        add_step_ids[add.tag_id] = st.step_id
        for extra in _place_extras(
            target_cfg, add.tag_id, supports_set_exposure=exposure_ok
        ):
            ex = _alloc(
                extra["action"],
                extra["target_id"],
                dict(extra["parameters"]),
                "tune",
                phase=6,
            )
            ex.predecessors = [st.step_id]

    # Phase 5: Unpark — wait until final target footprint is clear
    for tag, sidx in sorted(park_of.items(), key=lambda kv: kv[1]):
        intent = next(m for m in moves if m.tag_id == tag)
        assert intent.to_pose is not None
        pred_ids = [park_steps[tag]]
        dest = intent.to_pose
        tw, th = sizes(tag)
        for other, opose in current_poses.items():
            if other == tag:
                continue
            ow, oh = sizes(other)
            if not footprints_collide(
                dest["x"], dest["y"], tw, th,
                float(opose["x"]), float(opose["y"]), ow, oh,
                pad_mm=pad,
            ):
                continue
            if other in move_step_ids:
                pred_ids.append(move_step_ids[other])
            if other in remove_steps:
                pred_ids.append(remove_steps[other])
        st = _alloc(
            PrimitiveId.MOVE_COMPONENT,
            tag,
            _pose_params(intent.to_pose),
            "unpark",
            phase=5,
        )
        st.predecessors = list(dict.fromkeys(pred_ids))

    # Phase 6: remaining tunes (skip tags that got place extras)
    skip_tune_tags = {a.tag_id for a in adds}
    tune_cmds = _emit_tunes(
        current_cfg,
        target_cfg,
        skip_tags=skip_tune_tags,
        supports_set_exposure=exposure_ok,
    )
    # Spatial ops already emitted; suppress pose/presence/storage from old path via skip.
    # Link tunes after last spatial step for their tag when possible.
    last_spatial: Dict[str, str] = {}
    for st in steps:
        if st.role in ("park", "remove", "move", "add", "unpark", "storage_slot"):
            last_spatial[st.target_id] = st.step_id
    for cmd in tune_cmds:
        tag = str(cmd["target_id"])
        st = _alloc(cmd["action"], tag, dict(cmd["parameters"]), "tune", phase=6)
        if tag in last_spatial:
            st.predecessors = [last_spatial[tag]]

    commands: List[Dict[str, Any]] = []
    for st in steps:
        commands.append(
            {
                "action": st.action,
                "target_id": st.target_id,
                "parameters": st.parameters,
                "plan_step_id": st.step_id,
                "predecessors": list(st.predecessors),
                "plan_role": st.role,
            }
        )

    report["ready"] = True
    report["message"] = "ok"
    report["steps"] = len(commands)
    return BatchPlanResult(ready=True, commands=commands, report=report)


def plan_batch_or_raise(
    current_cfg: Mapping[str, Any],
    target_cfg: Mapping[str, Any],
    **kwargs: Any,
) -> BatchPlanResult:
    result = plan_batch(current_cfg, target_cfg, **kwargs)
    if not result.ready:
        raise BatchPlanError(result.report)
    return result
