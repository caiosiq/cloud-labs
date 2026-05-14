"""Resolve ``LAB_VIEW_PATH`` (per lab deployment) and required JSON bundles.

Everything that used to scatter across ``schemas/``, repo-root ``states/``, and recipe roots now hangs off the
resolved directory configured at process start.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

_LINE_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

_lab_paths_singleton: Optional["LabViewPaths"] = None


@dataclass(frozen=True)
class LabViewPaths:
    root_dir: str
    layout_json: str
    laser_lines_json: str
    component_library_json: str
    active_catalog_json: str
    motor_rotations_json: str
    lab_state_json: str
    stored_intent_json: str
    recipes_dir: str
    states_dir: str
    camera_captures_dir: str


def get_lab_view_paths() -> LabViewPaths:
    if _lab_paths_singleton is None:
        raise RuntimeError("lab_view bootstrap did not run; call bootstrap_lab_view() from main.")
    return _lab_paths_singleton


def get_lab_view_paths_optional() -> Optional[LabViewPaths]:
    return _lab_paths_singleton


def reset_lab_viewpaths_for_tests() -> None:
    """Test-only — allow loading a fresh ``LAB_VIEW_PATH`` bundle in-process."""
    global _lab_paths_singleton
    _lab_paths_singleton = None


def bootstrap_lab_view(project_root: str) -> LabViewPaths:
    """Load ``LAB_VIEW_PATH``, configure storage geometry, register paths.

    No legacy fallbacks: missing required files abort startup loudly.
    """
    global _lab_paths_singleton

    raw = (os.getenv("LAB_VIEW_PATH") or "").strip()
    if not raw:
        raise SystemExit(
            "[CONFIG] LAB_VIEW_PATH is required — absolute or project-relative path "
            'to your lab bundle directory (must contain layout.json, laser_lines.json, '
            "component_library.json, active_catalog.json)."
        )

    root = raw if os.path.isabs(raw) else os.path.abspath(os.path.join(project_root, raw))
    if not os.path.isdir(root):
        raise SystemExit(f"[CONFIG] LAB_VIEW_PATH does not exist or is not a directory: {root}")

    paths = LabViewPaths(
        root_dir=root,
        layout_json=os.path.join(root, "layout.json"),
        laser_lines_json=os.path.join(root, "laser_lines.json"),
        component_library_json=os.path.join(root, "component_library.json"),
        active_catalog_json=os.path.join(root, "active_catalog.json"),
        motor_rotations_json=os.path.join(root, "motor_rotations.json"),
        lab_state_json=os.path.join(root, "lab_state.json"),
        stored_intent_json=os.path.join(root, "stored_intent.json"),
        recipes_dir=os.path.join(root, "recipes"),
        states_dir=os.path.join(root, "states"),
        camera_captures_dir=os.path.join(root, "camera_captures"),
    )

    mandatory = (
        paths.layout_json,
        paths.laser_lines_json,
        paths.component_library_json,
        paths.active_catalog_json,
        paths.motor_rotations_json,
    )
    missing = [p for p in mandatory if not os.path.isfile(p)]
    if missing:
        raise SystemExit(
            "[CONFIG] lab_view bundle incomplete; missing:\n  " + "\n  ".join(missing)
        )

    with open(paths.layout_json, "r", encoding="utf-8") as f:
        layout_document = json.load(f)
    if not isinstance(layout_document, dict):
        raise SystemExit(f"[CONFIG] layout.json must contain a JSON object: {paths.layout_json}")

    from lab_model.storage_region import configure_from_layout_document

    configure_from_layout_document(layout_document)

    os.makedirs(paths.recipes_dir, exist_ok=True)
    os.makedirs(paths.states_dir, exist_ok=True)
    os.makedirs(paths.camera_captures_dir, exist_ok=True)

    if not os.path.isfile(paths.stored_intent_json):
        atomic_write_json(
            paths.stored_intent_json,
            {
                "version": 1,
                "updated_at": datetime.now().isoformat(),
                "stored": {},
            },
        )

    _lab_paths_singleton = paths
    return paths


def load_layout_document() -> Dict[str, Any]:
    p = get_lab_view_paths().layout_json
    with open(p, "r", encoding="utf-8") as f:
        doc = json.load(f)
    if not isinstance(doc, dict):
        raise ValueError(f"layout.json invalid: {p}")
    return doc


def atomic_write_json(path: str, data: Any) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def read_laser_lines_doc() -> Dict[str, Any]:
    path = get_lab_view_paths().laser_lines_json
    if not os.path.isfile(path):
        raise FileNotFoundError(f"laser_lines.json missing: {path}")
    with open(path, "r", encoding="utf-8") as f:
        doc = json.load(f)
    if not isinstance(doc, dict):
        raise ValueError(f"laser_lines.json must be a JSON object: {path}")
    return doc


def write_laser_lines_doc(doc: Dict[str, Any]) -> None:
    atomic_write_json(get_lab_view_paths().laser_lines_json, doc)


def two_points_to_ab(
    p1: Dict[str, Any], p2: Dict[str, Any]
) -> Optional[Tuple[float, float]]:
    """Return (a, b) for x = a*y + b in lab mm, or vertical (0, x0). None if degenerate."""
    try:
        x1 = float(p1["x"])
        y1 = float(p1["y"])
        x2 = float(p2["x"])
        y2 = float(p2["y"])
    except (TypeError, KeyError, ValueError):
        return None
    if abs(x2 - x1) < 1e-9 and abs(y2 - y1) < 1e-9:
        return None
    if abs(x2 - x1) < 1e-9:
        return 0.0, x1
    if abs(y2 - y1) < 1e-9:
        return None
    a = (x2 - x1) / (y2 - y1)
    b = x1 - a * y1
    return float(a), float(b)


def laser_line_coeffs_from_doc(doc: Dict[str, Any], lab_mode: str) -> Dict[str, Any]:
    """Legacy single-line coefficients for GET /api/laser-line (snap reference)."""
    lines = doc.get("lines") or []
    snap_id = doc.get("snap_line_id")
    by_id = {
        ln["id"]: ln
        for ln in lines
        if isinstance(ln, dict) and isinstance(ln.get("id"), str)
    }
    chosen = None
    if snap_id and snap_id in by_id:
        cand = by_id[snap_id]
        if cand.get("enabled", True):
            chosen = cand
    if chosen is None:
        for ln in lines:
            if not isinstance(ln, dict):
                continue
            if ln.get("enabled", True) and ln.get("p1") and ln.get("p2"):
                chosen = ln
                break
    if chosen is None:
        return {
            "a": 0.0,
            "b": 0.0,
            "source": "lab_view",
            "loaded": False,
            "lab_mode": lab_mode,
        }
    ab = two_points_to_ab(chosen["p1"], chosen["p2"])
    if ab is None:
        return {
            "a": 0.0,
            "b": 0.0,
            "source": "lab_view",
            "loaded": False,
            "lab_mode": lab_mode,
            "snap_line_id": chosen.get("id"),
        }
    a, b = ab
    return {
        "a": a,
        "b": b,
        "source": "lab_view",
        "loaded": True,
        "lab_mode": lab_mode,
        "snap_line_id": chosen.get("id"),
    }


def line_id_pattern() -> re.Pattern[str]:
    return _LINE_ID_RE
