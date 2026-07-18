"""Objective term → bench measurement plan (shared mock/real edge coverage)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Mapping, Optional

from lab_model.coordinator.catalog.schema import (
    resolve_cam_id_for_tag,
    resolve_hardware_binding,
    resolve_telemetry_stream_backend,
)
from lab_model.language.domain.component import get_measurables
from lab_model.execution.optimization.paths import component_entry
from lab_model.execution.optimization.spec import ObjectiveSpec, ObjectiveTermSpec

# Scalar fields that may be satisfied from a live camera frame on the real bench.
IMAGE_FALLBACK_SCALAR_FIELDS = frozenset({"last_optimization_score"})

# Fields that must be read from hardware/state — never image-derived on real.
HARDWARE_SCALAR_FIELDS = frozenset(
    {
        "output_power_readback_mw",
        "output_power_mw",
    }
)


def is_camera_capable_row(catalog_row: Optional[Mapping[str, Any]]) -> bool:
    if not isinstance(catalog_row, Mapping):
        return False
    comp_type = str(catalog_row.get("type") or "")
    if comp_type in ("OPTICAL_CAMERA", "CEILING_CAMERA"):
        return True
    row_dict = dict(catalog_row) if not isinstance(catalog_row, dict) else catalog_row
    binding = resolve_hardware_binding(row_dict)
    if binding is not None and binding.backend in ("recorder_tcp", "opencv_usb", "overhead"):
        return True
    if resolve_cam_id_for_tag(row_dict) is not None:
        return True
    if resolve_telemetry_stream_backend(row_dict) in ("overhead", "opencv_usb", "recorder_tcp"):
        return True
    return False


def is_laser_source_row(catalog_row: Optional[Mapping[str, Any]]) -> bool:
    return isinstance(catalog_row, Mapping) and str(catalog_row.get("type") or "") == "LASER_SOURCE"


def find_default_camera_tag(catalog_map: Mapping[str, Any]) -> Optional[str]:
    """First catalog camera tag suitable for ensemble centroid capture."""
    candidates: list[tuple[int, int, str]] = []
    for tag_id, row in catalog_map.items():
        if not isinstance(row, Mapping):
            continue
        if not is_camera_capable_row(row):
            continue
        row_dict = dict(row) if not isinstance(row, dict) else row
        cam_id = resolve_cam_id_for_tag(row_dict)
        type_rank = 0 if str(row_dict.get("type") or "") == "OPTICAL_CAMERA" else 1
        cam_rank = int(cam_id) if cam_id is not None else 999
        candidates.append((type_rank, cam_rank, str(tag_id)))
    if not candidates:
        return None
    candidates.sort()
    return candidates[0][2]


def measurable_field_from_path(path: Optional[str]) -> Optional[str]:
    if not path or not str(path).startswith("measurables."):
        return None
    field = str(path).split(".", 1)[1].strip()
    return field or None


def resolve_centroid_capture_tag(
    *,
    catalog_map: Optional[Mapping[str, Any]],
    term: ObjectiveTermSpec,
) -> str:
    """
    Tag whose camera stream supplies ``derived_centroid`` measurements.

    Mirrors may reference ``measurables.camera_image`` in the objective while
    capture runs on a bench ``OPTICAL_CAMERA`` (e.g. ``tag_22``).
    """
    src = term.source
    tag_id = str(src.tag_id)
    row = (catalog_map or {}).get(tag_id) if catalog_map is not None else None
    if is_camera_capable_row(row):
        return tag_id
    if catalog_map:
        fallback = find_default_camera_tag(catalog_map)
        if fallback:
            return fallback
    return tag_id


@dataclass(frozen=True)
class ObjectiveTermPlan:
    term_id: str
    kind: str
    source_tag_id: str
    capture_tag_id: Optional[str] = None
    measurable_field: Optional[str] = None
    metric: str = ""
    kernel_id: Optional[str] = None


def plan_objective_term(
    term: ObjectiveTermSpec,
    *,
    catalog_map: Optional[Mapping[str, Any]] = None,
) -> ObjectiveTermPlan:
    kind = term.source.kind
    source_tag = str(term.source.tag_id)
    if kind == "derived_centroid":
        return ObjectiveTermPlan(
            term_id=term.id,
            kind=kind,
            source_tag_id=source_tag,
            capture_tag_id=resolve_centroid_capture_tag(catalog_map=catalog_map, term=term),
            measurable_field="camera_image",
            metric=term.metric,
        )
    if kind in ("torchscript_scalar", "torchscript_features"):
        # Capture on the source tag if it is a camera; else resolve like centroid.
        capture_tag = source_tag
        row = (catalog_map or {}).get(source_tag) if catalog_map is not None else None
        if catalog_map is not None and not is_camera_capable_row(row):
            capture_tag = resolve_centroid_capture_tag(catalog_map=catalog_map, term=term)
        return ObjectiveTermPlan(
            term_id=term.id,
            kind=kind,
            source_tag_id=source_tag,
            capture_tag_id=capture_tag,
            measurable_field="torchscript_score",
            metric=term.metric,
            kernel_id=str(term.source.kernel_id or "").strip() or None,
        )
    field = measurable_field_from_path(term.source.path)
    return ObjectiveTermPlan(
        term_id=term.id,
        kind=kind,
        source_tag_id=source_tag,
        measurable_field=field,
        metric=term.metric,
    )


def plan_objective_terms(
    objective: ObjectiveSpec,
    *,
    catalog_map: Optional[Mapping[str, Any]] = None,
) -> list[ObjectiveTermPlan]:
    return [plan_objective_term(term, catalog_map=catalog_map) for term in objective.terms]


def read_scalar_from_component_state(
    state: Mapping[str, Any],
    tag_id: str,
    field: str,
) -> Optional[float]:
    entry = component_entry(state, tag_id)
    if entry is None:
        return None
    sc = entry.get("statecontrol") if isinstance(entry, dict) else None
    meas = sc.get("measurables") if isinstance(sc, dict) else None
    if not isinstance(meas, dict):
        return None
    raw = meas.get(field)
    if isinstance(raw, dict):
        # Tensor-native envelope: ``{"dtype", "domain", "data": <scalar>, ...}``.
        if "data" in raw and "dtype" in raw and "domain" in raw:
            data = raw.get("data")
            if isinstance(data, (int, float)):
                return float(data)
        for key in ("scalar", "value", "power", "score", "mw"):
            if key in raw:
                try:
                    return float(raw[key])
                except (TypeError, ValueError):
                    continue
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def read_laser_power_readback_mw(
    communicator: Any,
    tag_id: str,
    state: Mapping[str, Any],
) -> Optional[float]:
    hook = getattr(communicator, "_hardware_read_laser_output_power_mw", None)
    if callable(hook):
        try:
            val = hook(tag_id)
        except Exception:
            val = None
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                pass
    return read_scalar_from_component_state(state, tag_id, "output_power_readback_mw")


def collect_objective_measurements(
    objective: ObjectiveSpec,
    *,
    state: Mapping[str, Any],
    catalog_map: Optional[Mapping[str, Any]] = None,
    capture_bgr_for_tag: Callable[[str], Any],
    read_scalar: Optional[Callable[[str, str], Optional[float]]] = None,
    allow_image_scalar_fallback: bool = True,
) -> Dict[str, Dict[str, Any]]:
    """
    Build per-term measurement dicts for :func:`evaluate_weighted_sum`.

    ``capture_bgr_for_tag`` is invoked once per distinct capture tag per eval.
    """
    plans = plan_objective_terms(objective, catalog_map=catalog_map)
    capture_cache: Dict[str, Any] = {}
    out: Dict[str, Dict[str, Any]] = {}

    def _bgr_for(tag: str) -> Any:
        if tag not in capture_cache:
            capture_cache[tag] = capture_bgr_for_tag(tag)
        return capture_cache[tag]

    for plan in plans:
        if plan.kind == "derived_centroid":
            capture_tag = plan.capture_tag_id or plan.source_tag_id
            bgr = _bgr_for(capture_tag)
            from lab_model.execution.optimization.metrics.image_features import compute_beam_centroid_px

            centroid = compute_beam_centroid_px(bgr)
            if centroid is None:
                out[plan.term_id] = {
                    "centroid_x": float("nan"),
                    "centroid_y": float("nan"),
                }
            else:
                out[plan.term_id] = {
                    "centroid_x": centroid[0],
                    "centroid_y": centroid[1],
                }
            continue

        if plan.kind in ("torchscript_scalar", "torchscript_features"):
            capture_tag = plan.capture_tag_id or plan.source_tag_id
            bgr = _bgr_for(capture_tag)
            kernel_id = plan.kernel_id or ""
            from lab_model.execution.optimization.kernels.torchscript_runtime import (
                get_manifest_entry,
                run_torchscript_output,
            )

            if bgr is None or not kernel_id:
                if plan.kind == "torchscript_features":
                    out[plan.term_id] = {"features": [], "scalar": float("nan")}
                else:
                    out[plan.term_id] = {"scalar": float("nan")}
                continue
            kind, value = run_torchscript_output(kernel_id, bgr)
            entry = get_manifest_entry(kernel_id) or {}
            names = list(entry.get("feature_names") or [])
            if kind == "features" or plan.kind == "torchscript_features":
                feats = list(value) if kind == "features" else [float(value)]
                out[plan.term_id] = {
                    "features": feats,
                    "feature_names": names,
                    "scalar": float(feats[0]) if feats else float("nan"),
                }
            else:
                out[plan.term_id] = {"scalar": float(value)}
            continue

        field = plan.measurable_field or ""
        scalar: Optional[float] = None
        if read_scalar is not None and field:
            scalar = read_scalar(plan.source_tag_id, field)
        elif field:
            if field == "output_power_readback_mw":
                scalar = read_scalar_from_component_state(state, plan.source_tag_id, field)
            else:
                scalar = read_scalar_from_component_state(state, plan.source_tag_id, field)

        if (
            scalar is None
            and allow_image_scalar_fallback
            and field in IMAGE_FALLBACK_SCALAR_FIELDS
        ):
            capture_tag = resolve_centroid_capture_tag(
                catalog_map=catalog_map,
                term=next(t for t in objective.terms if t.id == plan.term_id),
            )
            bgr = _bgr_for(capture_tag)
            from lab_model.execution.optimization.metrics.image_features import compute_beam_power_scalar

            scalar = compute_beam_power_scalar(bgr)

        if scalar is None and field in HARDWARE_SCALAR_FIELDS:
            scalar = 0.0

        out[plan.term_id] = {
            "scalar": float(scalar if scalar is not None else 0.0),
        }

    return out


__all__ = [
    "HARDWARE_SCALAR_FIELDS",
    "IMAGE_FALLBACK_SCALAR_FIELDS",
    "ObjectiveTermPlan",
    "collect_objective_measurements",
    "find_default_camera_tag",
    "is_camera_capable_row",
    "is_laser_source_row",
    "measurable_field_from_path",
    "plan_objective_term",
    "plan_objective_terms",
    "read_laser_power_readback_mw",
    "read_scalar_from_component_state",
    "resolve_centroid_capture_tag",
]
