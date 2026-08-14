"""Per-backend coordinator store (VC + working FSM + Twin overlays).

Edge-owned data (library, inventory, layout, motor rotations) does **not**
live here — see ``docs/BACKEND_ISOLATION.md``.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass
from typing import Optional, Tuple

_BACKEND_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")


@dataclass(frozen=True)
class CoordinatorDataPaths:
    backend_id: str
    root_dir: str
    lab_state_json: str
    control_dir: str
    laser_lines_json: str
    recipes_dir: str
    created: bool


def default_coordinator_data_path(backend_id: str) -> str:
    return os.path.join("coordinator_data", backend_id.strip())


def validate_backend_id_for_path(backend_id: str) -> str:
    safe = (backend_id or "").strip()
    if not safe or not _BACKEND_ID_RE.match(safe):
        raise ValueError(f"invalid backend_id for coordinator_data: {backend_id!r}")
    return safe


def _atomic_write_json(path: str, payload: dict) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def _empty_lab_state() -> dict:
    return {
        "system_status": "IDLE",
        "holding": {
            "tag_id": None,
            "nominal_pose": None,
            "requires_operator_confirm": False,
        },
        "components": {},
        "alignment_guides": [],
        "optimization_step": 0,
        "optimization_run_dir": None,
        "optimization_target_id": None,
    }


def _empty_laser_lines() -> dict:
    return {"version": 1, "lines": [], "snap_line_id": None}


def _resolve_project_rel(project_root: str, path: str) -> str:
    raw = (path or "").strip()
    if not raw:
        return ""
    if not os.path.isabs(raw):
        raw = os.path.join(project_root, raw)
    return os.path.abspath(raw)


def _copy_laser_lines_seed(dest: str, *source_roots: str) -> bool:
    """Copy the first existing ``laser_lines.json`` under ``source_roots`` to dest."""
    for root in source_roots:
        if not root:
            continue
        src = os.path.join(root, "laser_lines.json")
        if os.path.isfile(src):
            shutil.copy2(src, dest)
            return True
    return False


def _laser_lines_file_is_empty(path: str) -> bool:
    """True when missing, unreadable, or ``lines`` is absent/empty."""
    if not os.path.isfile(path):
        return True
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            doc = json.load(fh)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return True
    if not isinstance(doc, dict):
        return True
    lines = doc.get("lines")
    return not isinstance(lines, list) or len(lines) == 0


def ensure_coordinator_data(
    project_root: str,
    backend_id: str,
    *,
    coordinator_data_path: str = "",
    migrate_from_lab_view: str = "",
    coordinator_seed_path: str = "",
) -> CoordinatorDataPaths:
    """Create or reuse ``coordinator_data/<backend_id>/`` (thin store).

    When ``migrate_from_lab_view`` points at an old fat lab_view and the
    coordinator ``control/`` is empty, copy ``control/`` and ``recipes/`` once
    so existing Twin VC history is not dropped.

    ``coordinator_seed_path`` supplies Twin overlays (e.g. laser lines) for
    HTTP-edge backends that have no local lab_view bundle.
    """
    safe = validate_backend_id_for_path(backend_id)
    rel = (coordinator_data_path or default_coordinator_data_path(safe)).strip()
    root = rel if os.path.isabs(rel) else os.path.join(project_root, rel)
    root = os.path.abspath(root)
    created = not os.path.isdir(root)

    control_dir = os.path.join(root, "control")
    recipes_dir = os.path.join(root, "recipes")
    lab_state_json = os.path.join(root, "lab_state.json")
    laser_lines_json = os.path.join(root, "laser_lines.json")
    migrate_root = _resolve_project_rel(project_root, migrate_from_lab_view)
    seed_root = _resolve_project_rel(project_root, coordinator_seed_path)

    os.makedirs(control_dir, exist_ok=True)
    os.makedirs(recipes_dir, exist_ok=True)

    if migrate_root:
        _maybe_migrate_dir(os.path.join(migrate_root, "control"), control_dir)
        _maybe_migrate_dir(os.path.join(migrate_root, "recipes"), recipes_dir)
        if not os.path.isfile(lab_state_json):
            src_state = os.path.join(migrate_root, "lab_state.json")
            if os.path.isfile(src_state):
                shutil.copy2(src_state, lab_state_json)

    # Laser lines: migrate lab_view → coordinator seed → empty placeholder.
    # Upgrade an auto-created empty placeholder when a non-empty seed exists.
    if _laser_lines_file_is_empty(laser_lines_json):
        if not _copy_laser_lines_seed(laser_lines_json, migrate_root, seed_root):
            if not os.path.isfile(laser_lines_json):
                _atomic_write_json(laser_lines_json, _empty_laser_lines())

    if not os.path.isfile(lab_state_json):
        _atomic_write_json(lab_state_json, _empty_lab_state())

    action = "created" if created else "reused"
    print(
        f"[backend] coordinator_data {action} backend_id={safe!r} path={root!r}",
        flush=True,
    )
    return CoordinatorDataPaths(
        backend_id=safe,
        root_dir=root,
        lab_state_json=lab_state_json,
        control_dir=control_dir,
        laser_lines_json=laser_lines_json,
        recipes_dir=recipes_dir,
        created=created,
    )


def _maybe_migrate_dir(src: str, dest: str) -> None:
    if not os.path.isdir(src):
        return
    try:
        dest_names = [
            n for n in os.listdir(dest) if n not in (".gitkeep", ".DS_Store")
        ]
    except OSError:
        dest_names = []
    if dest_names:
        return
    for name in os.listdir(src):
        if name in (".gitkeep", ".DS_Store"):
            continue
        s = os.path.join(src, name)
        d = os.path.join(dest, name)
        if os.path.isdir(s):
            shutil.copytree(s, d, dirs_exist_ok=True)
        elif os.path.isfile(s):
            shutil.copy2(s, d)


def apply_coordinator_overrides(
    paths: "LabViewPaths",
    coord: CoordinatorDataPaths,
) -> "LabViewPaths":
    """Return ``paths`` with coordinator-owned fields remapped."""
    from lab_model.coordinator.backends.lab_view_config import LabViewPaths

    return LabViewPaths(
        root_dir=paths.root_dir,
        layout_json=paths.layout_json,
        laser_lines_json=coord.laser_lines_json,
        component_library_json=paths.component_library_json,
        active_catalog_json=paths.active_catalog_json,
        motor_rotations_json=paths.motor_rotations_json,
        lab_state_json=coord.lab_state_json,
        stored_intent_json=paths.stored_intent_json,
        session_checkpoint_json=paths.session_checkpoint_json,
        recipes_dir=coord.recipes_dir,
        states_dir=paths.states_dir,
        control_dir=coord.control_dir,
        camera_captures_dir=paths.camera_captures_dir,
        table_cam_preview_json=paths.table_cam_preview_json,
        lab_manifest_json=paths.lab_manifest_json,
    )


def coordinator_only_paths(coord: CoordinatorDataPaths) -> "LabViewPaths":
    """Synthetic LabViewPaths for HTTP-edge backends with no local edge lab_view."""
    from lab_model.coordinator.backends.lab_view_config import LabViewPaths

    # Unused edge fields stay empty strings — callers must use edge HTTP.
    return LabViewPaths(
        root_dir=coord.root_dir,
        layout_json="",
        laser_lines_json=coord.laser_lines_json,
        component_library_json="",
        active_catalog_json="",
        motor_rotations_json="",
        lab_state_json=coord.lab_state_json,
        stored_intent_json=os.path.join(coord.root_dir, "stored_intent.json"),
        session_checkpoint_json=os.path.join(
            coord.root_dir, "session_last_lab_state.json"
        ),
        recipes_dir=coord.recipes_dir,
        states_dir=os.path.join(coord.root_dir, "states"),
        control_dir=coord.control_dir,
        camera_captures_dir=os.path.join(coord.root_dir, "camera_captures"),
        table_cam_preview_json=os.path.join(coord.root_dir, "table_cam_preview.json"),
        lab_manifest_json=os.path.join(coord.root_dir, "lab_manifest.json"),
    )


__all__ = [
    "CoordinatorDataPaths",
    "apply_coordinator_overrides",
    "coordinator_only_paths",
    "default_coordinator_data_path",
    "ensure_coordinator_data",
    "validate_backend_id_for_path",
]
