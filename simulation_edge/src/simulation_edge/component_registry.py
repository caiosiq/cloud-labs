"""Simulation-only component definitions layered over the built-in edge library.

The built-in ``data/library.json`` remains immutable.  Agent-authored generic
components and parameter overrides are stored separately in
``data/simulation_components.json`` and merged only when the simulation edge
serves its library or rebuilds MuJoCo.
"""

from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = 1
REGISTRY_FILENAME = "simulation_components.json"
TAG_RE = re.compile(r"^tag_([1-9][0-9]*)$")
TYPE_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
ALLOWED_FIELDS = frozenset({"name", "type", "parameters"})
MAX_DOCUMENT_BYTES = 64 * 1024


class SimulationComponentError(ValueError):
    """Invalid or conflicting simulation component definition."""


def _read_object(path: Path, default: Mapping[str, Any]) -> dict[str, Any]:
    if not path.is_file():
        return copy.deepcopy(dict(default))
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SimulationComponentError(f"Could not read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SimulationComponentError(f"{path}: top-level JSON must be an object")
    return value


def _atomic_write(path: Path, value: Mapping[str, Any]) -> None:
    encoded = json.dumps(value, indent=2, ensure_ascii=False) + "\n"
    if len(encoded.encode("utf-8")) > MAX_DOCUMENT_BYTES:
        raise SimulationComponentError(
            f"simulation component registry exceeds {MAX_DOCUMENT_BYTES} bytes"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(encoded, encoding="utf-8")
    os.replace(temporary, path)


def registry_path(edge_root: Path) -> Path:
    return edge_root.resolve() / "data" / REGISTRY_FILENAME


def empty_registry() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "definitions": {}, "overrides": {}}


def load_registry(edge_root: Path) -> dict[str, Any]:
    path = registry_path(edge_root)
    doc = _read_object(path, empty_registry())
    if int(doc.get("schema_version") or 0) != SCHEMA_VERSION:
        raise SimulationComponentError(
            f"{path}: schema_version must be {SCHEMA_VERSION}"
        )
    definitions = doc.get("definitions")
    overrides = doc.get("overrides")
    if not isinstance(definitions, dict) or not isinstance(overrides, dict):
        raise SimulationComponentError(
            f"{path}: definitions and overrides must be objects"
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "definitions": copy.deepcopy(definitions),
        "overrides": copy.deepcopy(overrides),
    }


def save_registry(edge_root: Path, document: Mapping[str, Any]) -> Path:
    path = registry_path(edge_root)
    _atomic_write(path, document)
    return path


def load_base_library(edge_root: Path) -> dict[str, Any]:
    path = edge_root.resolve() / "data" / "library.json"
    doc = _read_object(path, {})
    if int(doc.get("schema_version") or 0) != 1:
        raise SimulationComponentError(f"{path}: schema_version must be 1")
    components = doc.get("components")
    if not isinstance(components, dict) or not components:
        raise SimulationComponentError(f"{path}: components must be a non-empty object")
    return {"schema_version": 1, "components": copy.deepcopy(components)}


def _merge_patch(base: Mapping[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(dict(base))
    for key, value in patch.items():
        if key == "parameters" and isinstance(value, Mapping):
            parameters = result.get("parameters")
            merged = copy.deepcopy(dict(parameters)) if isinstance(parameters, Mapping) else {}
            for parameter, parameter_value in value.items():
                if parameter_value is None:
                    merged.pop(str(parameter), None)
                else:
                    merged[str(parameter)] = copy.deepcopy(parameter_value)
            result["parameters"] = merged
        else:
            result[key] = copy.deepcopy(value)
    return result


def merged_library(
    edge_root: Path,
    *,
    registry: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    base = load_base_library(edge_root)
    custom = copy.deepcopy(dict(registry or load_registry(edge_root)))
    components = copy.deepcopy(base["components"])
    definitions = custom.get("definitions") or {}
    overrides = custom.get("overrides") or {}
    if not isinstance(definitions, Mapping) or not isinstance(overrides, Mapping):
        raise SimulationComponentError("definitions and overrides must be objects")
    for tag_id, row in definitions.items():
        if tag_id in components:
            raise SimulationComponentError(
                f"custom definition {tag_id!r} conflicts with a built-in tag"
            )
        if isinstance(row, Mapping):
            components[str(tag_id)] = copy.deepcopy(dict(row))
    for tag_id, patch in overrides.items():
        if tag_id not in components:
            raise SimulationComponentError(
                f"override {tag_id!r} does not match a known component"
            )
        if isinstance(patch, Mapping):
            components[str(tag_id)] = _merge_patch(components[str(tag_id)], patch)
    return {"schema_version": 1, "components": components}


def validate_tag_id(tag_id: str) -> str:
    value = str(tag_id or "").strip()
    if not TAG_RE.fullmatch(value):
        raise SimulationComponentError("tag_id must be tag_<positive integer>, for example tag_23")
    return value


def tag_number(tag_id: str) -> int:
    """Return the numeric identity from a validated canonical tag."""

    value = validate_tag_id(tag_id)
    return int(value.removeprefix("tag_"))


def next_tag_id(tag_ids: Any) -> str:
    """Return one greater than the highest existing numeric tag."""

    highest = 0
    for raw_tag in tag_ids:
        highest = max(highest, tag_number(str(raw_tag)))
    return f"tag_{highest + 1}"


def validate_patch(value: Mapping[str, Any], *, require_identity: bool) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise SimulationComponentError("component definition must be a JSON object")
    extra = sorted(str(key) for key in value if key not in ALLOWED_FIELDS)
    if extra:
        raise SimulationComponentError(
            "unsupported component field(s): " + ", ".join(extra)
        )
    patch = copy.deepcopy(dict(value))
    if require_identity:
        if not str(patch.get("name") or "").strip():
            raise SimulationComponentError("component name is required")
        if not str(patch.get("type") or "").strip():
            patch["type"] = "GENERIC_COMPONENT"
    if "name" in patch:
        name = str(patch["name"] or "").strip()
        if not name or len(name) > 120:
            raise SimulationComponentError("name must contain 1-120 characters")
        patch["name"] = name
    if "type" in patch:
        component_type = str(patch["type"] or "").strip().upper()
        if not TYPE_RE.fullmatch(component_type):
            raise SimulationComponentError(
                "type must be an uppercase token using letters, numbers and '_'"
            )
        patch["type"] = component_type
    if "parameters" in patch:
        if not isinstance(patch["parameters"], Mapping):
            raise SimulationComponentError("parameters must be a JSON object")
        patch["parameters"] = copy.deepcopy(dict(patch["parameters"]))
    try:
        encoded = json.dumps(patch, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise SimulationComponentError(
            f"component definition contains a non-JSON value: {exc}"
        ) from exc
    if len(encoded.encode("utf-8")) > 16 * 1024:
        raise SimulationComponentError("one component definition may not exceed 16 KiB")
    return patch


def _generic_capabilities() -> dict[str, Any]:
    return {
        "statecontrol": {
            "tunables": {
                "nominal_pose": {"widget": "TablePose", "recordable": True}
            },
            "measurables": {},
        },
        "telemetry": {"teleop": {}, "live_feed": {}},
        "primitives": [
            "MOVE_COMPONENT",
            "STORE_COMPONENT",
            "PLACE_FROM_STORAGE",
            "PICK_COMPONENT",
            "HOVER",
            "PLACE_FROM_HOVER",
            "RECORD_MEASURABLES",
        ],
    }


def build_custom_row(tag_id: str, definition: Mapping[str, Any]) -> dict[str, Any]:
    tag = validate_tag_id(tag_id)
    normalized = validate_patch(definition, require_identity=True)
    component_type = str(normalized.get("type") or "GENERIC_COMPONENT")
    return {
        "id": tag.removeprefix("tag_") or tag,
        "tag_id": tag,
        "name": normalized["name"],
        "type": component_type,
        "size": {"width": 72.0, "height": 72.0},
        "height_mm": 60.0,
        "parameters": normalized.get("parameters") or {},
        "simulation": {
            "model": "generic_black_box",
            "definition_source": "simulation_components",
        },
        "capabilities": _generic_capabilities(),
    }


def define_component(
    edge_root: Path,
    tag_id: str,
    definition: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    tag = validate_tag_id(tag_id)
    base = load_base_library(edge_root)["components"]
    registry = load_registry(edge_root)
    if tag in base or tag in registry["definitions"]:
        raise SimulationComponentError(
            f"component {tag!r} already exists; use configure instead"
        )
    row = build_custom_row(tag, definition)
    registry["definitions"][tag] = row
    return registry, row


def configure_component(
    edge_root: Path,
    tag_id: str,
    patch_value: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    tag = validate_tag_id(tag_id)
    patch = validate_patch(patch_value, require_identity=False)
    if not patch:
        raise SimulationComponentError("configure patch must not be empty")
    base = load_base_library(edge_root)["components"]
    registry = load_registry(edge_root)
    if tag in registry["definitions"]:
        current = registry["definitions"][tag]
        updated = _merge_patch(current, patch)
        # Preserve generated identity/capabilities and validate the editable view.
        editable = {key: updated.get(key) for key in ALLOWED_FIELDS if key in updated}
        row = build_custom_row(tag, editable)
        registry["definitions"][tag] = row
        return registry, row
    if tag in base:
        current = registry["overrides"].get(tag) or {}
        registry["overrides"][tag] = _merge_patch(current, patch)
        row = merged_library(edge_root, registry=registry)["components"][tag]
        return registry, row
    raise SimulationComponentError(f"unknown component tag: {tag}")


def reset_component_override(
    edge_root: Path,
    tag_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    tag = validate_tag_id(tag_id)
    base = load_base_library(edge_root)["components"]
    if tag not in base:
        raise SimulationComponentError(
            f"{tag} is not built-in; use delete for a custom definition"
        )
    registry = load_registry(edge_root)
    registry["overrides"].pop(tag, None)
    return registry, copy.deepcopy(base[tag])


def delete_custom_component(edge_root: Path, tag_id: str) -> dict[str, Any]:
    tag = validate_tag_id(tag_id)
    registry = load_registry(edge_root)
    if tag not in registry["definitions"]:
        if tag in load_base_library(edge_root)["components"]:
            raise SimulationComponentError(
                f"{tag} is built-in and cannot be deleted; use reset"
            )
        raise SimulationComponentError(f"unknown custom component tag: {tag}")
    registry["definitions"].pop(tag)
    return registry


def component_origin(edge_root: Path, tag_id: str) -> str:
    registry = load_registry(edge_root)
    if tag_id in registry["definitions"]:
        return "custom"
    if tag_id in registry["overrides"]:
        return "built_in_override"
    return "built_in"


__all__ = [
    "SimulationComponentError",
    "build_custom_row",
    "component_origin",
    "configure_component",
    "define_component",
    "delete_custom_component",
    "empty_registry",
    "load_base_library",
    "load_registry",
    "merged_library",
    "next_tag_id",
    "reset_component_override",
    "save_registry",
    "tag_number",
    "validate_tag_id",
]
