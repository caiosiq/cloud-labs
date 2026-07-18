"""Capability wiki helpers for SDK discovery (mirrors /wiki UI)."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence


def normalize_capabilities(caps: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Normalize catalog capabilities to the three-pillar shape."""
    if not isinstance(caps, Mapping):
        return {
            "statecontrol": {"tunables": {}, "measurables": {}},
            "telemetry": {"teleop": {}, "live_feed": {}},
            "primitives": [],
        }
    if caps.get("statecontrol"):
        tel = dict(caps.get("telemetry") or {})
        tel.setdefault("teleop", {})
        tel.setdefault("live_feed", {})
        return {
            "statecontrol": dict(caps.get("statecontrol") or {"tunables": {}, "measurables": {}}),
            "telemetry": tel,
            "primitives": list(caps.get("primitives") or []),
        }
    tunables = dict(caps.get("tunables") or {})
    teleop = tunables.pop("teleop", None) or {}
    return {
        "statecontrol": {
            "tunables": tunables,
            "measurables": dict(caps.get("measurables") or {}),
        },
        "telemetry": {
            "teleop": dict(teleop) if isinstance(teleop, Mapping) else {},
            "live_feed": dict(caps.get("telemetry") or {}),
        },
        "primitives": list(caps.get("primitives") or []),
    }


def _format_bounds(decl: Mapping[str, Any]) -> Optional[str]:
    if "min" in decl or "max" in decl:
        return f"[{decl.get('min', '-inf')}, {decl.get('max', '+inf')}]"
    if decl.get("bounds") is not None:
        return str(decl["bounds"])
    return None


def tunable_script_handles(
    tag_id: str,
    field: str,
    decl: Mapping[str, Any],
    *,
    motor_ids: Optional[Sequence[Any]] = None,
) -> List[Dict[str, Any]]:
    """Expand a tunable field into concrete SDK script handles."""
    widget = decl.get("widget") or "—"
    unit = decl.get("unit") or ""
    bounds = _format_bounds(decl)

    if field == "nominal_pose":
        rows: List[Dict[str, Any]] = []
        for axis in ("x", "y", "rotation"):
            path = f"tunables.nominal_pose.{axis}"
            rows.append(
                {
                    "path": path,
                    "widget": widget,
                    "unit": "deg" if axis == "rotation" else "mm",
                    "bounds": bounds,
                    "snippet": f'lab.move_component({tag_id!r}, {path!r}, <value>)',
                }
            )
        return rows

    if field == "nominal_motor_positions":
        motors: Sequence[Any] = list(motor_ids) if motor_ids else ["<motor_id>"]
        rows = []
        for mid in motors:
            path = f"tunables.nominal_motor_positions.{mid}"
            rows.append(
                {
                    "path": path,
                    "widget": widget,
                    "unit": unit or "deg",
                    "bounds": bounds,
                    "note": (
                        "Lab observe / tracker recalculates this same tunable "
                        "— not a measurable."
                    ),
                    "snippet": f'lab.move_component({tag_id!r}, {path!r}, <value>)',
                }
            )
        return rows

    path = f"tunables.{field}"
    write_hint = decl.get("write_primitive") or "declared primitive / Twin UI"
    return [
        {
            "path": path,
            "widget": widget,
            "unit": unit or None,
            "bounds": bounds,
            "note": f"write via {write_hint}",
            "snippet": (
                f"# {path} — write via {write_hint}\n"
                "# (no dedicated SDK helper yet; use Twin or POST /api/command)"
            ),
        }
    ]


def measurable_script_handle(tag_id: str, field: str, decl: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Build a measurable SDK handle row."""
    decl = decl or {}
    # Pose / motor angles recalculate tunables — never advertise as measurables.
    if field in ("pose", "motor_rotations", "last_optimized_pose"):
        return {}
    dtype = decl.get("dtype")
    layout = decl.get("layout")
    shape = decl.get("shape") or decl.get("tensor_shape")
    axes = decl.get("axes") if isinstance(decl.get("axes"), Mapping) else {}
    tensor_lines = []
    if dtype or layout or shape:
        tensor_lines.append(
            " · ".join(str(x) for x in (dtype, layout, shape) if x)
        )
    if isinstance(axes, Mapping):
        for k, v in axes.items():
            tensor_lines.append(f"{k}: {v}")
    if decl.get("wire_format"):
        tensor_lines.append(f"wire: {decl.get('wire_format')}")
    return {
        "path": f"measurables.{field}",
        "field": field,
        "widget": decl.get("widget") or "—",
        "dtype": dtype,
        "layout": layout,
        "shape": shape,
        "axes": dict(axes) if isinstance(axes, Mapping) else {},
        "tensor_text": "\n".join(tensor_lines) if tensor_lines else None,
        "domain": decl.get("domain"),
        "description": decl.get("description") or decl.get("label"),
        "physical_interpretation": decl.get("physical_interpretation"),
        "snippet": f'lab.measurable({tag_id!r}, {field!r}).resolve(record=True)',
    }


def describe_component_row(
    row: Mapping[str, Any],
    *,
    on_bench: bool = False,
    registries: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a wiki-style description dict from a catalog library row."""
    tag_id = str(row.get("tag_id") or "").strip()
    caps = normalize_capabilities(row.get("capabilities") if isinstance(row.get("capabilities"), Mapping) else None)
    sc = caps.get("statecontrol") or {}
    tunables_decl = sc.get("tunables") if isinstance(sc.get("tunables"), Mapping) else {}
    measurables_decl = sc.get("measurables") if isinstance(sc.get("measurables"), Mapping) else {}
    reg_tunables = (registries or {}).get("tunables") if isinstance(registries, Mapping) else {}
    if not isinstance(reg_tunables, Mapping):
        reg_tunables = {}

    tunable_rows: List[Dict[str, Any]] = []
    for field, decl in tunables_decl.items():
        merged = dict(decl) if isinstance(decl, Mapping) else {}
        reg = reg_tunables.get(field)
        if isinstance(reg, Mapping):
            merged.setdefault("widget", reg.get("widget"))
            merged.setdefault("write_primitive", reg.get("write_primitive"))
        tunable_rows.extend(
            tunable_script_handles(
                tag_id,
                field,
                merged,
                motor_ids=row.get("motor_ids") if isinstance(row.get("motor_ids"), list) else None,
            )
        )

    measurable_rows = [
        row
        for field, decl in measurables_decl.items()
        if (
            row := measurable_script_handle(
                tag_id, field, decl if isinstance(decl, Mapping) else {}
            )
        )
    ]

    from lab_model.language.parameters import parameters_from_catalog_row

    parameters = parameters_from_catalog_row(row if isinstance(row, Mapping) else {})

    tel = caps.get("telemetry") or {}
    return {
        "tag_id": tag_id,
        "name": row.get("name"),
        "type": row.get("type"),
        "id": row.get("id"),
        "on_bench": bool(on_bench),
        "tunables": tunable_rows,
        "measurables": measurable_rows,
        "parameters": parameters,
        "primitives": list(caps.get("primitives") or []),
        "telemetry": {
            "teleop": list((tel.get("teleop") or {}).keys()) if isinstance(tel.get("teleop"), Mapping) else [],
            "live_feed": list((tel.get("live_feed") or {}).keys())
            if isinstance(tel.get("live_feed"), Mapping)
            else [],
        },
        "capabilities": caps,
    }
