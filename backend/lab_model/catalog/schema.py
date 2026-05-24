"""Capability Contract schema (v1) — loader, validator, and capability presets.

This module is the cloud-labs side of the **Capability Contract** described
in ``universal_component_architecture.md`` §13–§15. It owns:

- :data:`SUPPORTED_SCHEMA_VERSIONS` — the set of catalog versions this build
  accepts.
- :data:`KNOWN_WIDGETS_*` — the closed widget vocabulary (§14).
- :func:`infer_default_capabilities` — type-based presets used by the
  migration script to bootstrap a ``capabilities`` block from a legacy row.
- :func:`validate_catalog_v1` — §15.1 hard-fail validation.
- :func:`load_component_library_rows` — dual-shape loader that accepts both
  the legacy top-level **array** and the new ``{schema_version, components}``
  **object** shape; returns a normalized list of row dicts so existing
  callers (``library_by_tag``, ``merged_catalog_rows``) are unchanged.

Cross-repo invariants (per §16.4 / Reading B): cloud-labs is the only writer
of catalog files; ``lab_automation`` *reads* them via the same JSON, never
imports this module. Keep validation here aligned with the hardware-side
mirror in ``CLOUDLAB_CONTRACT.md``.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, FrozenSet, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# Schema version + widget vocabulary (§14)
# ---------------------------------------------------------------------------

#: Catalog ``schema_version`` values this build can load.
SUPPORTED_SCHEMA_VERSIONS: FrozenSet[int] = frozenset({1})

#: §14.1 tunable widgets.
KNOWN_WIDGETS_TUNABLE: FrozenSet[str] = frozenset({
    "TablePose",
    "NudgeMotorGroup",
    "FloatRange",
    "IntRange",
    "Boolean",
    "StringDropdown",
    "StorageSlot",
    # Opaque JSON tunables (e.g. nominal_motor_positions map).
    "JsonInspector",
    "TeleopRz",
    "TeleopPose3d",
    "LivePosePoll",
})

#: §14.2 measurable widgets.
KNOWN_WIDGETS_MEASURABLE: FrozenSet[str] = frozenset({
    "PoseReadout",
    "MotorRotationsReadout",
    "ImageViewer",
    "NumberBadge",
    "Timestamp",
})

#: §14.3 telemetry widgets.
KNOWN_WIDGETS_TELEMETRY: FrozenSet[str] = frozenset({
    "MJPEGViewer",
    "JPEGPoll",
    "LiveCoordinatesReadout",
    "LivePosePoll",
})

#: §14.4 universal soft-fallback widget. Used implicitly for unknown names;
#: may also be declared explicitly for opaque JSON fields (motor map, …).
SOFT_FALLBACK_WIDGET: str = "JsonInspector"

#: Widgets that hard-require additional config keys (§15.1 last bullet).
#: ``{widget_name: (required_keys, scope)}``.
WIDGET_REQUIRED_KEYS: Dict[str, Tuple[Tuple[str, ...], str]] = {
    "FloatRange": (("min", "max"), "tunable"),
    "IntRange": (("min", "max"), "tunable"),
    "StringDropdown": (("options",), "tunable"),
    "NudgeMotorGroup": (("motor_ids",), "tunable"),
    "MJPEGViewer": (("url",), "telemetry"),
    "JPEGPoll": (("url",), "telemetry"),
    "LiveCoordinatesReadout": (("url",), "telemetry"),
    "LivePosePoll": (("url",), "telemetry"),
}


# ---------------------------------------------------------------------------
# Primitive set (loaded lazily to avoid an unconditional ``lab_model.primitives``
# import at module import time — keeps this file usable from the migration
# script which is run from the repo root before the package is on path).
# ---------------------------------------------------------------------------

def _known_primitive_ids() -> Set[str]:
    """Return the set of known ``PrimitiveId`` string values.

    Lazy import so this module can be imported by ``scripts/`` before the
    ``backend`` package is on ``sys.path``; the migration script wires the
    path itself before calling validators.
    """
    try:
        from lab_model.primitives.ids import PrimitiveId  # noqa: PLC0415
    except Exception:  # pragma: no cover -- only reached when path is unset
        return set()
    return {p.value for p in PrimitiveId}


# ---------------------------------------------------------------------------
# Capability presets (used by the migration script)
# ---------------------------------------------------------------------------

# Tag-id substitution token used in telemetry URLs (§16.6).
_TAG_TOKEN = "{tag_id}"

# Primitive bundles per component class.
_MOTOR_TUNABLE_PRIMITIVES = (
    "MOVE_MOTOR",
    "SET_MOTOR_SETPOINT",
    "MOTOR_SEND_HOME",
    "MOTOR_SET_ZERO",
)

_MOTOR_WORKFLOW_PRIMITIVES = (
    "OPTIMIZE",
    "SCAN_ROTATE_IN_PLACE",
)

_CAMERA_TUNABLE_PRIMITIVES = (
    "SET_EXPOSURE",
)

_PLACEMENT_PRIMITIVES = (
    "MOVE_COMPONENT",
)

_MANIPULATION_PRIMITIVES = (
    "STORE_COMPONENT",
    "PLACE_FROM_STORAGE",
    "PICK_COMPONENT",
    "HOVER",
    "PLACE_FROM_HOVER",
)

_TELEMETRY_SESSION_PRIMITIVES = (
    "START_TELEOP",
    "END_TELEOP",
    "TELEOP_GOTO",
)

_UNIVERSAL_PRIMITIVES = (
    "RECORD_MEASURABLES",
)

_LIVE_FEED_PRIMITIVES = (
    "START_LIVE_FEED",
    "END_LIVE_FEED",
)


def _migrate_legacy_pose_channel(teleop: Dict[str, Any]) -> Dict[str, Any]:
    """Split legacy ``pose`` / flat TeleopJog into ``rz`` + ``pose3d`` channels."""
    legacy = teleop.pop("pose", None)
    if not isinstance(legacy, dict):
        return teleop
    step_deg = legacy.get("step_deg") or [0.5, 2.0, 10.0]
    step_mm = legacy.get("step_mm") or [0.5, 2.0, 10.0]
    teleop.setdefault(
        "rz",
        {"widget": "TeleopRz", "step_deg": list(step_deg)},
    )
    teleop.setdefault(
        "pose3d",
        {
            "widget": "TeleopPose3d",
            "step_mm": list(step_mm),
            "step_deg": list(step_deg),
            "step_z_mm": legacy.get("step_z_mm") or [1.0, 5.0, 20.0],
        },
    )
    return teleop


def _normalize_telemetry_teleop(teleop: Any) -> Dict[str, Any]:
    """Ensure teleop block is channel-keyed (e.g. ``rz: {widget: ...}``)."""
    if not isinstance(teleop, dict) or not teleop:
        return {}
    # Flat legacy descriptor migrated from ``tunables.teleop``.
    if isinstance(teleop.get("widget"), str):
        teleop = {"pose": dict(teleop)}
    # Already channel-keyed: each entry is a widget descriptor object.
    if all(isinstance(v, dict) and "widget" in v for v in teleop.values()):
        out = dict(teleop)
        if "pose" in out and ("rz" not in out or "pose3d" not in out):
            out = _migrate_legacy_pose_channel(out)
        return out
    return dict(teleop)


def normalize_capabilities(caps: Any) -> Dict[str, Any]:
    """Normalize legacy flat capabilities to ``statecontrol`` + ``telemetry``."""
    if not isinstance(caps, dict):
        return {
            "statecontrol": {"tunables": {}, "measurables": {}},
            "telemetry": {"teleop": {}, "live_feed": {}},
            "primitives": [],
        }
    if "statecontrol" in caps:
        out = dict(caps)
        tel = dict(out.get("telemetry") or {})
        tel["teleop"] = _normalize_telemetry_teleop(tel.get("teleop"))
        tel.setdefault("live_feed", {})
        out["telemetry"] = tel
        sc = dict(out.get("statecontrol") or {})
        sc.setdefault("tunables", {})
        sc.setdefault("measurables", {})
        out.setdefault("primitives", [])
        return out

    legacy_tun = dict(caps.get("tunables") or {})
    teleop_decl = legacy_tun.pop("teleop", None)
    legacy_telemetry = dict(caps.get("telemetry") or {})
    return {
        "statecontrol": {
            "tunables": legacy_tun,
            "measurables": dict(caps.get("measurables") or {}),
        },
        "telemetry": {
            "teleop": _normalize_telemetry_teleop(teleop_decl),
            "live_feed": legacy_telemetry,
        },
        "primitives": list(caps.get("primitives") or []),
    }


def measurables_decl(catalog_row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Declared measurable fields for a catalog row (normalized shape)."""
    caps = normalize_capabilities((catalog_row or {}).get("capabilities"))
    sc = caps.get("statecontrol") or {}
    decl = sc.get("measurables") if isinstance(sc, dict) else {}
    return dict(decl) if isinstance(decl, dict) else {}


