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

Edge live plane (wire / live_channel bindings for measurables) is specified in
``schemas/edge_contract/v1/measurable_live_decl.schema.json``. Catalog-row
validation of those optional fields will land with Edge Contract Phase 1+;
until then treat that schema as normative for new edges.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, FrozenSet, List, Mapping, Optional, Set, Tuple

_LOG = logging.getLogger(__name__)
_LEGACY_CAM_GRIPPER_WARNED = False


# ---------------------------------------------------------------------------
# Schema version + widget vocabulary (§14)
# ---------------------------------------------------------------------------

#: Catalog ``schema_version`` values this build can load.
SUPPORTED_SCHEMA_VERSIONS: FrozenSet[int] = frozenset({1})

#: §14.1 tunable widgets.
KNOWN_WIDGETS_TUNABLE: FrozenSet[str] = frozenset({
    "TablePose",
    "PoseReadout",  # reported_pose (read-only bench report of the pose tunable)
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
# Primitive set (loaded lazily to avoid an unconditional ``lab_model.language.primitives``
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
        from lab_model.language.primitives.ids import PrimitiveId  # noqa: PLC0415
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

_LASER_TUNABLE_PRIMITIVES = (
    "SET_LASER_OUTPUT",
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
      ``nominal_motor_positions`` tunable and ``last_optimization_score``
      measurable. Lab motor angles recalculate ``nominal_motor_positions``
      (they are not a separate measurable).
    - ``OPTICAL_CAMERA`` adds ``exposure_time_ms`` tunable, ``camera_image``
      measurable, and the ``stream`` telemetry channel.
    """
    comp_type = str(row.get("type") or "")
    motor_ids = row.get("motor_ids") or []
    tag_id = str(row.get("tag_id") or "")

    tunables: Dict[str, Any] = {
        "nominal_pose": {"widget": "TablePose"},
        "reported_pose": {
            "widget": "PoseReadout",
            "physical_interpretation": (
                "Bench-reported table pose for the same DOF as nominal_pose "
                "(mm / degrees). Refresh from scan or settle after motion — "
                "not a measurable."
            ),
        },
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
            "transport": "websocket",
            "url": f"/api/components/{_TAG_TOKEN}/teleop/session",
            "default_hz": 50,
            "default_fps": 50,
        },
    }
    measurables: Dict[str, Any] = {}
    live_feed: Dict[str, Any] = {}
    primitives: List[str] = ["MOVE_COMPONENT"]

    if isinstance(motor_ids, list) and motor_ids:
        tunables["nominal_motor_positions"] = {
            "widget": "JsonInspector",
            "unit": "deg",
            "physical_interpretation": (
                "Motor angles for this mount (degrees). Lab observe / tracker "
                "recalculates this same tunable — it is not a measurable."
            ),
        }
        measurables["last_optimization_score"] = {
            "widget": "NumberBadge",
            "format": ".3f",
            "dtype": "float64",
            "layout": "scalar",
            "domain": "scalar",
            "physical_interpretation": (
                "Last closed-loop / optimize scalar for this part. Captured, "
                "not commanded."
            ),
        }
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
        measurables["camera_image"] = {
            "widget": "ImageViewer",
            "dtype": "uint8",
            "layout": "bgr_hwc_uint8",
            "domain": "spatial",
            "shape": "(H, W, 3)",
            "axes": {
                "H": "rows (y), pixels top→bottom",
                "W": "cols (x), pixels left→right",
                "3": "BGR channels (OpenCV / kernel layout)",
            },
            "wire_format": "png",
            "physical_interpretation": (
                "Still frame from this camera. Kernels and "
                "lab.measurable(...).resolve() use BGR uint8 H×W×3; PNG is "
                "storage/wire only."
            ),
        }
        # Phase 6 owns the route; URLs are direct per §16.6.
        if tag_id:
            url_stream = f"/api/components/{_TAG_TOKEN}/telemetry/stream"
            live_feed["stream"] = {"widget": "MJPEGViewer", "url": url_stream}

    if comp_type == "LASER_SOURCE":
        tunables["output_power_mw"] = {
            "widget": "FloatRange",
            "min": 0.0,
            "max": 100.0,
            "default": 0.0,
            "unit": "mW",
        }
        measurables["output_power_readback_mw"] = {
            "widget": "NumberBadge",
            "format": ".2f",
            "unit": "mW",
            "dtype": "float64",
            "layout": "scalar",
            "domain": "scalar",
            "physical_interpretation": (
                "Measured optical output power (mW). A captured sensor "
                "summary — not a command."
            ),
        }
        primitives.extend(list(_LASER_TUNABLE_PRIMITIVES))

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
        from lab_model.language.parameters import normalize_catalog_row_parameters

        normalize_catalog_row_parameters(row)
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


# ---------------------------------------------------------------------------
# Hardware binding (Phase 2 — catalog → bench driver)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HardwareBinding:
    """Resolved hardware attachment for a catalog component row."""

    backend: str
    recorder_cam_id: Optional[int] = None
    recorder_port: Optional[int] = None
    device_index: Optional[int] = None
    role: Optional[str] = None
    preview_profile: Optional[str] = None


def _binding_from_dict(raw: Dict[str, Any]) -> HardwareBinding:
    backend = str(raw.get("backend") or "none").strip().lower()
    rec_id = raw.get("recorder_cam_id")
    if rec_id is None:
        rec_id = raw.get("cam_id")
    if isinstance(rec_id, str) and rec_id.isdigit():
        rec_id = int(rec_id)
    if not isinstance(rec_id, int):
        rec_id = None
    port = raw.get("recorder_port")
    if isinstance(port, str) and port.isdigit():
        port = int(port)
    if not isinstance(port, int):
        port = None
    dev = raw.get("device_index")
    if isinstance(dev, str) and dev.isdigit():
        dev = int(dev)
    if not isinstance(dev, int):
        dev = None
    role = raw.get("role")
    role_s = str(role).strip() if role is not None else None
    profile = raw.get("preview_profile")
    profile_s = str(profile).strip() if profile is not None else None
    return HardwareBinding(
        backend=backend,
        recorder_cam_id=rec_id,
        recorder_port=port,
        device_index=dev,
        role=role_s,
        preview_profile=profile_s,
    )


def resolve_hardware_binding(
    catalog_row: Optional[Dict[str, Any]],
) -> Optional[HardwareBinding]:
    """Resolve ``parameters.hardware_binding`` (legacy ``properties`` accepted).

    Priority:

    1. Explicit ``parameters.hardware_binding`` (or legacy ``properties``) object.
    2. ``stream_source == overhead`` → OpenCV overhead USB.
    3. Legacy ``cam_gripper_N`` slug → ``recorder_tcp`` (deprecated).
    """
    global _LEGACY_CAM_GRIPPER_WARNED  # noqa: PLW0603

    from lab_model.language.parameters import catalog_parameters_bag

    if not isinstance(catalog_row, dict):
        return None

    props = catalog_parameters_bag(catalog_row)
    if props:
        raw = props.get("hardware_binding")
        if isinstance(raw, dict) and raw.get("backend"):
            return _binding_from_dict(raw)
        if props.get("stream_source") == "overhead":
            dev = props.get("device_index")
            if isinstance(dev, str) and dev.isdigit():
                dev = int(dev)
            if not isinstance(dev, int):
                dev = 0
            return HardwareBinding(
                backend="opencv_usb",
                device_index=dev,
                role="table_overview",
            )

    cid = catalog_row.get("id")
    if isinstance(cid, str) and cid.startswith("cam_gripper_"):
        suffix = cid[len("cam_gripper_") :]
        if suffix.isdigit():
            if not _LEGACY_CAM_GRIPPER_WARNED:
                _LEGACY_CAM_GRIPPER_WARNED = True
                _LOG.warning(
                    "Catalog row id=%r uses deprecated cam_gripper_N slug; "
                    "add parameters.hardware_binding (recorder_tcp).",
                    cid,
                )
            return HardwareBinding(
                backend="recorder_tcp",
                recorder_cam_id=int(suffix),
            )

    raw_cam = catalog_row.get("cam_id")
    if raw_cam is None:
        raw_cam = props.get("cam_id")
    if isinstance(raw_cam, int):
        return HardwareBinding(backend="recorder_tcp", recorder_cam_id=raw_cam)
    if isinstance(raw_cam, str) and raw_cam.isdigit():
        return HardwareBinding(backend="recorder_tcp", recorder_cam_id=int(raw_cam))

    return None


def catalog_declared_primitives(catalog_row: Optional[Dict[str, Any]]) -> List[str]:
    """Primitive id strings declared on a catalog row."""
    if not isinstance(catalog_row, dict):
        return []
    caps = normalize_capabilities(catalog_row.get("capabilities") or {})
    prims = caps.get("primitives") or []
    return [str(p) for p in prims if isinstance(p, str)]


def catalog_is_placeable_on_table(catalog_row: Optional[Dict[str, Any]]) -> bool:
    """True when a camera (or other instrument) sits on the breadboard like other optics."""
    if not isinstance(catalog_row, dict):
        return False
    from lab_model.language.parameters import catalog_parameters_bag

    return catalog_parameters_bag(catalog_row).get("placeable_on_table") is True


def catalog_is_fixed_instrument(catalog_row: Optional[Dict[str, Any]]) -> bool:
    """True for bench-fixed components (cameras, lasers) — not arm-scanned optics."""
    if not isinstance(catalog_row, dict):
        return False
    if catalog_is_placeable_on_table(catalog_row):
        return False
    from lab_model.language.parameters import catalog_parameters_bag

    props = catalog_parameters_bag(catalog_row)
    if props.get("fixture") is True:
        return True
    if props.get("stream_source") == "overhead":
        return True
    binding = resolve_hardware_binding(catalog_row)
    if binding is None:
        return False
    comp_type = str(catalog_row.get("type") or "")
    if comp_type in ("OPTICAL_CAMERA", "CEILING_CAMERA", "LASER_SOURCE"):
        return True
    if binding.backend in ("recorder_tcp", "opencv_usb", "overhead"):
        return True
    return False


def resolve_cam_id_for_tag(catalog_row: Optional[Dict[str, Any]]) -> Optional[int]:
    """Resolve the hardware ``cam_id`` for a catalog row, or ``None``.

    Priority:

    1. ``hardware_binding.recorder_cam_id`` (``recorder_tcp`` backend).
    2. Explicit ``cam_id`` on the row or under ``parameters``.
    3. Deprecated ``cam_gripper_N`` slug on ``id``.
    """
    from lab_model.language.parameters import catalog_parameters_bag

    if not isinstance(catalog_row, dict):
        return None

    binding = resolve_hardware_binding(catalog_row)
    if binding is not None and binding.backend == "recorder_tcp":
        if binding.recorder_cam_id is not None:
            return int(binding.recorder_cam_id)

    raw = catalog_row.get("cam_id")
    if raw is None:
        raw = catalog_parameters_bag(catalog_row).get("cam_id")
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.isdigit():
        return int(raw)

    cid = catalog_row.get("id")
    if isinstance(cid, str) and cid.startswith("cam_gripper_"):
        suffix = cid[len("cam_gripper_") :]
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

    binding = resolve_hardware_binding(catalog_row)
    if binding is not None:
        if binding.backend == "recorder_tcp":
            return "table_cam"
        if binding.backend in ("opencv_usb", "overhead"):
            from lab_model.language.parameters import catalog_parameters_bag

            props = catalog_parameters_bag(catalog_row)
            if props.get("stream_source") == "overhead":
                return "overhead"
            if binding.role in ("table_overview", "inventory_stereo_left", "inventory_stereo_right"):
                return "overhead"
            if binding.backend == "overhead":
                return "overhead"

    from lab_model.language.parameters import catalog_parameters_bag

    if catalog_parameters_bag(catalog_row).get("stream_source") == "overhead":
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
        binding = resolve_hardware_binding(row)
        if binding is not None and binding.recorder_cam_id == want:
            return str(tag_id)
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
        from lab_model.language.parameters import normalize_catalog_row_parameters

        rows = [x for x in data if isinstance(x, dict)]
        for row in rows:
            normalize_catalog_row_parameters(row)
        return rows

    raise ValueError(
        f"component_library.json must be either a JSON array (legacy) or a "
        f"{{schema_version, components}} object (v1): {path}"
    )
