"""
Storage quadrant Q3: x < 0 and y < 0 (lab mm, origin at table center).

Inventory uses a fixed row-major grid: each STORED part reserves one cell; nominal pose is the
cell center. Vision pose is valid if the axis-aligned footprint (width × height) fits inside
that cell rectangle.
"""
from __future__ import annotations

import math
import random
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

LAB_X_MIN = -500.0
LAB_X_MAX = 500.0
LAB_Y_MIN = -500.0
LAB_Y_MAX = 500.0

DANGER_RADIUS_MM = 90.0
PADDING_MM = 5.0

# Q3 spans (mm)
Q3_WIDTH_MM = 0.0 - LAB_X_MIN
Q3_HEIGHT_MM = 0.0 - LAB_Y_MIN

# Grid resolution (row-major: j = row from bottom, i = column from left).
# 5×5 over Q3 (500×500 mm) → 100×100 mm cells; easier to fit real parts than a denser grid.
STORAGE_GRID_NX = 5
STORAGE_GRID_NY = 5


def is_storage_region(x: float, y: float) -> bool:
    return x < 0.0 and y < 0.0


def is_placed_region(x: float, y: float) -> bool:
    return not is_storage_region(x, y)


def layout_consistent(state: str, x: float, y: float) -> bool:
    if state == "STORED":
        return is_storage_region(x, y)
    if state == "PLACED":
        return is_placed_region(x, y)
    return True


def cell_dimensions_mm() -> Tuple[float, float]:
    return Q3_WIDTH_MM / STORAGE_GRID_NX, Q3_HEIGHT_MM / STORAGE_GRID_NY


def cell_bounds(i: int, j: int) -> Tuple[float, float, float, float]:
    """Half-open-friendly bounds [xmin, xmax) × [ymin, ymax) in lab mm."""
    cw, ch = cell_dimensions_mm()
    xmin = LAB_X_MIN + i * cw
    xmax = LAB_X_MIN + (i + 1) * cw
    ymin = LAB_Y_MIN + j * ch
    ymax = LAB_Y_MIN + (j + 1) * ch
    return xmin, xmax, ymin, ymax


def cell_center(i: int, j: int) -> Tuple[float, float]:
    xmin, xmax, ymin, ymax = cell_bounds(i, j)
    return (xmin + xmax) / 2.0, (ymin + ymax) / 2.0


# Standard in-plane rotation (degrees) when a part is parked in inventory.
STORAGE_NOMINAL_ROTATION_DEG = 0.0


def nominal_center_pose_for_stored_entry(entry: Dict[str, Any]) -> Optional[Tuple[float, float, int, int]]:
    """
    Nominal pose for a STORED part: center of its grid cell at standard rotation.
    Uses metadata.storage_slot when present; otherwise infers the cell from the current center pose in Q3.
    Returns (cx_mm, cy_mm, i, j) or None if the cell cannot be resolved.
    """
    if not isinstance(entry, dict) or entry.get("state") != "STORED":
        return None
    pose = entry.get("pose") or {}
    try:
        px = float(pose.get("x", 0.0))
        py = float(pose.get("y", 0.0))
    except (TypeError, ValueError):
        return None
    md = entry.get("metadata") or {}
    slot = md.get("storage_slot")
    if isinstance(slot, dict) and "i" in slot and "j" in slot:
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
    for j in range(STORAGE_GRID_NY):
        for i in range(STORAGE_GRID_NX):
            out.append((i, j))
    return out