def infer_default_capabilities(row: Dict[str, Any]) -> Dict[str, Any]:
    """Build a ``capabilities`` block from a legacy catalog row.

    Used by the migration script and by the loader when an entry in the
    new-shape catalog is missing the block entirely (which the validator
    will then re-flag if it would still fail §15.1, but allows opportunistic
    backfill for hand-edited catalogs).

    Rules of inference (kept deliberately simple — the migration script is
    the right place for hand-edits, not this function):

    - Every component gets ``MOVE_COMPONENT`` + storage/pick/hover/place +
      ``RECORD_MEASURABLES`` in ``primitives``.
    - If ``motor_ids`` is set, append motor primitives + add
      ``nominal_motor_positions`` tunable and ``motor_rotations`` +
      ``last_optimization_score`` measurables.
    - ``OPTICAL_CAMERA`` adds ``exposure_time_ms`` tunable, ``camera_image``
      measurable, and the ``stream`` telemetry channel.
    """
    comp_type = str(row.get("type") or "")
    motor_ids = row.get("motor_ids") or []
    tag_id = str(row.get("tag_id") or "")

    tunables: Dict[str, Any] = {
        "nominal_pose": {"widget": "TablePose"},
    }
    teleop: Dict[str, Any] = {
        "rz": {
            "widget": "TeleopRz",
            "step_deg": [0.5, 2.0, 10.0],
        },
        "pose3d": {
            "widget": "TeleopPose3d",
            "step_mm": [0.5, 2.0, 10.0],
            "step_deg": [0.5, 2.0, 10.0],
            "step_z_mm": [1.0, 5.0, 20.0],
        },
        "live_pose": {
            "widget": "LivePosePoll",
            "url": f"/api/components/{_TAG_TOKEN}/telemetry/live-pose",
            "default_fps": 20,
        },
    }
    measurables: Dict[str, Any] = {}
    live_feed: Dict[str, Any] = {}
    primitives: List[str] = ["MOVE_COMPONENT"]

    if isinstance(motor_ids, list) and motor_ids:
        tunables["nominal_motor_positions"] = {
            "widget": "JsonInspector",
        }
        measurables["motor_rotations"] = {"widget": "MotorRotationsReadout"}
        measurables["last_optimization_score"] = {"widget": "NumberBadge", "format": ".3f"}
        primitives.extend(list(_MOTOR_TUNABLE_PRIMITIVES))

    if comp_type == "OPTICAL_CAMERA":
        tunables["exposure_time_ms"] = {
            "widget": "FloatRange",
            "min": 10.0,
            "max": 1000.0,
            "default": 200.0,
            "unit": "ms",
        }
        primitives.extend(list(_CAMERA_TUNABLE_PRIMITIVES))
        measurables["camera_image"] = {"widget": "ImageViewer", "format": "png"}
        # Phase 6 owns the route; URLs are direct per §16.6.
        if tag_id:
            url_stream = f"/api/components/{_TAG_TOKEN}/telemetry/stream"
            live_feed["stream"] = {"widget": "MJPEGViewer", "url": url_stream}

    primitives.extend(list(_MANIPULATION_PRIMITIVES))
    primitives.extend(list(_UNIVERSAL_PRIMITIVES))

    if isinstance(motor_ids, list) and motor_ids:
        primitives.extend(list(_MOTOR_WORKFLOW_PRIMITIVES))

    for p in _TELEMETRY_SESSION_PRIMITIVES:
        if p not in primitives:
            primitives.append(p)
    if comp_type == "OPTICAL_CAMERA":
        for p in _LIVE_FEED_PRIMITIVES:
            if p not in primitives:
                primitives.append(p)

    return {
        "statecontrol": {"tunables": tunables, "measurables": measurables},
        "telemetry": {"teleop": teleop, "live_feed": live_feed},
        "primitives": primitives,
    }


