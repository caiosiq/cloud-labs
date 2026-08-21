"""Compile ensemble OPTIMIZE IR → edge ``optimization_pipeline`` document.

Phase 0: produce a schema-valid pipeline JSON. Nothing executes yet.
The edge (Phase 2+) reads this document; the coordinator never runs the loop.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, MutableMapping, Optional, Sequence, Union

from lab_model.execution.optimization.errors import PathResolveError
from lab_model.execution.optimization.objective_measurements import (
    measurable_field_from_path,
    resolve_centroid_capture_tag,
)
from lab_model.execution.optimization.paths import parse_variable_path
from lab_model.execution.optimization.spec import (
    ObjectiveTermSpec,
    OptimizeEnsembleParameters,
    VariableRef,
)

PIPELINE_SCHEMA_VERSION = 1

# Ensemble metric ids that the edge pipeline schema prefers without the ``_px`` suffix.
_METRIC_ALIASES = {
    "rms_distance_px": "rms_distance",
}

# Image / TorchScript source kinds → kernel + capture field.
_DERIVED_CENTROID_KERNEL = "builtin.roi_centroid"
_CAMERA_FIELD_DEFAULT = "camera_image"


class PipelineCompileError(ValueError):
    """Ensemble IR could not be compiled into an edge pipeline."""


def _as_ensemble_spec(
    spec: Union[OptimizeEnsembleParameters, Mapping[str, Any]],
) -> OptimizeEnsembleParameters:
    if isinstance(spec, OptimizeEnsembleParameters):
        return spec
    if not isinstance(spec, Mapping):
        raise PipelineCompileError("spec must be OptimizeEnsembleParameters or a mapping")
    try:
        from lab_model.execution.optimization.preflight import strip_legacy_optimize_fields

        return OptimizeEnsembleParameters.model_validate(
            strip_legacy_optimize_fields(dict(spec))
        )
    except Exception as exc:  # noqa: BLE001 — surface as compile error
        raise PipelineCompileError(f"invalid ensemble spec: {exc}") from exc


def _catalog_row(
    catalog: Optional[Mapping[str, Any]],
    tag_id: str,
) -> Optional[Mapping[str, Any]]:
    if not catalog:
        return None
    row = catalog.get(tag_id)
    return row if isinstance(row, Mapping) else None


def _motor_controller(catalog: Optional[Mapping[str, Any]], tag_id: str) -> str:
    row = _catalog_row(catalog, tag_id)
    if row is None:
        raise PipelineCompileError(
            f"tag {tag_id!r}: catalog entry required to resolve motor controller"
        )
    controller = row.get("motor_controller")
    if isinstance(controller, str) and controller.strip():
        return controller.strip()
    params = row.get("parameters")
    if isinstance(params, Mapping):
        nested = params.get("motor_controller")
        if isinstance(nested, str) and nested.strip():
            return nested.strip()
    raise PipelineCompileError(
        f"tag {tag_id!r}: catalog row missing motor_controller "
        f"(needed for path tunables.nominal_motor_positions.*)"
    )


def path_to_actuator(
    path: str,
    *,
    tag_id: str,
    catalog: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Translate a coordinator variable ``path`` into an edge ``actuator`` object."""
    try:
        parsed = parse_variable_path(path)
    except PathResolveError as exc:
        raise PipelineCompileError(
            f"tag {tag_id!r}: {exc.reason} (path={path!r})"
        ) from exc

    if parsed.kind == "motor":
        assert parsed.motor_id is not None
        return {
            "kind": "motor",
            "controller": _motor_controller(catalog, tag_id),
            "motor_id": int(parsed.motor_id),
        }
    if parsed.kind == "pose_axis":
        assert parsed.axis is not None
        return {"kind": "pose", "axis": parsed.axis}
    raise PipelineCompileError(
        f"tag {tag_id!r}: unsupported path kind {parsed.kind!r} for {path!r}"
    )


