"""Named simulation workspace presets stored under ``lab_view/states``.

Simulation presets are deliberately separate from Control configuration
history.  They are complete-enough, normalized workspace states used to spawn
MuJoCo deterministically; loading one becomes uncommitted live runtime state.
"""

from __future__ import annotations

import copy
import json
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional

from lab_model.coordinator.backends.lab_view_config import atomic_write_json
from lab_model.coordinator.state.runtime_manager import default_runtime_state
from lab_model.language.domain.component import (
    PRESENCE_BREADBOARD,
    PRESENCE_OFF_TABLE,
    PRESENCE_STORAGE,
    default_measurables,
    default_telemetry,
    normalize_components_map,
)
from lab_model.language.domain.holding import empty_holding

WORKSPACE_STATE_KIND = "cloud_labs_workspace_state"
WORKSPACE_STATE_SCHEMA_VERSION = 2
AUTHORING_KIND = "cloud_labs_simulation_preset"
AUTHORING_SCHEMA_VERSION = 1

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_RESERVED_SAVE_NAMES = frozenset({"current", "default", "list"})
_GENERATED_TOP_LEVEL_FIELDS = frozenset(
    {
        "active_backend_id",
        "active_job_id",
        "command_matrix",
        "edge_agent",
        "edge_attached",
        "edge_offline",
        "edge_session_id",
        "edge_stale_after_s",
        "edge_state_source",
        "last_runtime_error",
        "runtime_sync",
        "session_lease",
        "simulation_reset",
        "simulator",
    }
)


class SimulationPresetError(ValueError):
    """Raised when a simulation preset name or document is invalid."""


def validate_preset_name(name: str, *, for_save: bool = False) -> str:
    value = str(name or "").strip()
    if not _NAME_RE.fullmatch(value):
        raise SimulationPresetError(
            "preset name must be 1-64 characters: letters, numbers, '_' or '-'"
        )
    if for_save and value.lower() in _RESERVED_SAVE_NAMES:
        raise SimulationPresetError(
            f"{value!r} is reserved; choose another preset name"
        )
    return value


def _preset_path(states_dir: str, name: str, *, for_save: bool = False) -> Path:
    safe = validate_preset_name(name, for_save=for_save)
    root = Path(states_dir).resolve()
    path = (root / f"{safe}.json").resolve()
    if path.parent != root:
        raise SimulationPresetError("preset path escapes the configured states directory")
    return path


def _number(value: Any, *, label: str) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise SimulationPresetError(f"{label} must be a number") from exc
    if not math.isfinite(out):
        raise SimulationPresetError(f"{label} must be finite")
    return out


def _layout_bounds(layout: Optional[Mapping[str, Any]]) -> Optional[Dict[str, float]]:
    raw = (layout or {}).get("lab_bounds_mm")
    if not isinstance(raw, Mapping):
        return None
    try:
        return {
            "x_min": float(raw["x_min"]),
            "x_max": float(raw["x_max"]),
            "y_min": float(raw["y_min"]),
            "y_max": float(raw["y_max"]),
        }
    except (KeyError, TypeError, ValueError):
        return None


