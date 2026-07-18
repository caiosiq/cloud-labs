"""Write per-eval objective measurements into runtime measurables (edge sync)."""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from lab_model.execution.optimization.objective_measurements import (
    plan_objective_terms,
    resolve_centroid_capture_tag,
)
from lab_model.execution.optimization.spec import ObjectiveSpec


def sync_measurables_from_objective_eval(
    state: Dict[str, Any],
    objective: ObjectiveSpec,
    measurements: Mapping[str, Mapping[str, Any]],
    *,
    catalog_map: Optional[Mapping[str, Any]] = None,
    camera_images: Optional[Mapping[str, Mapping[str, Any]]] = None,
    state_lock: Any = None,
) -> Dict[str, Any]:
    """Mirror mock ``sync_measurables_from_eval`` for real (and shared) captures.

    Writes centroid / scalar receipts so Twin UI and SDK can observe live
    progress during ``OPTIMIZING`` without calling ``RECORD_MEASURABLES``.

    Returns a summary of tags touched (for tests / kernel telemetry).
    """
    plans = plan_objective_terms(objective, catalog_map=catalog_map)
    camera_images = camera_images or {}
    touched: Dict[str, Any] = {"tags": [], "fields": {}}

    def _apply() -> None:
        components = state.setdefault("components", {})
        if not isinstance(components, dict):
            return
        for plan in plans:
            term_meas = measurements.get(plan.term_id) or {}
            if plan.kind == "derived_centroid":
                tags = {plan.source_tag_id}
                capture = plan.capture_tag_id or plan.source_tag_id
                tags.add(capture)
                cx = term_meas.get("centroid_x")
                cy = term_meas.get("centroid_y")
                for tag in tags:
                    _write_fields(
                        components,
                        tag,
                        {
                            "centroid_x_px": _as_float(cx),
                            "centroid_y_px": _as_float(cy),
                        },
                        touched,
                    )
                continue

            field = plan.measurable_field
            if not field:
                continue
            scalar = term_meas.get("scalar")
            if scalar is None:
                scalar = term_meas.get("power")
            _write_fields(
                components,
                plan.source_tag_id,
                {field: _as_float(scalar)},
                touched,
            )

        for tag_id, meta in camera_images.items():
            if not isinstance(meta, Mapping):
                continue
            from lab_model.language.measurables.materialize import materialize_measurable

            tid = str(tag_id)
            envelope = materialize_measurable(tid, "camera_image", dict(meta)).to_api_dict()
            _write_fields(
                components,
                tid,
                {"camera_image": envelope},
                touched,
            )

    if state_lock is not None:
        with state_lock:
            _apply()
    else:
        _apply()
    return touched


def _as_float(value: Any) -> Any:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return value


def _write_fields(
    components: Dict[str, Any],
    tag_id: str,
    fields: Mapping[str, Any],
    touched: Dict[str, Any],
) -> None:
    entry = components.get(tag_id)
    if not isinstance(entry, dict):
        return
    sc = entry.setdefault("statecontrol", {})
    if not isinstance(sc, dict):
        return
    meas = sc.setdefault("measurables", {})
    if not isinstance(meas, dict):
        return
    from lab_model.language import measurables as _measurables  # noqa: F401 — register
    from lab_model.language.measurables.materialize import materialize_measurable
    from lab_model.language.measurables.registry import MEASURABLE_REGISTRY, is_tensor_envelope

    written = []
    for key, value in fields.items():
        if value is None:
            continue
        if key in MEASURABLE_REGISTRY and not is_tensor_envelope(value):
            meas[key] = materialize_measurable(tag_id, key, value).to_api_dict()
        else:
            meas[key] = value
        written.append(key)
    if written:
        if tag_id not in touched["tags"]:
            touched["tags"].append(tag_id)
        touched["fields"][tag_id] = list(
            dict.fromkeys([*(touched["fields"].get(tag_id) or []), *written])
        )


def capture_tags_for_objective(
    objective: ObjectiveSpec,
    *,
    catalog_map: Optional[Mapping[str, Any]] = None,
) -> list[str]:
    """Distinct camera tags that will be captured for this objective."""
    tags: list[str] = []
    for plan in plan_objective_terms(objective, catalog_map=catalog_map):
        if plan.kind == "derived_centroid":
            tag = plan.capture_tag_id or plan.source_tag_id
            if tag and tag not in tags:
                tags.append(tag)
            continue
        # Image-fallback scalars also capture (last_optimization_score).
        if plan.measurable_field == "last_optimization_score":
            term = next(t for t in objective.terms if t.id == plan.term_id)
            tag = resolve_centroid_capture_tag(catalog_map=catalog_map, term=term)
            if tag and tag not in tags:
                tags.append(tag)
        if plan.kind in ("torchscript_scalar", "torchscript_features"):
            tag = plan.capture_tag_id or plan.source_tag_id
            if tag and tag not in tags:
                tags.append(tag)
    return tags


__all__ = [
    "capture_tags_for_objective",
    "sync_measurables_from_objective_eval",
]
