"""Load edge-owned ``data/library.json`` and ``data/inventory.json``.

Shared by scaffold stubs, mock/sim/deathray HTTP apps, and (optionally) the
coordinator when reading an in-tree edge data folder.

Validation policy (intentional — do **not** silently backfill):
- Missing or malformed library/inventory → raise with a clear path + reason.
- ``recordable: false`` without ``set_at_init`` → hard error.
- Omitted ``recordable`` (except ``nominal_pose``, which defaults true) →
  hard error when ``strict_recordable=True`` (used when capabilities claim
  ``features.runtime_sync``). Otherwise returned as warnings for doctor.
- Never invent tunables / primitives via type inference at load time.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


class LibraryValidationError(ValueError):
    """Hard-fail library / tunable metadata problems (no silent repair)."""


def edge_data_dir(edge_root: Path) -> Path:
    return edge_root.resolve() / "data"


def load_json(path: Path) -> Dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top-level must be an object")
    return data


def validate_tunable_record_metadata(
    field_id: str,
    desc: Any,
    *,
    scope: str,
    strict_recordable: bool = False,
) -> Tuple[List[str], List[str]]:
    """Return ``(errors, warnings)`` for one tunable descriptor.

    Does not mutate ``desc``. Callers must fix the authored JSON.
    """
    errors: List[str] = []
    warnings: List[str] = []
    if not isinstance(desc, dict):
        errors.append(f"{scope}.{field_id}: tunable descriptor must be an object")
        return errors, warnings
    if "widget" not in desc or not isinstance(desc.get("widget"), str) or not desc["widget"]:
        errors.append(f"{scope}.{field_id}: missing required 'widget' string")

    if desc.get("recordable") is True:
        return errors, warnings
    if desc.get("recordable") is False:
        if "set_at_init" not in desc:
            errors.append(
                f"{scope}.{field_id}: recordable:false requires set_at_init"
            )
        return errors, warnings

    # recordable omitted
    if field_id == "nominal_pose":
        return errors, warnings
    msg = (
        f"{scope}.{field_id}: declare recordable:true or "
        f"recordable:false with set_at_init (SYNC_RUNTIME)"
    )
    if strict_recordable:
        errors.append(msg)
    else:
        warnings.append(msg)
    return errors, warnings


def validate_library_document(
    library: Dict[str, Any],
    *,
    source: str = "<library>",
    strict_recordable: bool = False,
) -> List[str]:
    """Validate library shape + tunable RECORD/SYNC metadata.

    Returns soft warnings. Raises :class:`LibraryValidationError` on hard errors.
    Does **not** backfill missing ``capabilities`` or tunables.
    """
    errors: List[str] = []
    warnings: List[str] = []

    if int(library.get("schema_version") or 0) != 1:
        errors.append(f"{source}: schema_version must be 1")
    comps = library.get("components")
    if not isinstance(comps, dict) or not comps:
        errors.append(f"{source}: components must be a non-empty object")
        raise LibraryValidationError("\n  - ".join(["Library validation failed:", *errors]))

    for key, entry in comps.items():
        prefix = f"{source}.components[{key!r}]"
        if not isinstance(entry, dict):
            errors.append(f"{prefix}: must be an object")
            continue
        for required in ("id", "type", "tag_id", "capabilities"):
            if required not in entry:
                errors.append(f"{prefix}: missing required field {required!r}")
        tag_id = entry.get("tag_id")
        if isinstance(tag_id, str) and tag_id and tag_id != key:
            errors.append(
                f"{prefix}: tag_id ({tag_id!r}) does not match key ({key!r})"
            )
        caps = entry.get("capabilities")
        if not isinstance(caps, dict):
            errors.append(f"{prefix}: missing 'capabilities' object")
            continue
        prims = caps.get("primitives")
        if not isinstance(prims, list) or not prims:
            errors.append(f"{prefix}.capabilities: 'primitives' must be a non-empty array")
        sc = caps.get("statecontrol") or {}
        if sc is not None and not isinstance(sc, dict):
            errors.append(f"{prefix}.capabilities.statecontrol: must be an object")
            continue
        tunables = (sc or {}).get("tunables") or {}
        if not isinstance(tunables, dict):
            errors.append(f"{prefix}.capabilities.statecontrol.tunables: must be an object")
            continue
        for field_id, desc in tunables.items():
            if field_id == "reported_pose":
                warnings.append(
                    f"{prefix}.capabilities.statecontrol.tunables.reported_pose: "
                    "deprecated — RECORD_TUNABLES writes nominal_pose; remove it"
                )
            errs, warns = validate_tunable_record_metadata(
                str(field_id),
                desc,
                scope=f"{prefix}.capabilities.statecontrol.tunables",
                strict_recordable=strict_recordable,
            )
            errors.extend(errs)
            warnings.extend(warns)

    if errors:
        raise LibraryValidationError(
            "\n  - ".join(["Library validation failed:", *errors])
        )
    return warnings


def load_library(
    edge_root: Path,
    *,
    strict_recordable: bool = False,
    validate: bool = True,
) -> Dict[str, Any]:
    path = edge_data_dir(edge_root) / "library.json"
    if not path.is_file():
        raise FileNotFoundError(f"edge library not found: {path}")
    doc = load_json(path)
    if validate:
        validate_library_document(
            doc,
            source=str(path),
            strict_recordable=strict_recordable,
        )
    else:
        if int(doc.get("schema_version") or 0) != 1:
            raise ValueError(f"{path}: schema_version must be 1")
        comps = doc.get("components")
        if not isinstance(comps, dict) or not comps:
            raise ValueError(f"{path}: components must be a non-empty object")
    return doc


def load_inventory(edge_root: Path) -> Dict[str, Any]:
    path = edge_data_dir(edge_root) / "inventory.json"
    if not path.is_file():
        raise FileNotFoundError(f"edge inventory not found: {path}")
    doc = load_json(path)
    if int(doc.get("schema_version") or 0) != 1:
        raise ValueError(f"{path}: schema_version must be 1")
    entries = doc.get("entries")
    if not isinstance(entries, dict):
        raise ValueError(f"{path}: entries must be an object")
    return doc


def library_tag_ids(library: Dict[str, Any]) -> List[str]:
    comps = library.get("components") or {}
    return sorted(str(k) for k in comps.keys() if isinstance(k, str) and k.strip())


def inventory_tag_ids(inventory: Dict[str, Any]) -> List[str]:
    entries = inventory.get("entries") or {}
    return [str(k) for k in entries.keys() if isinstance(k, str) and k.strip()]


def validate_inventory_against_library(
    library: Dict[str, Any],
    inventory: Dict[str, Any],
) -> None:
    lib_keys = set(library_tag_ids(library))
    missing = [t for t in inventory_tag_ids(inventory) if t not in lib_keys]
    if missing:
        raise ValueError(
            "inventory tags missing from library: " + ", ".join(sorted(missing))
        )


def default_localize_tag_ids(inventory: Dict[str, Any]) -> List[str]:
    """Tags LOCALIZE_COMPONENTS / SYNC_RUNTIME should scan when args.tag_ids is omitted."""
    out: List[str] = []
    entries = inventory.get("entries") or {}
    for tag_id, entry in entries.items():
        if not isinstance(tag_id, str) or not tag_id.strip():
            continue
        if not isinstance(entry, dict):
            continue
        if entry.get("localize") is False:
            continue
        placement = str(entry.get("placement") or "")
        if placement in ("table", "storage"):
            out.append(tag_id.strip())
    return out


def library_rows(library: Dict[str, Any]) -> List[Dict[str, Any]]:
    comps = library.get("components") or {}
    rows: List[Dict[str, Any]] = []
    for key, entry in comps.items():
        if not isinstance(entry, dict):
            continue
        row = dict(entry)
        row.setdefault("tag_id", key)
        rows.append(row)
    return rows


def merged_active_rows(
    library: Dict[str, Any],
    inventory: Dict[str, Any],
) -> List[Dict[str, Any]]:
    active: Set[str] = set(inventory_tag_ids(inventory))
    return [r for r in library_rows(library) if str(r.get("tag_id") or "") in active]


def stamp_backend(doc: Dict[str, Any], backend_id: str) -> Dict[str, Any]:
    out = dict(doc)
    out["backend_id"] = backend_id
    return out


def resolve_edge_root_beside_lab_view(lab_view_root: Path) -> Optional[Path]:
    """``mock_backend/lab_view`` → ``mock_backend/cloudlabs_edge`` when that tree exists."""
    candidate = lab_view_root.resolve().parent / "cloudlabs_edge"
    if (candidate / "data" / "library.json").is_file():
        return candidate
    return None


def capabilities_wants_runtime_sync(capabilities: Dict[str, Any]) -> bool:
    features = capabilities.get("features") or {}
    if isinstance(features, dict) and features.get("runtime_sync") is True:
        return True
    prims = set(capabilities.get("supported_primitives") or [])
    return "SYNC_RUNTIME" in prims