def cell_index_for_point(px: float, py: float) -> Optional[Tuple[int, int]]:
    """Which grid cell contains this point (center location), if inside Q3."""
    if not is_storage_region(px, py):
        return None
    cw, ch = cell_dimensions_mm()
    # Map to indices; clamp inside [0, nx-1] if on boundary 0
    if px >= 0 or py >= 0:
        return None
    i = int((px - LAB_X_MIN) / cw)
    j = int((py - LAB_Y_MIN) / ch)
    if i < 0 or i >= STORAGE_GRID_NX or j < 0 or j >= STORAGE_GRID_NY:
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
        if not isinstance(entry, dict) or entry.get("state") != "STORED":
            continue
        md = entry.get("metadata") or {}
        slot = md.get("storage_slot")
        if isinstance(slot, dict) and "i" in slot and "j" in slot:
            occ.add((int(slot["i"]), int(slot["j"])))
    return occ


def _center_clear_of_danger(cx: float, cy: float, w: float, h: float) -> bool:
    r = math.sqrt(w * w + h * h) / 2.0
    return math.hypot(cx, cy) >= DANGER_RADIUS_MM + r + PADDING_MM


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
        # Part larger than a single cell — cannot use grid (caller may scale grid constants)
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
    return {
        "nx": STORAGE_GRID_NX,
        "ny": STORAGE_GRID_NY,
        "cell_width_mm": cw,
        "cell_height_mm": ch,
        "nominal_storage_rotation_deg": STORAGE_NOMINAL_ROTATION_DEG,
        "q3": {"x_min": LAB_X_MIN, "x_max": 0.0, "y_min": LAB_Y_MIN, "y_max": 0.0},
    }


def analyze_layout_issues(
    components: Dict[str, Any],
    get_size_for_tag: Callable[[str], Tuple[float, float]],
) -> List[Dict[str, Any]]:
    """
    Structured issues for UI modals.
    Kinds: PLACED_IN_Q3, STORED_OUTSIDE_Q3, STORED_OFF_SLOT
    """
    issues: List[Dict[str, Any]] = []
    for tag_id, entry in components.items():
        if not isinstance(entry, dict):
            continue
        st = entry.get("state")
        pose = entry.get("pose") or {}
        try:
            px = float(pose.get("x", 0.0))
            py = float(pose.get("y", 0.0))
        except (TypeError, ValueError):
            continue
        w, h = get_size_for_tag(tag_id)

        if st == "PLACED" and is_storage_region(px, py):
            issues.append(
                {
                    "tag_id": tag_id,
                    "kind": "PLACED_IN_Q3",
                    "message": f"{tag_id} is PLACED but its center lies in the storage quadrant (Q3).",
                }
            )
            continue

        if st != "STORED":
            continue

        if not is_storage_region(px, py):
            issues.append(
                {
                    "tag_id": tag_id,
                    "kind": "STORED_OUTSIDE_Q3",
                    "message": f"{tag_id} is STORED but its center is not in Q3 (x<0, y<0).",
                }
            )
            continue

        md = entry.get("metadata") or {}
        slot = md.get("storage_slot")
        i: Optional[int] = None
        j: Optional[int] = None
        if isinstance(slot, dict) and "i" in slot and "j" in slot:
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
    pad: float = PADDING_MM,
) -> bool:
    if math.hypot(x, y) < DANGER_RADIUS_MM + r + pad:
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
    r_self = _circ_r(width_mm, height_mm)
    others: List[Tuple[float, float, float]] = []
    for tid, entry in components.items():
        if tid == tag_id:
            continue
        if not isinstance(entry, dict):
            continue
        if entry.get("state") not in ("PLACED", "STORED"):
            continue
        pose = entry.get("pose") or {}
        try:
            ox = float(pose.get("x", 0.0))
            oy = float(pose.get("y", 0.0))
        except (TypeError, ValueError):
            continue
        ow, oh = get_size_for_tag(tid)
        others.append((ox, oy, _circ_r(ow, oh)))

    for _ in range(200):
        x = random.uniform(LAB_X_MIN + r_self, LAB_X_MAX - r_self)
        y = random.uniform(LAB_Y_MIN + r_self, LAB_Y_MAX - r_self)
        if not is_placed_region(x, y):
            continue
        if not _collides(x, y, r_self, others):
            return (x, y)
    return None