# ---------------------------------------------------------------------------
# Validation (§15.1)
# ---------------------------------------------------------------------------

class CatalogValidationError(ValueError):
    """Raised on §15.1 hard-fail validation failures."""


def _check_widget(
    field_name: str,
    descriptor: Any,
    *,
    known: FrozenSet[str],
    scope: str,
    errors: List[str],
) -> None:
    """Validate a single tunable/measurable/telemetry descriptor."""
    if not isinstance(descriptor, dict):
        errors.append(f"{scope}.{field_name}: descriptor must be an object, got {type(descriptor).__name__}")
        return
    widget = descriptor.get("widget")
    if not isinstance(widget, str) or not widget:
        errors.append(f"{scope}.{field_name}: missing 'widget' string")
        return
    # Unknown widget is a soft fallback (§15.2) -- loader logs warning.
    if widget in WIDGET_REQUIRED_KEYS:
        required, expected_scope = WIDGET_REQUIRED_KEYS[widget]
        for key in required:
            if descriptor.get(key) is None:
                errors.append(
                    f"{scope}.{field_name}: widget {widget!r} requires {key!r}"
                )
        # Soft check: widget vs declared scope match. Don't fail; just note.
        _ = expected_scope  # reserved for future cross-scope warning
    # Track unknown widgets via a separate list (warnings, not errors).
    if widget not in known:
        errors.append(f"__WARN__{scope}.{field_name}: unknown widget {widget!r} (will render as JsonInspector)")