def _validate_component(
    tag_id: str,
    entry: Mapping[str, Any],
    *,
    bounds: Optional[Mapping[str, float]],
    layout: Optional[Mapping[str, Any]],
) -> None:
    sc = entry.get("statecontrol")
    tun = sc.get("tunables") if isinstance(sc, Mapping) else None
    if not isinstance(tun, Mapping):
        raise SimulationPresetError(f"{tag_id}: statecontrol.tunables is required")
    pose = tun.get("nominal_pose")
    if not isinstance(pose, Mapping):
        raise SimulationPresetError(f"{tag_id}: tunables.nominal_pose is required")
    x = _number(pose.get("x"), label=f"{tag_id}.nominal_pose.x")
    y = _number(pose.get("y"), label=f"{tag_id}.nominal_pose.y")
    _number(pose.get("rotation", 0.0), label=f"{tag_id}.nominal_pose.rotation")

    presence = str(tun.get("presence") or PRESENCE_BREADBOARD).strip().lower()
    if presence not in {PRESENCE_BREADBOARD, PRESENCE_STORAGE, PRESENCE_OFF_TABLE}:
        raise SimulationPresetError(f"{tag_id}: invalid presence {presence!r}")
    if presence == PRESENCE_BREADBOARD and bounds:
        if not (
            bounds["x_min"] <= x <= bounds["x_max"]
            and bounds["y_min"] <= y <= bounds["y_max"]
        ):
            raise SimulationPresetError(
                f"{tag_id}: nominal pose ({x:g}, {y:g}) is outside lab bounds"
            )

    storage = tun.get("storage")
    if presence == PRESENCE_STORAGE:
        slot = storage.get("slot") if isinstance(storage, Mapping) else None
        if not isinstance(slot, Mapping):
            raise SimulationPresetError(f"{tag_id}: stored component requires a slot")
        try:
            slot_i = int(slot["i"])
            slot_j = int(slot["j"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SimulationPresetError(
                f"{tag_id}: storage slot requires integer i and j"
            ) from exc
        storage_layout = (layout or {}).get("storage")
        if isinstance(storage_layout, Mapping):
            nx = int(storage_layout.get("grid_nx") or 0)
            ny = int(storage_layout.get("grid_ny") or 0)
            if nx > 0 and ny > 0 and not (0 <= slot_i < nx and 0 <= slot_j < ny):
                raise SimulationPresetError(
                    f"{tag_id}: storage slot ({slot_i}, {slot_j}) is outside the grid"
                )


def _catalog_size_mm(
    tag_id: str,
    catalog_components: Optional[Mapping[str, Any]],
) -> tuple[float, float]:
    row = (catalog_components or {}).get(tag_id)
    size = row.get("size") if isinstance(row, Mapping) else None
    if isinstance(size, Mapping):
        try:
            width = float(size.get("width") or 62.0)
            height = float(size.get("height") or 62.0)
            if width > 0 and height > 0:
                return width, height
        except (TypeError, ValueError):
            pass
    if isinstance(size, (int, float)) and float(size) > 0:
        return float(size), float(size)
    return 62.0, 62.0


def _validate_arrangement(
    state: Mapping[str, Any],
    *,
    layout: Optional[Mapping[str, Any]],
    catalog_components: Optional[Mapping[str, Any]],
) -> None:
    """Reject impossible authored arrangements before they become preset files."""
    components = state.get("components")
    if not isinstance(components, Mapping):
        return

    bounds = _layout_bounds(layout)
    danger = (layout or {}).get("danger_zone")
    danger_radius = (
        float(danger.get("radius_mm") or 0.0) if isinstance(danger, Mapping) else 0.0
    )
    padding = (
        float(danger.get("padding_mm") or 5.0) if isinstance(danger, Mapping) else 5.0
    )
    breadboard: list[tuple[str, float, float, float]] = []
    occupied_slots: Dict[tuple[int, int], str] = {}

    for tag_id, entry in components.items():
        if not isinstance(entry, Mapping):
            continue
        sc = entry.get("statecontrol")
        tun = sc.get("tunables") if isinstance(sc, Mapping) else None
        if not isinstance(tun, Mapping):
            continue
        presence = str(tun.get("presence") or PRESENCE_BREADBOARD).strip().lower()
        pose = tun.get("nominal_pose")
        if not isinstance(pose, Mapping):
            continue

        if presence == PRESENCE_STORAGE:
            storage = tun.get("storage")
            slot = storage.get("slot") if isinstance(storage, Mapping) else None
            if isinstance(slot, Mapping):
                key = (int(slot["i"]), int(slot["j"]))
                other = occupied_slots.get(key)
                if other is not None:
                    raise SimulationPresetError(
                        f"storage slot ({key[0]}, {key[1]}) is assigned to both "
                        f"{other} and {tag_id}"
                    )
                occupied_slots[key] = str(tag_id)
                storage_layout = (layout or {}).get("storage")
                storage_bounds = (
                    storage_layout.get("bounds_mm")
                    if isinstance(storage_layout, Mapping)
                    else None
                )
                if isinstance(storage_bounds, Mapping):
                    nx = int(storage_layout.get("grid_nx") or 0)
                    ny = int(storage_layout.get("grid_ny") or 0)
                    if nx > 0 and ny > 0:
                        cell_width = (
                            float(storage_bounds["x_max"])
                            - float(storage_bounds["x_min"])
                        ) / nx
                        cell_height = (
                            float(storage_bounds["y_max"])
                            - float(storage_bounds["y_min"])
                        ) / ny
                        expected_x = float(storage_bounds["x_min"]) + (
                            key[0] + 0.5
                        ) * cell_width
                        expected_y = float(storage_bounds["y_min"]) + (
                            key[1] + 0.5
                        ) * cell_height
                        x = float(pose["x"])
                        y = float(pose["y"])
                        if abs(x - expected_x) > 1e-6 or abs(y - expected_y) > 1e-6:
                            raise SimulationPresetError(
                                f"{tag_id}: storage pose must be the center of slot "
                                f"({key[0]}, {key[1]}): ({expected_x:g}, {expected_y:g})"
                            )
                        width, height = _catalog_size_mm(
                            str(tag_id), catalog_components
                        )
                        if max(width, height) > min(cell_width, cell_height) + 1e-6:
                            raise SimulationPresetError(
                                f"{tag_id}: component footprint does not fit storage slot "
                                f"({key[0]}, {key[1]})"
                            )
            continue
        if presence != PRESENCE_BREADBOARD:
            continue

        x = float(pose["x"])
        y = float(pose["y"])
        width, height = _catalog_size_mm(str(tag_id), catalog_components)
        radius = math.hypot(width, height) / 2.0
        if bounds and not (
            bounds["x_min"] + radius <= x <= bounds["x_max"] - radius
            and bounds["y_min"] + radius <= y <= bounds["y_max"] - radius
        ):
            raise SimulationPresetError(
                f"{tag_id}: component footprint at ({x:g}, {y:g}) is outside lab bounds"
            )
        if danger_radius > 0 and math.hypot(x, y) < danger_radius + padding + radius:
            raise SimulationPresetError(
                f"{tag_id}: component footprint at ({x:g}, {y:g}) enters the danger zone"
            )
        breadboard.append((str(tag_id), x, y, radius))

    for index, (tag_a, x_a, y_a, radius_a) in enumerate(breadboard):
        for tag_b, x_b, y_b, radius_b in breadboard[index + 1 :]:
            if math.hypot(x_a - x_b, y_a - y_b) < radius_a + radius_b + padding:
                raise SimulationPresetError(
                    f"{tag_a} target footprint overlaps {tag_b}"
                )


def normalize_simulation_state(
    state: Mapping[str, Any],
    *,
    catalog_tag_ids: Optional[Iterable[str]] = None,
    layout: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Return a deterministic, IDLE state suitable for saving/spawning MuJoCo."""
    if not isinstance(state, Mapping):
        raise SimulationPresetError("lab_state must be a JSON object")
    components_raw = state.get("components")
    if not isinstance(components_raw, Mapping):
        raise SimulationPresetError("lab_state.components must be an object")

    allowed = {str(tag) for tag in catalog_tag_ids or [] if str(tag)}
    components: Dict[str, Any] = copy.deepcopy(dict(components_raw))
    # Hand-authored presets commonly omit live telemetry.  Add the telemetry
    # shell before the shared component normalizer runs so it treats an entry
    # with statecontrol as the current schema instead of as a legacy component.
    for entry in components.values():
        if isinstance(entry, dict) and isinstance(entry.get("statecontrol"), Mapping):
            entry.setdefault("telemetry", default_telemetry())
    normalize_components_map(components)
    bounds = _layout_bounds(layout)

    for raw_tag, entry in components.items():
        tag_id = str(raw_tag or "").strip()
        if not tag_id or not isinstance(entry, dict):
            raise SimulationPresetError("every component must have a non-empty tag and object value")
        if allowed and tag_id not in allowed:
            raise SimulationPresetError(f"unknown component tag in preset: {tag_id}")
        entry["id"] = str(entry.get("id") or tag_id)
        _validate_component(tag_id, entry, bounds=bounds, layout=layout)

        sc = entry.setdefault("statecontrol", {})
        tun = sc.setdefault("tunables", {})
        nominal = copy.deepcopy(tun["nominal_pose"])
        # A preset describes one deterministic spawn pose.  Reported/legacy
        # pose mirrors start at that pose instead of preserving measurement noise.
        tun["reported_pose"] = copy.deepcopy(nominal)
        meas = default_measurables()
        meas["pose"] = copy.deepcopy(nominal)
        sc["measurables"] = meas
        entry["telemetry"] = default_telemetry()

    out = default_runtime_state()
    out["components"] = components
    for key in ("alignment_guides", "laser_lines"):
        if key in state:
            out[key] = copy.deepcopy(state[key])
    out["holding"] = empty_holding()
    out["system_status"] = "IDLE"
    out["last_updated"] = datetime.now(timezone.utc).isoformat()
    for key in _GENERATED_TOP_LEVEL_FIELDS:
        out.pop(key, None)
    return out


def workspace_document(state: Mapping[str, Any], *, saved_at: Optional[str] = None) -> Dict[str, Any]:
    return {
        "schema_version": WORKSPACE_STATE_SCHEMA_VERSION,
        "kind": WORKSPACE_STATE_KIND,
        "saved_at": saved_at or datetime.now(timezone.utc).isoformat(),
        "lab_state": copy.deepcopy(dict(state)),
    }


def simulation_preset_authoring_document(
    state: Mapping[str, Any],
    *,
    base: str,
) -> Dict[str, Any]:
    """Return compact, editable JSON that can be submitted to ``simwrite``."""
    components = state.get("components")
    if not isinstance(components, Mapping):
        raise SimulationPresetError("lab_state.components must be an object")
    authored: Dict[str, Any] = {}
    for raw_tag in sorted(components, key=lambda value: str(value)):
        tag_id = str(raw_tag)
        entry = components[raw_tag]
        sc = entry.get("statecontrol") if isinstance(entry, Mapping) else None
        tun = sc.get("tunables") if isinstance(sc, Mapping) else None
        if not isinstance(tun, Mapping):
            raise SimulationPresetError(f"{tag_id}: statecontrol.tunables is required")
        pose = tun.get("nominal_pose")
        if not isinstance(pose, Mapping):
            raise SimulationPresetError(f"{tag_id}: tunables.nominal_pose is required")
        presence = str(tun.get("presence") or PRESENCE_BREADBOARD).strip().lower()
        row: Dict[str, Any] = {
            "presence": presence,
            "pose": {
                "x": _number(pose.get("x"), label=f"{tag_id}.pose.x"),
                "y": _number(pose.get("y"), label=f"{tag_id}.pose.y"),
                "rotation": _number(
                    pose.get("rotation", 0.0), label=f"{tag_id}.pose.rotation"
                ),
            },
        }
        if presence == PRESENCE_STORAGE:
            storage = tun.get("storage")
            slot = storage.get("slot") if isinstance(storage, Mapping) else None
            if not isinstance(slot, Mapping):
                raise SimulationPresetError(f"{tag_id}: stored component requires a slot")
            row["storage"] = {"slot": {"i": int(slot["i"]), "j": int(slot["j"])}}
        authored[tag_id] = row
    return {
        "schema_version": AUTHORING_SCHEMA_VERSION,
        "kind": AUTHORING_KIND,
        "base": str(base),
        "components": authored,
    }


def build_simulation_state_from_authoring(
    document: Mapping[str, Any],
    *,
    base_state: Mapping[str, Any],
    catalog_tag_ids: Optional[Iterable[str]] = None,
    catalog_components: Optional[Mapping[str, Any]] = None,
    layout: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Apply a compact ``simwrite`` document to an explicit base state."""
    if not isinstance(document, Mapping):
        raise SimulationPresetError("simwrite document must be a JSON object")
    allowed_top = {"schema_version", "kind", "base", "components"}
    extra_top = sorted(str(key) for key in document if key not in allowed_top)
    if extra_top:
        raise SimulationPresetError(
            f"simwrite document has unsupported field(s): {', '.join(extra_top)}"
        )
    if document.get("schema_version") != AUTHORING_SCHEMA_VERSION:
        raise SimulationPresetError(
            f"simwrite schema_version must be {AUTHORING_SCHEMA_VERSION}"
        )
    if document.get("kind") != AUTHORING_KIND:
        raise SimulationPresetError(f"simwrite kind must be {AUTHORING_KIND!r}")
    base = str(document.get("base") or "").strip()
    if not base or base.lower() == "list":
        raise SimulationPresetError("simwrite base must be current, default, or a preset name")
    validate_preset_name(base)

    authored = document.get("components")
    if not isinstance(authored, Mapping) or not authored:
        raise SimulationPresetError("simwrite components must be a non-empty object")

    candidate = copy.deepcopy(dict(base_state))
    base_components = candidate.get("components")
    if not isinstance(base_components, dict):
        raise SimulationPresetError("simwrite base state components must be an object")

    allowed_component = {"presence", "pose", "storage"}
    for raw_tag, raw_spec in authored.items():
        tag_id = str(raw_tag or "").strip()
        if not tag_id or not isinstance(raw_spec, Mapping):
            raise SimulationPresetError("every simwrite component must be an object")
        extra = sorted(str(key) for key in raw_spec if key not in allowed_component)
        if extra:
            raise SimulationPresetError(
                f"{tag_id}: unsupported field(s): {', '.join(extra)}"
            )
        presence = str(raw_spec.get("presence") or "").strip().lower()
        if presence not in {PRESENCE_BREADBOARD, PRESENCE_STORAGE, PRESENCE_OFF_TABLE}:
            raise SimulationPresetError(
                f"{tag_id}: presence must be breadboard, storage, or off_table"
            )
        pose = raw_spec.get("pose")
        if not isinstance(pose, Mapping):
            raise SimulationPresetError(f"{tag_id}: pose must be an object")
        pose_extra = sorted(str(key) for key in pose if key not in {"x", "y", "rotation"})
        if pose_extra:
            raise SimulationPresetError(
                f"{tag_id}.pose has unsupported field(s): {', '.join(pose_extra)}"
            )
        for field in ("x", "y", "rotation"):
            if field not in pose:
                raise SimulationPresetError(f"{tag_id}.pose.{field} is required")
        nominal = {
            "x": _number(pose["x"], label=f"{tag_id}.pose.x"),
            "y": _number(pose["y"], label=f"{tag_id}.pose.y"),
            "rotation": _number(pose["rotation"], label=f"{tag_id}.pose.rotation"),
        }

        if tag_id not in base_components:
            catalog_row = (
                catalog_components.get(tag_id)
                if isinstance(catalog_components, Mapping)
                else None
            )
            if not isinstance(catalog_row, Mapping):
                raise SimulationPresetError(
                    f"{tag_id}: component is neither present in base {base!r} "
                    "nor defined in the simulation library"
                )
            base_components[tag_id] = {
                "id": tag_id,
                "type": str(catalog_row.get("type") or "GENERIC_COMPONENT"),
                "statecontrol": {"tunables": {}, "measurables": {}},
                "telemetry": {"teleop": {}, "live_feed": {}},
                "parameters": {},
            }

        entry = base_components[tag_id]
        sc = entry.setdefault("statecontrol", {})
        tun = sc.setdefault("tunables", {})
        tun["presence"] = presence
        tun["nominal_pose"] = nominal
        if presence == PRESENCE_STORAGE:
            storage_spec = raw_spec.get("storage")
            slot = storage_spec.get("slot") if isinstance(storage_spec, Mapping) else None
            if not isinstance(slot, Mapping) or set(slot) != {"i", "j"}:
                raise SimulationPresetError(
                    f"{tag_id}: storage must contain exactly slot.i and slot.j"
                )
            if (
                isinstance(slot["i"], bool)
                or isinstance(slot["j"], bool)
                or not isinstance(slot["i"], int)
                or not isinstance(slot["j"], int)
            ):
                raise SimulationPresetError(
                    f"{tag_id}: storage slot i and j must be integers"
                )
            tun["storage"] = {
                "in_storage": True,
                "slot": {"i": slot["i"], "j": slot["j"]},
            }
            tun["placement"] = {"mode": "STORAGE"}
        else:
            if raw_spec.get("storage") not in (None, {}):
                raise SimulationPresetError(
                    f"{tag_id}: storage is allowed only when presence is storage"
                )
            tun["storage"] = {"in_storage": False, "slot": None}
            tun["placement"] = {"mode": "MANUAL"}

    normalized = normalize_simulation_state(
        candidate,
        catalog_tag_ids=catalog_tag_ids,
        layout=layout,
    )
    _validate_arrangement(
        normalized,
        layout=layout,
        catalog_components=catalog_components,
    )
    return normalized


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise SimulationPresetError(f"simulation preset not found: {path.stem}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise SimulationPresetError(f"could not read simulation preset {path.name}: {exc}") from exc
    if not isinstance(raw, dict):
        raise SimulationPresetError(f"simulation preset {path.name} must be a JSON object")
    return raw


def load_simulation_preset(
    states_dir: str,
    name: str,
    *,
    default_state_path: str,
    catalog_tag_ids: Optional[Iterable[str]] = None,
    layout: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    selector = str(name or "").strip()
    path = (
        Path(default_state_path).resolve()
        if selector.lower() == "default"
        else _preset_path(states_dir, selector)
    )
    raw = _read_json(path)
    state = raw.get("lab_state") if isinstance(raw.get("lab_state"), dict) else raw
    return normalize_simulation_state(
        state,
        catalog_tag_ids=catalog_tag_ids,
        layout=layout,
    )


def save_simulation_preset(
    states_dir: str,
    name: str,
    state: Mapping[str, Any],
    *,
    overwrite: bool = False,
    catalog_tag_ids: Optional[Iterable[str]] = None,
    layout: Optional[Mapping[str, Any]] = None,
) -> Path:
    path = _preset_path(states_dir, name, for_save=True)
    if path.exists() and not overwrite:
        raise FileExistsError(f"simulation preset already exists: {path.stem}")
    normalized = normalize_simulation_state(
        state,
        catalog_tag_ids=catalog_tag_ids,
        layout=layout,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(str(path), workspace_document(normalized))
    return path


def list_simulation_presets(states_dir: str) -> list[Dict[str, Any]]:
    root = Path(states_dir)
    if not root.is_dir():
        return []
    rows: list[Dict[str, Any]] = []
    for path in sorted(root.glob("*.json"), key=lambda item: item.name.lower()):
        try:
            validate_preset_name(path.stem)
            raw = _read_json(path)
            state = raw.get("lab_state") if isinstance(raw.get("lab_state"), dict) else raw
            components = state.get("components") if isinstance(state, dict) else None
            rows.append(
                {
                    "name": path.stem,
                    "saved_at": raw.get("saved_at"),
                    "component_count": len(components) if isinstance(components, dict) else 0,
                    "valid": isinstance(components, dict) and bool(components),
                }
            )
        except SimulationPresetError as exc:
            rows.append(
                {
                    "name": path.stem,
                    "saved_at": None,
                    "component_count": 0,
                    "valid": False,
                    "error": str(exc),
                }
            )
    return rows


__all__ = [
    "AUTHORING_KIND",
    "AUTHORING_SCHEMA_VERSION",
    "SimulationPresetError",
    "WORKSPACE_STATE_KIND",
    "WORKSPACE_STATE_SCHEMA_VERSION",
    "build_simulation_state_from_authoring",
    "list_simulation_presets",
    "load_simulation_preset",
    "normalize_simulation_state",
    "save_simulation_preset",
    "simulation_preset_authoring_document",
    "validate_preset_name",
    "workspace_document",
]
