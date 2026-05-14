"""
Inventory storage in the negative-x / negative-y corner of the lab frame (origin at table center).

Rule ``negative_xy`` tile the rectangle from ``(storage_rect_x_min, storage_rect_y_min)`` up to the
axes (exclusive at ``x == 0`` / ``y == 0``). By default that rectangle is the full lab quadrant
(up to ``lab_bounds_mm``). Optional ``storage.extent_from_origin_mm`` in ``layout.json`` limits how
far from the corner at ``(0,0)`` the grid extends (**width_mm** / **height_mm** into negative X/Y).

Geometry is loaded from lab_view ``layout.json`` via :func:`configure_from_layout_document`.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from .component_model import (
    PRESENCE_BREADBOARD,
    PRESENCE_STORAGE,
    is_stored,
    meas_pose,
    presence_of,
    storage_slot,
)


@dataclass(frozen=True)
class LabLayoutSnapshot:
    lab_x_min: float
    lab_x_max: float
    lab_y_min: float
    lab_y_max: float
    danger_radius_mm: float
    padding_mm: float
    storage_grid_nx: int
    storage_grid_ny: int
    storage_rule: str
    #: West/south edges of the storage rectangle (negative values). Cells tile to ``(0, 0)`` (axes excluded).
    storage_rect_x_min: float
    storage_rect_y_min: float
    breadboard_grid_spacing_mm: float
    breadboard_origin_offset_x_mm: float
    breadboard_origin_offset_y_mm: float


_geom: Optional[LabLayoutSnapshot] = None


def reset_lab_layout_for_tests() -> None:
    global _geom
    _geom = None


def get_lab_layout_snapshot() -> LabLayoutSnapshot:
    if _geom is None:
        raise RuntimeError("storage_region not configured; bootstrap_lab_view must run first.")
    return _geom


def configure_from_layout_document(document: Dict[str, Any]) -> LabLayoutSnapshot:
    """Populate module-level geometry from lab_view ``layout.json`` (called once at startup)."""
    global _geom
    if not isinstance(document, dict):
        raise ValueError("layout document must be a dict")
    bb = document["lab_bounds_mm"]
    dz = document["danger_zone"]
    st = document["storage"]
    br = document.get("breadboard") or {}
    rule = str(st.get("rule") or "negative_xy")
    if rule != "negative_xy":
        raise ValueError(f'Unsupported storage.rule: {rule!r} (only "negative_xy" is implemented)')

    lab_x_min = float(bb["x_min"])
    lab_x_max = float(bb["x_max"])
    lab_y_min = float(bb["y_min"])
    lab_y_max = float(bb["y_max"])

    # Full negative quadrant clipped to lab bounds (legacy default).
    storage_rect_x_min = lab_x_min
    storage_rect_y_min = lab_y_min
    ext = st.get("extent_from_origin_mm")
    if ext is not None:
        if not isinstance(ext, dict):
            raise ValueError("storage.extent_from_origin_mm must be an object with width_mm and height_mm")
        try:
            ew = float(ext["width_mm"])
            eh = float(ext["height_mm"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                "storage.extent_from_origin_mm requires numeric width_mm and height_mm "
                "(extent into negative X and negative Y from the table-center corner)"
            ) from exc
        if ew <= 0 or eh <= 0:
            raise ValueError("extent_from_origin_mm width_mm and height_mm must be positive")
        # Rectangle adjacent to origin: [-ew, 0) × [-eh, 0), intersected with lab_bounds.
        storage_rect_x_min = max(lab_x_min, -abs(ew))
        storage_rect_y_min = max(lab_y_min, -abs(eh))

    snapshot = LabLayoutSnapshot(
        lab_x_min=lab_x_min,
        lab_x_max=lab_x_max,
        lab_y_min=lab_y_min,
        lab_y_max=lab_y_max,
        danger_radius_mm=float(dz["radius_mm"]),
        padding_mm=float(dz.get("padding_mm", 5.0)),
        storage_grid_nx=int(st["grid_nx"]),
        storage_grid_ny=int(st["grid_ny"]),
        storage_rule=rule,
        storage_rect_x_min=storage_rect_x_min,
        storage_rect_y_min=storage_rect_y_min,
        breadboard_grid_spacing_mm=float(br.get("grid_spacing_mm", 25.0)),
        breadboard_origin_offset_x_mm=float((br.get("origin_offset_mm") or {}).get("x", 0.0)),
        breadboard_origin_offset_y_mm=float((br.get("origin_offset_mm") or {}).get("y", 0.0)),
    )
    _geom = snapshot
    return snapshot


def lab_x_min() -> float:
    return get_lab_layout_snapshot().lab_x_min


def lab_x_max() -> float:
    return get_lab_layout_snapshot().lab_x_max


def lab_y_min() -> float:
    return get_lab_layout_snapshot().lab_y_min


def lab_y_max() -> float:
    return get_lab_layout_snapshot().lab_y_max


def danger_radius_mm() -> float:
    return get_lab_layout_snapshot().danger_radius_mm


def padding_mm() -> float:
    return get_lab_layout_snapshot().padding_mm


def q3_width_mm() -> float:
    """East–west span of the **configured** storage rectangle (toward ``x == 0``)."""
    g = get_lab_layout_snapshot()
    return 0.0 - g.storage_rect_x_min


def q3_height_mm() -> float:
    """North–south span of the **configured** storage rectangle (toward ``y == 0``)."""
    g = get_lab_layout_snapshot()
    return 0.0 - g.storage_rect_y_min


def storage_grid_nx() -> int:
    return get_lab_layout_snapshot().storage_grid_nx


def storage_grid_ny() -> int:
    return get_lab_layout_snapshot().storage_grid_ny


STORAGE_NOMINAL_ROTATION_DEG = 0.0


def is_storage_region(x: float, y: float) -> bool:
    """Inside the tiled storage rectangle open toward the origin (exclusive on ``x==0``, ``y==0``)."""
    g = get_lab_layout_snapshot()
    return (
        g.storage_rect_x_min <= x < 0.0
        and g.storage_rect_y_min <= y < 0.0
    )


def is_placed_region(x: float, y: float) -> bool:
    return not is_storage_region(x, y)


def layout_consistent(presence: str, x: float, y: float) -> bool:
    if presence == PRESENCE_STORAGE:
        return is_storage_region(x, y)
    if presence == PRESENCE_BREADBOARD:
        return is_placed_region(x, y)
    return True


def cell_dimensions_mm() -> Tuple[float, float]:
    g = get_lab_layout_snapshot()
    cw = q3_width_mm() / g.storage_grid_nx
    ch = q3_height_mm() / g.storage_grid_ny
    return cw, ch


def cell_bounds(i: int, j: int) -> Tuple[float, float, float, float]:
    """Half-open-friendly bounds [xmin, xmax) × [ymin, ymax) in lab mm."""
    g = get_lab_layout_snapshot()
    cw, ch = cell_dimensions_mm()
    xmin = g.storage_rect_x_min + i * cw
    xmax = g.storage_rect_x_min + (i + 1) * cw
    ymin = g.storage_rect_y_min + j * ch
    ymax = g.storage_rect_y_min + (j + 1) * ch
    return xmin, xmax, ymin, ymax


def cell_center(i: int, j: int) -> Tuple[float, float]:
    xmin, xmax, ymin, ymax = cell_bounds(i, j)
    return (xmin + xmax) / 2.0, (ymin + ymax) / 2.0


def nominal_center_pose_for_stored_entry(entry: Dict[str, Any]) -> Optional[Tuple[float, float, int, int]]:
    """
    Nominal pose for a STORED part: center of its grid cell at standard rotation.
    Uses tunables.storage.slot when present; otherwise infers the cell from the current center pose
    inside the configured storage rectangle.
    Returns (cx_mm, cy_mm, i, j) or None if the cell cannot be resolved.
    """
    if not isinstance(entry, dict) or not is_stored(entry):
        return None
    pose = meas_pose(entry)
    try:
        px = float(pose.get("x", 0.0))
        py = float(pose.get("y", 0.0))
    except (TypeError, ValueError):
        return None
    slot = storage_slot(entry)
    if slot is not None:
        i, j = int(slot["i"]), int(slot["j"])
    else:
        inferred = cell_index_for_point(px, py)
        if inferred is None:
            return None
        i, j = inferred
    cx, cy = cell_center(i, j)
    return (cx, cy, i, j)


def iter_slots_row_major() -> List[Tuple[int, int]]:
    out: List[Tuple[int, int]] = []
    g = get_lab_layout_snapshot()
    for j in range(g.storage_grid_ny):
        for i in range(g.storage_grid_nx):
            out.append((i, j))
    return out


def cell_index_for_point(px: float, py: float) -> Optional[Tuple[int, int]]:
    """Which grid cell contains this point (center location), if inside the storage rectangle."""
    if not is_storage_region(px, py):
        return None
    g = get_lab_layout_snapshot()
    cw, ch = cell_dimensions_mm()
    i = int((px - g.storage_rect_x_min) / cw)
    j = int((py - g.storage_rect_y_min) / ch)
    if i < 0 or i >= g.storage_grid_nx or j < 0 or j >= g.storage_grid_ny:
        return None
    return (i, j)


def _axis_aligned_footprint_fits_in_rect(
    cx: float,
    cy: float,
    width_mm: float,
    height_mm: float,
    xmin: float,
    xmax: float,
    ymin: float,
    ymax: float,
    eps: float = 0.5,
) -> bool:
    hw, hh = width_mm / 2.0, height_mm / 2.0
    return (
        cx - hw >= xmin - eps
        and cx + hw <= xmax + eps
        and cy - hh >= ymin - eps
        and cy + hh <= ymax + eps
    )


def pose_valid_for_storage_slot(
    cx: float,
    cy: float,
    width_mm: float,
    height_mm: float,
    i: int,
    j: int,
) -> bool:
    xmin, xmax, ymin, ymax = cell_bounds(i, j)
    return _axis_aligned_footprint_fits_in_rect(cx, cy, width_mm, height_mm, xmin, xmax, ymin, ymax)


def occupied_slots(components: Dict[str, Any], exclude_tag_id: Optional[str] = None) -> Set[Tuple[int, int]]:
    occ: Set[Tuple[int, int]] = set()
    for tid, entry in components.items():
        if exclude_tag_id is not None and tid == exclude_tag_id:
            continue
        if not isinstance(entry, dict) or not is_stored(entry):
            continue
        slot = storage_slot(entry)
        if slot is not None:
            occ.add((int(slot["i"]), int(slot["j"])))
    return occ


def _center_clear_of_danger(cx: float, cy: float, w: float, h: float) -> bool:
    g = get_lab_layout_snapshot()
    r = math.sqrt(w * w + h * h) / 2.0
    return math.hypot(cx, cy) >= g.danger_radius_mm + r + g.padding_mm


def find_storage_slot_and_center(
    components: Dict[str, Any],
    tag_id: str,
    width_mm: float,
    height_mm: float,
    get_size_for_tag: Callable[[str], Tuple[float, float]],
) -> Optional[Tuple[float, float, int, int]]:
    """
    Row-major first free cell; pose = cell center. Returns (cx, cy, i, j) or None.
    """
    _ = get_size_for_tag  # reserved for future per-part sizing rules
    occ = occupied_slots(components, exclude_tag_id=tag_id)
    cw, ch = cell_dimensions_mm()
    if max(width_mm, height_mm) > min(cw, ch) + 1e-6:
        return None

    for i, j in iter_slots_row_major():
        if (i, j) in occ:
            continue
        cx, cy = cell_center(i, j)
        if not _center_clear_of_danger(cx, cy, width_mm, height_mm):
            continue
        xmin, xmax, ymin, ymax = cell_bounds(i, j)
        if not _axis_aligned_footprint_fits_in_rect(cx, cy, width_mm, height_mm, xmin, xmax, ymin, ymax):
            continue
        return (cx, cy, i, j)
    return None


def storage_grid_spec() -> Dict[str, Any]:
    cw, ch = cell_dimensions_mm()
    g = get_lab_layout_snapshot()
    return {
        "nx": g.storage_grid_nx,
        "ny": g.storage_grid_ny,
        "cell_width_mm": cw,
        "cell_height_mm": ch,
        "nominal_storage_rotation_deg": STORAGE_NOMINAL_ROTATION_DEG,
        "q3": {
            "x_min": g.storage_rect_x_min,
            "x_max": 0.0,
            "y_min": g.storage_rect_y_min,
            "y_max": 0.0,
        },
    }


def analyze_layout_issues(
    components: Dict[str, Any],
    get_size_for_tag: Callable[[str], Tuple[float, float]],
    stored_intent: Optional[Dict[str, Dict[str, int]]] = None,
) -> List[Dict[str, Any]]:
    """
    Structured issues for UI modals.
    Kinds: PLACED_IN_Q3, STORED_OUTSIDE_Q3, STORED_OFF_SLOT, STORED_WRONG_SLOT
    """
    issues: List[Dict[str, Any]] = []
    for tag_id, entry in components.items():
        if not isinstance(entry, dict):
            continue
        pres = presence_of(entry)
        pose = meas_pose(entry)
        try:
            px = float(pose.get("x", 0.0))
            py = float(pose.get("y", 0.0))
        except (TypeError, ValueError):
            continue
        w, h = get_size_for_tag(tag_id)

        if pres == PRESENCE_BREADBOARD and is_storage_region(px, py):
            if stored_intent is not None and tag_id not in stored_intent:
                continue
            issues.append(
                {
                    "tag_id": tag_id,
                    "kind": "PLACED_IN_Q3",
                    "message": f"{tag_id} is intended on the breadboard but its measured center lies in the inventory storage rectangle.",
                }
            )
            continue

        if pres != PRESENCE_STORAGE:
            continue

        if not is_storage_region(px, py):
            issues.append(
                {
                    "tag_id": tag_id,
                    "kind": "STORED_OUTSIDE_Q3",
                    "message": f"{tag_id} is STORED but its measured center is outside the inventory storage rectangle (see layout.json).",
                }
            )
            continue

        slot = storage_slot(entry)
        i: Optional[int] = None
        j: Optional[int] = None
        if slot is not None:
            i, j = int(slot["i"]), int(slot["j"])
        else:
            inferred = cell_index_for_point(px, py)
            if inferred is None:
                issues.append(
                    {
                        "tag_id": tag_id,
                        "kind": "STORED_OFF_SLOT",
                        "message": f"{tag_id} is STORED but center is not inside any storage cell.",
                    }
                )
                continue
            i, j = inferred

        if stored_intent is not None and tag_id in stored_intent:
            exp = stored_intent[tag_id]
            ei, ej = int(exp["i"]), int(exp["j"])
            if i != ei or j != ej:
                issues.append(
                    {
                        "tag_id": tag_id,
                        "kind": "STORED_WRONG_SLOT",
                        "message": (
                            f"{tag_id} is recorded as stored in cell ({ei},{ej}) but the measured center "
                            f"maps to cell ({i},{j})."
                        ),
                        "expected_cell": {"i": ei, "j": ej},
                        "measured_cell": {"i": i, "j": j},
                    }
                )

        if not pose_valid_for_storage_slot(px, py, w, h, i, j):
            issues.append(
                {
                    "tag_id": tag_id,
                    "kind": "STORED_OFF_SLOT",
                    "message": f"{tag_id} STORED pose/footprint does not fit its inventory cell ({i},{j}).",
                    "cell": {"i": i, "j": j},
                }
            )

    return issues


def _circ_r(w: float, h: float) -> float:
    return math.sqrt(w * w + h * h) / 2.0


def _collides(
    x: float,
    y: float,
    r: float,
    others: List[Tuple[float, float, float]],
    pad: float,
) -> bool:
    g = get_lab_layout_snapshot()
    if math.hypot(x, y) < g.danger_radius_mm + r + pad:
        return True
    for ox, oy, or_ in others:
        if math.hypot(x - ox, y - oy) < r + or_ + pad:
            return True
    return False


def random_placed_position(
    components: Dict[str, Any],
    tag_id: str,
    width_mm: float,
    height_mm: float,
    get_size_for_tag: Callable[[str], Tuple[float, float]],
) -> Optional[Tuple[float, float]]:
    """Random pose in placed region, avoiding overlaps (breadboard adds)."""
    g = get_lab_layout_snapshot()
    r_self = _circ_r(width_mm, height_mm)
    others: List[Tuple[float, float, float]] = []
    for tid, entry in components.items():
        if tid == tag_id:
            continue
        if not isinstance(entry, dict):
            continue
        if presence_of(entry) not in (PRESENCE_BREADBOARD, PRESENCE_STORAGE):
            continue
        pose = meas_pose(entry)
        try:
            ox = float(pose.get("x", 0.0))
            oy = float(pose.get("y", 0.0))
        except (TypeError, ValueError):
            continue
        ow, oh = get_size_for_tag(tid)
        others.append((ox, oy, _circ_r(ow, oh)))

    for _ in range(200):
        x = random.uniform(g.lab_x_min + r_self, g.lab_x_max - r_self)
        y = random.uniform(g.lab_y_min + r_self, g.lab_y_max - r_self)
        if not is_placed_region(x, y):
            continue
        if not _collides(x, y, r_self, others, g.padding_mm):
            return (x, y)
    return None