def _compile_variable(
    var: VariableRef,
    *,
    catalog: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    actuator = path_to_actuator(var.path, tag_id=var.tag_id, catalog=catalog)
    # Path → actuator kind is authoritative. UI historically marked pose axes
    # as continuous; continuous blocks on the edge only accept motors.
    if actuator.get("kind") == "pose":
        physical_type = "invasive_discrete"
    elif actuator.get("kind") == "motor":
        physical_type = "continuous"
    else:
        physical_type = var.physical_type
    out: Dict[str, Any] = {
        "id": var.id,
        "tag_id": var.tag_id,
        "actuator": actuator,
        "physical_type": physical_type,
        "unit": var.unit,
        "bounds": {"min": float(var.bounds.min), "max": float(var.bounds.max)},
        "delta": bool(var.delta),
    }
    if var.step_hint is not None:
        out["step_hint"] = float(var.step_hint)
    if physical_type == "invasive_discrete":
        if var.touch_and_go is not None:
            out["touch_and_go"] = var.touch_and_go.model_dump(exclude_none=True)
        else:
            out["touch_and_go"] = {
                "required": True,
                "gripper_tag": var.tag_id,
                "measure_only_while_released": True,
                "settle_ms_after_release": 450,
            }
    return out


def _source_field(term: ObjectiveTermSpec) -> str:
    src = term.source
    from_field = getattr(src, "from_", None) or (
        src.model_dump(by_alias=True).get("from") if hasattr(src, "model_dump") else None
    )
    if isinstance(from_field, str) and from_field.strip():
        field = measurable_field_from_path(from_field.strip())
        if field:
            return field
        if from_field.startswith("measurables."):
            return from_field.split(".", 1)[1]
        return from_field.strip()
    path = src.path
    if isinstance(path, str) and path.startswith("measurables."):
        return measurable_field_from_path(path) or _CAMERA_FIELD_DEFAULT
    return _CAMERA_FIELD_DEFAULT


def _metric_name(metric: str) -> str:
    return _METRIC_ALIASES.get(metric, metric)


def _term_params(term: ObjectiveTermSpec) -> Dict[str, Any]:
    src = term.source
    params: Dict[str, Any] = {}
    if src.target_px is not None:
        tp = src.target_px
        params["target"] = [float(tp.get("x", 0.0)), float(tp.get("y", 0.0))]
        params["target_px"] = dict(tp)
    if src.target is not None:
        params.setdefault("target", src.target)
    if src.target_scalar is not None:
        params["target_scalar"] = float(src.target_scalar)
    if src.feature_index is not None:
        params["feature_index"] = src.feature_index
    elif src.kind == "derived_centroid":
        params["feature_index"] = [0, 1]
    if src.feature_names is not None:
        params["feature_names"] = list(src.feature_names)
    if src.normalize is not None:
        params["normalize"] = {
            "min": float(src.normalize.min),
            "max": float(src.normalize.max),
        }
    if src.use_abs is not None:
        params["use_abs"] = bool(src.use_abs)
    # Beam-presence latch (session fills peak_ref on first eval when enabled).
    extra = getattr(src, "__pydantic_extra__", None) or {}
    for key in (
        "latch_peak_ref",
        "min_peak_ratio",
        "peak_feature_index",
        "peak_ref",
        "absent_penalty",
        "rms_scale_px",
        "value_scale_px",
        "loss_cap",
        "normalize_by_fov",
        "frame_hw",
        "latch_value_ref",
        "value_ref",
        "origin_px",
        "axis",
        "direction",
    ):
        if hasattr(src, key) and getattr(src, key) is not None:
            params[key] = getattr(src, key)
        elif key in extra and extra[key] is not None:
            params[key] = extra[key]
    # Origin alias for signed-axis push (also mirror onto target_px for overlays).
    if "origin_px" in params and "target_px" not in params:
        op = params["origin_px"]
        if isinstance(op, dict):
            params["target_px"] = dict(op)
            params.setdefault(
                "target",
                [float(op.get("x", 0.0)), float(op.get("y", 0.0))],
            )
    # Centroid align terms: latch full-frame peak on eval 1 by default.
    if "latch_peak_ref" not in params:
        kid = (src.kernel_id or "").strip()
        if term.metric in (
            "rms_distance",
            "rms_distance_px",
            "beam_presence",
            "signed_axis_offset",
        ) and (
            kid in ("", "builtin.roi_centroid", "builtin.beam_com")
            or src.kind == "derived_centroid"
        ):
            params["latch_peak_ref"] = True
            params.setdefault("min_peak_ratio", 0.5)
            params.setdefault("peak_feature_index", 2)
    if term.metric in ("ratio_to_ref", "ratio_from_ref"):
        params.setdefault("latch_value_ref", True)
        params.setdefault("feature_index", 0)
    if term.metric in ("rms_distance", "rms_distance_px", "signed_axis_offset"):
        params.setdefault("loss_cap", 2.0)
    if term.metric == "signed_axis_offset":
        params.setdefault("feature_index", [0, 1])
        params.setdefault("normalize_by_fov", True)
        params.setdefault("axis", "x")
        params.setdefault("direction", 1)
    if term.metric == "minimize_value" and (
        params.get("normalize_by_fov")
        or (src.kernel_id or "").strip()
        in ("builtin.gaussian_beam_fit", "builtin.beam_shift")
    ):
        params.setdefault("normalize_by_fov", True)
        params.setdefault("loss_cap", 2.0)
    return params


def _ensure_capture(
    captures: List[Dict[str, Any]],
    capture_index: Dict[tuple[str, str], str],
    *,
    tag_id: str,
    field: str,
) -> str:
    key = (tag_id, field)
    existing = capture_index.get(key)
    if existing:
        return existing
    capture_id = f"cap_{len(captures) + 1}"
    captures.append({"id": capture_id, "tag_id": tag_id, "field": field})
    capture_index[key] = capture_id
    return capture_id


def _compile_term(
    term: ObjectiveTermSpec,
    *,
    catalog: Optional[Mapping[str, Any]],
    captures: List[Dict[str, Any]],
    capture_index: Dict[tuple[str, str], str],
) -> Dict[str, Any]:
    src = term.source
    kind = (src.kind or "").strip()
    base: Dict[str, Any] = {
        "id": term.id,
        "weight": float(term.weight),
        "metric": _metric_name(term.metric),
    }
    params = _term_params(term)
    if params:
        base["params"] = params

    if kind == "derived_centroid":
        capture_tag = resolve_centroid_capture_tag(catalog_map=catalog, term=term)
        field = _source_field(term) or _CAMERA_FIELD_DEFAULT
        if field != _CAMERA_FIELD_DEFAULT and not field:
            field = _CAMERA_FIELD_DEFAULT
        # derived_centroid always reads a camera frame
        if not field or field == "last_optimization_score":
            field = _CAMERA_FIELD_DEFAULT
        capture_id = _ensure_capture(
            captures, capture_index, tag_id=capture_tag, field=field
        )
        base["capture_id"] = capture_id
        base["kernel_id"] = _DERIVED_CENTROID_KERNEL
        return base

    if kind in {"torchscript_scalar", "torchscript_features"}:
        kid = (src.kernel_id or "").strip()
        if not kid:
            raise PipelineCompileError(
                f"term {term.id!r}: {kind} requires source.kernel_id"
            )
        capture_tag = str(src.tag_id)
        field = _source_field(term) or _CAMERA_FIELD_DEFAULT
        capture_id = _ensure_capture(
            captures, capture_index, tag_id=capture_tag, field=field
        )
        base["capture_id"] = capture_id
        base["kernel_id"] = kid
        return base

    if kind == "measurable_scalar":
        path = src.path or (
            f"measurables.{_source_field(term)}" if _source_field(term) else None
        )
        if not path:
            raise PipelineCompileError(
                f"term {term.id!r}: measurable_scalar requires source.path"
            )
        # Prefer an explicit kernel when already present (forward-compat).
        kid = (src.kernel_id or "").strip()
        if kid:
            capture_tag = str(src.tag_id)
            field = measurable_field_from_path(path) or _CAMERA_FIELD_DEFAULT
            capture_id = _ensure_capture(
                captures, capture_index, tag_id=capture_tag, field=field
            )
            base["capture_id"] = capture_id
            base["kernel_id"] = kid
            return base
        base["measurable_path"] = path if path.startswith("measurables.") else f"measurables.{path}"
        return base

    # Unknown kind: require kernel_id + treat as capture from tag
    kid = (src.kernel_id or "").strip()
    if kid:
        capture_tag = str(src.tag_id)
        field = _source_field(term) or _CAMERA_FIELD_DEFAULT
        capture_id = _ensure_capture(
            captures, capture_index, tag_id=capture_tag, field=field
        )
        base["capture_id"] = capture_id
        base["kernel_id"] = kid
        return base

    raise PipelineCompileError(
        f"term {term.id!r}: unsupported source kind {kind!r}; "
        "expected derived_centroid, torchscript_*, or measurable_scalar"
    )


def compile_pipeline(
    spec: Union[OptimizeEnsembleParameters, Mapping[str, Any]],
    catalog: Optional[Mapping[str, Any]] = None,
    *,
    kernel_packages: Optional[Sequence[Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    """Compile ensemble parameters into an edge optimization pipeline document.

    Parameters
    ----------
    spec:
        ``OptimizeEnsembleParameters`` or a raw ensemble ``parameters`` dict.
    catalog:
        Optional ``tag_id → library row`` map. Required when any variable uses a
        motor path (to resolve ``motor_controller``). Also used to pick a camera
        tag for ``derived_centroid`` terms on non-camera components.
    kernel_packages:
        Optional session packages (``session.*``) to attach; premade kernels are
        not re-shipped here.
    """
    ensemble = _as_ensemble_spec(spec)

    variables = [
        _compile_variable(var, catalog=catalog) for var in ensemble.variables
    ]

    captures: List[Dict[str, Any]] = []
    capture_index: Dict[tuple[str, str], str] = {}
    terms = [
        _compile_term(
            term,
            catalog=catalog,
            captures=captures,
            capture_index=capture_index,
        )
        for term in ensemble.objective.terms
    ]

    solver = ensemble.solver.model_dump(exclude_none=True)
    # Drop coordinator-only noise; keep block_cobyla fields the edge understands.
    pipeline: Dict[str, Any] = {
        "schema_version": PIPELINE_SCHEMA_VERSION,
        "variables": variables,
        "capture": captures,
        "objective": {
            "type": ensemble.objective.type,
            "minimize": bool(ensemble.objective.minimize),
            "terms": terms,
        },
        "solver": solver,
    }
    if ensemble.session_label:
        pipeline["session_label"] = ensemble.session_label

    packages: List[Dict[str, Any]] = []
    if kernel_packages:
        for raw in kernel_packages:
            if not isinstance(raw, Mapping):
                continue
            kid = str(raw.get("kernel_id") or raw.get("id") or "").strip()
            if not kid:
                continue
            if not kid.startswith("session."):
                raise PipelineCompileError(
                    f"kernel_packages entry {kid!r} is not a session.* id; "
                    "premade kernels stay on the edge catalog"
                )
            packages.append(dict(raw))
    # Also forward session ids listed on the ensemble kernels allowlist if packages
    # were not supplied (ids only — no artifacts).
    if not packages and ensemble.kernels:
        session_ids = [k for k in ensemble.kernels if str(k).startswith("session.")]
        if session_ids:
            # Presence without artifacts is allowed in the schema only with digest;
            # skip bare ids — callers must pass kernel_packages for session artifacts.
            pass
    if packages:
        pipeline["kernel_packages"] = packages

    return pipeline


def attach_pipeline(
    params: MutableMapping[str, Any],
    *,
    catalog: Optional[Mapping[str, Any]] = None,
    kernel_packages: Optional[Sequence[Mapping[str, Any]]] = None,
) -> MutableMapping[str, Any]:
    """Mutate ensemble ``params`` to include a compiled ``pipeline`` key.

    No-op when ``mode != \"ensemble\"``. Raises :class:`PipelineCompileError` on
    failure (caller may refuse the command).
    """
    if str(params.get("mode") or "").strip() != "ensemble":
        return params
    packages = kernel_packages
    if packages is None:
        raw = params.get("kernel_packages")
        if isinstance(raw, list):
            packages = raw
    params["pipeline"] = compile_pipeline(params, catalog, kernel_packages=packages)
    return params


def pipeline_schema_path() -> Path:
    """Locate ``optimization_pipeline.schema.json`` in the repo contract schemas."""
    try:
        from cloudlabs_edge_dev.schemas_path import contract_schemas_dir

        path = contract_schemas_dir() / "optimization_pipeline.schema.json"
        if path.is_file():
            return path
    except Exception:
        pass
    # backend/lab_model/execution/optimization → repo root
    root = Path(__file__).resolve().parents[4]
    path = root / "schemas" / "edge_contract" / "v1" / "optimization_pipeline.schema.json"
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


def validate_pipeline_document(pipeline: Mapping[str, Any]) -> None:
    """Raise ``PipelineCompileError`` if ``pipeline`` fails the contract schema."""
    try:
        from jsonschema import Draft202012Validator
        from referencing import Registry, Resource
    except ImportError as exc:  # pragma: no cover
        raise PipelineCompileError(
            "jsonschema/referencing required to validate pipeline documents"
        ) from exc

    schema_path = pipeline_schema_path()
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    registry: Registry = Registry().with_resource(
        schema.get("$id") or schema_path.name,
        Resource.from_contents(schema),
    )
    validator = Draft202012Validator(schema, registry=registry)
    errors = sorted(validator.iter_errors(dict(pipeline)), key=lambda e: list(e.path))
    if errors:
        first = errors[0]
        path = ".".join(str(p) for p in first.path) or "<root>"
        raise PipelineCompileError(f"pipeline schema invalid at {path}: {first.message}")


def optional_catalog_map(lab: Any = None) -> Dict[str, Any]:
    """Best-effort ``tag_id → library row`` for pipeline compile."""
    if lab is not None:
        for attr in ("catalog_map", "component_library", "library"):
            raw = getattr(lab, attr, None)
            if isinstance(raw, Mapping) and raw:
                return {str(k): v for k, v in raw.items() if isinstance(v, Mapping)}
        getter = getattr(lab, "get_component_library", None)
        if callable(getter):
            try:
                rows = getter()
            except Exception:
                rows = None
            if isinstance(rows, Mapping):
                return {str(k): v for k, v in rows.items() if isinstance(v, Mapping)}
            if isinstance(rows, list):
                out: Dict[str, Any] = {}
                for row in rows:
                    if isinstance(row, Mapping) and row.get("tag_id"):
                        out[str(row["tag_id"])] = dict(row)
                if out:
                    return out
    try:
        from lab_model.coordinator.catalog.bundle import library_by_tag

        return library_by_tag()
    except Exception:
        return {}


__all__ = [
    "PIPELINE_SCHEMA_VERSION",
    "PipelineCompileError",
    "attach_pipeline",
    "compile_pipeline",
    "optional_catalog_map",
    "path_to_actuator",
    "pipeline_schema_path",
    "validate_pipeline_document",
]