def validate_catalog_v1(
    catalog: Dict[str, Any],
    *,
    source: str = "<catalog>",
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Validate a v1 catalog object and return ``(rows, warnings)``.

    Hard-fails per §15.1: schema version, structural integrity, unknown
    primitives, required widget config keys. Soft warnings per §15.2:
    unknown widget names.

    The returned list of rows is in the same shape legacy callers expect —
    a list of per-component dicts, each carrying ``tag_id``.
    """
    if not isinstance(catalog, dict):
        raise CatalogValidationError(f"{source}: top-level must be an object (got {type(catalog).__name__})")
    version = catalog.get("schema_version")
    if not isinstance(version, int):
        raise CatalogValidationError(f"{source}: 'schema_version' must be an integer")
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise CatalogValidationError(
            f"{source}: schema_version={version} not in supported set "
            f"{sorted(SUPPORTED_SCHEMA_VERSIONS)}"
        )

    components = catalog.get("components")
    if not isinstance(components, dict):
        raise CatalogValidationError(f"{source}: 'components' must be an object keyed by tag_id")

    known_prims = _known_primitive_ids()
    errors: List[str] = []
    warnings: List[str] = []
    rows: List[Dict[str, Any]] = []

    for key, entry in components.items():
        prefix = f"{source}:components[{key!r}]"
        if not isinstance(entry, dict):
            errors.append(f"{prefix}: entry must be an object")
            continue

        for required in ("id", "type", "tag_id"):
            v = entry.get(required)
            if not isinstance(v, str) or not v:
                errors.append(f"{prefix}: missing required field {required!r}")
        # tag_id key vs nested tag_id must agree.
        if entry.get("tag_id") and entry.get("tag_id") != key:
            errors.append(f"{prefix}: tag_id ({entry.get('tag_id')!r}) does not match key ({key!r})")

        caps = entry.get("capabilities")
        row = dict(entry)
        if not isinstance(caps, dict):
            errors.append(f"{prefix}: missing 'capabilities' object")
        else:
            prims = caps.get("primitives")
            if not isinstance(prims, list) or not prims:
                errors.append(f"{prefix}.capabilities: 'primitives' must be a non-empty array")
            else:
                for p in prims:
                    if not isinstance(p, str):
                        errors.append(f"{prefix}.capabilities.primitives: non-string entry {p!r}")
                    elif known_prims and p not in known_prims:
                        errors.append(f"{prefix}.capabilities.primitives: unknown primitive {p!r}")

            caps_norm = normalize_capabilities(caps)
            for scope, known in (
                ("tunables", KNOWN_WIDGETS_TUNABLE),
                ("measurables", KNOWN_WIDGETS_MEASURABLE),
            ):
                block = (caps_norm.get("statecontrol") or {}).get(scope) or {}
                if not isinstance(block, dict):
                    errors.append(f"{prefix}.capabilities.statecontrol.{scope}: must be an object")
                    continue
                for fname, desc in block.items():
                    _check_widget(
                        fname, desc, known=known,
                        scope=f"{prefix}.capabilities.statecontrol.{scope}", errors=errors,
                    )
            tel = caps_norm.get("telemetry") or {}
            for scope, known in (
                ("teleop", KNOWN_WIDGETS_TUNABLE),
                ("live_feed", KNOWN_WIDGETS_TELEMETRY),
            ):
                block = tel.get(scope) or {}
                if not isinstance(block, dict):
                    errors.append(f"{prefix}.capabilities.telemetry.{scope}: must be an object")
                    continue
                for fname, desc in block.items():
                    _check_widget(
                        fname, desc, known=known,
                        scope=f"{prefix}.capabilities.telemetry.{scope}", errors=errors,
                    )
            row["capabilities"] = caps_norm
        if "tag_id" not in row:
            row["tag_id"] = key
        rows.append(row)

    # Partition warnings (lines starting with __WARN__) from hard errors.
    hard: List[str] = []
    for e in errors:
        if e.startswith("__WARN__"):
            warnings.append(e[len("__WARN__"):])
        else:
            hard.append(e)
    if hard:
        msg = "\n  - ".join([f"Catalog validation failed ({source}):", *hard])
        raise CatalogValidationError(msg)
    return rows, warnings


# ---------------------------------------------------------------------------
# Loader (dual-shape: legacy array + v1 object)
# ---------------------------------------------------------------------------

def is_legacy_array_shape(data: Any) -> bool:
    """True iff ``data`` is the legacy top-level array of component rows."""
    return isinstance(data, list)


def is_v1_object_shape(data: Any) -> bool:
    """True iff ``data`` looks like the v1 ``{schema_version, components}`` object."""
    return (
        isinstance(data, dict)
        and "schema_version" in data
        and "components" in data
    )


def live_feed_channel(
    catalog_row: Optional[Dict[str, Any]],
    channel: str,
) -> Optional[Dict[str, Any]]:
    """Return ``capabilities.telemetry.live_feed[channel]`` descriptor or ``None``."""
    if not isinstance(catalog_row, dict):
        return None
    caps = normalize_capabilities(catalog_row.get("capabilities") or {})
    lf = (caps.get("telemetry") or {}).get("live_feed") or {}
    if not isinstance(lf, dict):
        return None
    desc = lf.get(channel)
    return desc if isinstance(desc, dict) else None


def teleop_channel(
    catalog_row: Optional[Dict[str, Any]],
    channel: str,
) -> Optional[Dict[str, Any]]:
    """Return ``capabilities.telemetry.teleop[channel]`` descriptor or ``None``."""
    if not isinstance(catalog_row, dict):
        return None
    caps = normalize_capabilities(catalog_row.get("capabilities") or {})
    top = (caps.get("telemetry") or {}).get("teleop") or {}
    if not isinstance(top, dict):
        return None
    desc = top.get(channel)
    return desc if isinstance(desc, dict) else None


def telemetry_channel(
    catalog_row: Optional[Dict[str, Any]],
    channel: str,
) -> Optional[Dict[str, Any]]:
    """Backward-compatible alias for ``live_feed_channel``."""
    return live_feed_channel(catalog_row, channel)


def resolve_cam_id_for_tag(catalog_row: Optional[Dict[str, Any]]) -> Optional[int]:
    """Resolve the hardware ``cam_id`` for a catalog row, or ``None``.

    Two sources, in priority order:

    1. Explicit ``cam_id`` field on the row (or under ``properties.cam_id``)
       — catalogs may carry this for non-gripper cameras.
    2. The ``cam_gripper_N`` naming convention on ``id`` (``cam_gripper_1``
       → 1, ``cam_gripper_2`` → 2). Matches the mock + real bundles
       through Phase 5.

    Returning ``None`` lets callers raise a precise 4xx ("no underlying
    cam_id resolvable") rather than guessing.
    """
    if not isinstance(catalog_row, dict):
        return None

    raw = catalog_row.get("cam_id")
    if raw is None:
        props = catalog_row.get("properties") or {}
        if isinstance(props, dict):
            raw = props.get("cam_id")
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.isdigit():
        return int(raw)

    cid = catalog_row.get("id")
    if isinstance(cid, str):
        # ``cam_gripper_1`` / ``cam_gripper_2`` convention.
        if cid.startswith("cam_gripper_"):
            suffix = cid[len("cam_gripper_"):]
            if suffix.isdigit():
                return int(suffix)
    return None


def catalog_declares_table_pose(catalog_row: Optional[Dict[str, Any]]) -> bool:
    """True when the catalog row declares ``tunables.nominal_pose`` (TablePose)."""
    if not isinstance(catalog_row, dict):
        return False
    caps = catalog_row.get("capabilities") or {}
    if not isinstance(caps, dict):
        return False
    tun = caps.get("tunables") or {}
    return isinstance(tun, dict) and "nominal_pose" in tun


def resolve_telemetry_stream_backend(catalog_row: Optional[Dict[str, Any]]) -> str:
    """How to serve ``telemetry.stream`` for this tag: ``table_cam``, ``overhead``, or ``none``."""
    if not isinstance(catalog_row, dict):
        return "none"
    props = catalog_row.get("properties") or {}
    if isinstance(props, dict) and props.get("stream_source") == "overhead":
        return "overhead"
    if resolve_cam_id_for_tag(catalog_row) is not None:
        return "table_cam"
    return "none"


def find_tag_id_for_cam_id(
    catalog_map: Mapping[str, Any],
    cam_id: int,
) -> Optional[str]:
    """Return the first catalog ``tag_id`` whose hardware ``cam_id`` matches.

    Used by COBYLA (Phase 9d) to locate the camera component whose
    ``measurables.camera_image`` supplies the alignment reference.
    """
    want = int(cam_id)
    for tag_id, row in catalog_map.items():
        if not isinstance(row, dict):
            continue
        if resolve_cam_id_for_tag(row) == want:
            return str(tag_id)
    return None


def load_component_library_rows(
    path: str,
    *,
    on_warning: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """Load ``component_library.json`` accepting both legacy and v1 shapes.

    Returns a normalized list of per-component dicts (legacy shape), so
    callers like :func:`catalog_bundle.library_by_tag` are unchanged.

    - **v1 object shape** → validated against §15.1; on hard failure, raises
      :class:`CatalogValidationError`. Soft warnings (§15.2) are routed to
      ``on_warning(msg)`` if provided, else ``print``ed once.
    - **Legacy array shape** → returned as-is (each dict passed through);
      Phase 5 keeps this path alive for unmigrated bundles so mock-mode
      doesn't break before the script is run on a given bundle.
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(f"component_library.json not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if is_v1_object_shape(data):
        rows, warnings = validate_catalog_v1(data, source=path)
        if warnings:
            log = on_warning or (lambda m: print(f"[catalog] {m}"))
            for w in warnings:
                log(w)
        return rows

    if is_legacy_array_shape(data):
        # Pre-migration bundle; pass through unchanged so existing behavior
        # is preserved until the operator runs the migration script.
        return [x for x in data if isinstance(x, dict)]

    raise ValueError(
        f"component_library.json must be either a JSON array (legacy) or a "
        f"{{schema_version, components}} object (v1): {path}"
    )
