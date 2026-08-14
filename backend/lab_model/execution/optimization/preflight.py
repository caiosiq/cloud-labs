"""Pre-flight validation for ensemble OPTIMIZE before session lock."""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Set

from pydantic import ValidationError

from lab_model.language.domain.component import is_live_feed_active
from .errors import EnsemblePreflightError, PathResolveError
from .metrics.registry import METRIC_REGISTRY
from .objective_measurements import (
    is_camera_capable_row,
    is_laser_source_row,
    measurable_field_from_path,
    resolve_centroid_capture_tag,
)
from .paths import VariablePathResolver, component_entry
from .spec import ObjectiveSpec, ObjectiveTermSpec, OptimizeEnsembleParameters

# ``OptimizeParameters`` (legacy OPTIMIZE body) defaults ``strategy`` to
# ``NEWTON``; that key must not reach ``OptimizeEnsembleParameters`` (extra=forbid).
# ``pipeline`` / ``kernel_packages`` are edge-facing extras compiled alongside the IR.
_LEGACY_ENSEMBLE_NOISE_KEYS = frozenset({"strategy", "pipeline", "kernel_packages"})


def strip_legacy_optimize_fields(raw: Mapping[str, Any]) -> Dict[str, Any]:
    """Drop legacy single-tag keys accidentally carried on ensemble payloads."""
    data = dict(raw)
    for key in _LEGACY_ENSEMBLE_NOISE_KEYS:
        data.pop(key, None)
    return data


def parse_ensemble_parameters(raw: Mapping[str, Any]) -> OptimizeEnsembleParameters:
    """Validate JSON shape; raises ``ValidationError`` on schema failure."""
    return OptimizeEnsembleParameters.model_validate(strip_legacy_optimize_fields(raw))


def _measurables_keys(state: Mapping[str, Any], tag_id: str) -> set[str]:
    entry = component_entry(state, tag_id)
    if entry is None:
        return set()
    sc = entry.get("statecontrol") or {}
    meas = sc.get("measurables") if isinstance(sc, dict) else None
    if not isinstance(meas, dict):
        return set()
    return set(meas.keys())


def preflight_objective_term(
    state: Mapping[str, Any],
    term: ObjectiveTermSpec,
    *,
    catalog_map: Optional[Mapping[str, Any]] = None,
    strict_real_objectives: bool = False,
    edge_kernel_ids: Optional[Set[str]] = None,
) -> None:
    """Validate one compiled objective term against live state and metric registry.

    When ``edge_kernel_ids`` is set (remote edge catalog), TorchScript terms are
    checked against that set and coordinator ``ensure_torchscript_ready`` is skipped.
    """
    if term.metric not in METRIC_REGISTRY:
        raise EnsemblePreflightError(
            message="objective term references unknown metric",
            errors=[
                {
                    "term_id": term.id,
                    "metric": term.metric,
                    "reason": f"unknown metric {term.metric!r}; registered: {sorted(METRIC_REGISTRY)}",
                }
            ],
        )

    src = term.source
    tag_id = src.tag_id
    if component_entry(state, tag_id) is None:
        raise EnsemblePreflightError(
            message="objective source tag not found on bench",
            errors=[
                PathResolveError(
                    path=f"objective.terms.{term.id}.source",
                    tag_id=tag_id,
                    reason="tag_id not in runtime components",
                ).as_dict()
            ],
        )

    kind = src.kind
    if kind == "measurable_scalar":
        path = (src.path or "").strip()
        if not path.startswith("measurables."):
            raise EnsemblePreflightError(
                message="objective measurable_scalar path invalid",
                errors=[
                    PathResolveError(
                        path=path or f"objective.terms.{term.id}.source.path",
                        tag_id=tag_id,
                        reason="path must start with measurables.",
                    ).as_dict()
                ],
            )
        field = measurable_field_from_path(path) or ""
        if not field:
            raise EnsemblePreflightError(
                message="objective measurable_scalar path invalid",
                errors=[
                    PathResolveError(
                        path=path,
                        tag_id=tag_id,
                        reason="missing measurable field after measurables.",
                    ).as_dict()
                ],
            )
        known = _measurables_keys(state, tag_id)
        if field not in known:
            raise EnsemblePreflightError(
                message="objective measurable field not declared on component",
                errors=[
                    PathResolveError(
                        path=path,
                        tag_id=tag_id,
                        reason=f"measurables.{field} not on component (known: {sorted(known)})",
                    ).as_dict()
                ],
            )
        if strict_real_objectives and catalog_map is not None:
            row = catalog_map.get(tag_id)
            if field == "output_power_readback_mw" and not is_laser_source_row(row):
                raise EnsemblePreflightError(
                    message="output_power_readback_mw requires a LASER_SOURCE tag on real bench",
                    errors=[
                        {
                            "term_id": term.id,
                            "tag_id": tag_id,
                            "field": field,
                            "reason": "point this term at a laser catalog row with power readback",
                        }
                    ],
                )
        return

    if kind == "derived_centroid":
        from_path = getattr(src, "from_", None) or getattr(src, "from", None)
        if from_path != "measurables.camera_image":
            raise EnsemblePreflightError(
                message="derived_centroid source invalid",
                errors=[
                    {
                        "term_id": term.id,
                        "tag_id": tag_id,
                        "reason": "derived_centroid requires from=measurables.camera_image",
                    }
                ],
            )
        known = _measurables_keys(state, tag_id)
        if "camera_image" not in known and strict_real_objectives:
            # Mirrors keep a placeholder camera_image key; catalog check is authoritative.
            pass
        capture_tag = resolve_centroid_capture_tag(catalog_map=catalog_map, term=term)
        if strict_real_objectives and catalog_map is not None:
            capture_row = catalog_map.get(capture_tag)
            if not is_camera_capable_row(capture_row):
                raise EnsemblePreflightError(
                    message="real bench cannot capture centroid for objective term",
                    errors=[
                        {
                            "term_id": term.id,
                            "tag_id": tag_id,
                            "capture_tag_id": capture_tag,
                            "reason": (
                                "no OPTICAL_CAMERA with hardware binding found; "
                                "add a camera to the catalog or point the term at a camera tag"
                            ),
                        }
                    ],
                )
        if src.target_px is None:
            raise EnsemblePreflightError(
                message="derived_centroid missing target_px",
                errors=[
                    {
                        "term_id": term.id,
                        "tag_id": tag_id,
                        "reason": "target_px required for rms_distance_px metric",
                    }
                ],
            )
        return

    if kind in ("torchscript_scalar", "torchscript_features"):
        kernel_id = str(getattr(src, "kernel_id", None) or "").strip()
        if not kernel_id:
            raise EnsemblePreflightError(
                message=f"{kind} missing kernel_id",
                errors=[
                    {
                        "term_id": term.id,
                        "tag_id": tag_id,
                        "reason": "source.kernel_id is required",
                    }
                ],
            )
        from_path = getattr(src, "from_", None) or getattr(src, "from", None) or src.path
        if from_path and from_path != "measurables.camera_image":
            # v1 only supports camera_image inputs
            raise EnsemblePreflightError(
                message=f"{kind} input not supported",
                errors=[
                    {
                        "term_id": term.id,
                        "tag_id": tag_id,
                        "reason": "v1 requires from=measurables.camera_image",
                    }
                ],
            )
        capture_tag = resolve_centroid_capture_tag(catalog_map=catalog_map, term=term)
        if strict_real_objectives and catalog_map is not None:
            capture_row = catalog_map.get(capture_tag)
            if not is_camera_capable_row(capture_row):
                raise EnsemblePreflightError(
                    message="real bench cannot capture image for TorchScript term",
                    errors=[
                        {
                            "term_id": term.id,
                            "tag_id": tag_id,
                            "capture_tag_id": capture_tag,
                            "reason": "no camera-capable catalog row for TorchScript input",
                        }
                    ],
                )
        try:
            from lab_model.execution.optimization.kernels import ensure_torchscript_ready
            from lab_model.execution.optimization.kernels.torchscript_runtime import get_manifest_entry

            if edge_kernel_ids is not None:
                # Remote edge: id must appear in the edge catalog (or be session.*).
                if kernel_id not in edge_kernel_ids and not (
                    kernel_id.startswith("session.") and kernel_id in edge_kernel_ids
                ):
                    if kernel_id not in edge_kernel_ids:
                        raise EnsemblePreflightError(
                            message="unknown TorchScript kernel on edge catalog",
                            errors=[
                                {
                                    "term_id": term.id,
                                    "kernel_id": kernel_id,
                                    "reason": (
                                        f"kernel {kernel_id!r} not listed by active edge "
                                        f"GET /kernels (known: {sorted(edge_kernel_ids)[:20]})"
                                    ),
                                }
                            ],
                        )
                # Skip coordinator ensure_torchscript_ready — artifacts live on the edge.
                return

            ensure_torchscript_ready(kernel_id)
            entry = get_manifest_entry(kernel_id) or {}
            declared = str(entry.get("output_kind") or "scalar").lower()
            if kind == "torchscript_features" and declared == "scalar":
                # Allow scalar kernels used as 1-feature vectors
                pass
            if kind == "torchscript_scalar" and declared == "features":
                raise EnsemblePreflightError(
                    message="kernel returns features but term kind is torchscript_scalar",
                    errors=[
                        {
                            "term_id": term.id,
                            "kernel_id": kernel_id,
                            "reason": "use kind=torchscript_features for vector kernels",
                        }
                    ],
                )
        except EnsemblePreflightError:
            raise
        except KeyError as exc:
            raise EnsemblePreflightError(
                message="unknown TorchScript kernel",
                errors=[
                    {
                        "term_id": term.id,
                        "kernel_id": kernel_id,
                        "reason": str(exc),
                    }
                ],
            ) from exc
        except ImportError as exc:
            raise EnsemblePreflightError(
                message="PyTorch required for TorchScript objective term",
                errors=[
                    {
                        "term_id": term.id,
                        "kernel_id": kernel_id,
                        "reason": str(exc),
                    }
                ],
            ) from exc
        except FileNotFoundError as exc:
            raise EnsemblePreflightError(
                message="TorchScript artifact missing",
                errors=[
                    {
                        "term_id": term.id,
                        "kernel_id": kernel_id,
                        "reason": str(exc),
                    }
                ],
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise EnsemblePreflightError(
                message="TorchScript kernel failed preflight",
                errors=[
                    {
                        "term_id": term.id,
                        "kernel_id": kernel_id,
                        "reason": str(exc),
                    }
                ],
            ) from exc
        return

    raise EnsemblePreflightError(
        message="objective source kind not supported",
        errors=[
            {
                "term_id": term.id,
                "tag_id": tag_id,
                "kind": kind,
                "reason": (
                    "supported kinds: measurable_scalar, derived_centroid, "
                    "torchscript_scalar"
                ),
            }
        ],
    )


def preflight_objective_sources(
    state: Mapping[str, Any],
    objective: ObjectiveSpec,
    *,
    catalog_map: Optional[Mapping[str, Any]] = None,
    strict_real_objectives: bool = False,
    edge_kernel_ids: Optional[Set[str]] = None,
) -> None:
    """Validate every compiled objective term."""
    for term in objective.terms:
        preflight_objective_term(
            state,
            term,
            catalog_map=catalog_map,
            strict_real_objectives=strict_real_objectives,
            edge_kernel_ids=edge_kernel_ids,
        )


def preflight_live_feed_conflicts(
    state: Mapping[str, Any],
    spec: OptimizeEnsembleParameters,
    *,
    catalog_map: Optional[Mapping[str, Any]] = None,
) -> None:
    """Refuse OPTIMIZE when a capture / camera objective tag still has live feed on.

    Live feed and one-shot / in-loop RECORD capture share the camera path —
    operators must End live feed (pop-out Hide is not enough) before optimize.
    """
    check_tags: set[str] = set()
    if spec.capture is not None:
        for step in spec.capture.before_each_eval or []:
            check_tags.add(str(step.tag_id))
    for term in spec.objective.terms:
        check_tags.add(str(term.source.tag_id))

    conflicts: list[Dict[str, Any]] = []
    for tid in sorted(check_tags):
        entry = component_entry(state, tid)
        if entry is None or not is_live_feed_active(entry):
            continue
        row = None
        if isinstance(catalog_map, Mapping):
            row = catalog_map.get(tid)
        # Without catalog, still refuse any live-scoped objective/capture tag.
        if row is not None and not is_camera_capable_row(row):
            continue
        conflicts.append(
            {
                "tag_id": tid,
                "reason": (
                    f"{tid} has live feed on — End live feed before OPTIMIZE "
                    f"(RECORD/capture blocked while streaming)"
                ),
            }
        )
    if conflicts:
        raise EnsemblePreflightError(
            message="live feed is active on an OPTIMIZE capture/camera tag",
            errors=conflicts,
        )


def preflight_ensemble(
    state: Mapping[str, Any],
    raw: Mapping[str, Any],
    *,
    catalog_map: Optional[Mapping[str, Any]] = None,
    strict_real_objectives: bool = False,
    edge_kernel_ids: Optional[Set[str]] = None,
) -> tuple[OptimizeEnsembleParameters, VariablePathResolver, Dict[str, float]]:
    """
    Parse spec and dry-run resolve every variable path on ``state``.

    Raises :class:`EnsemblePreflightError` with structured detail on failure.
    """
    cleaned = strip_legacy_optimize_fields(raw)
    try:
        spec = parse_ensemble_parameters(cleaned)
    except ValidationError as exc:
        raise EnsemblePreflightError(
            message="ensemble parameters failed schema validation",
            errors=list(exc.errors(include_url=False)),
        ) from exc

    try:
        resolver = VariablePathResolver.resolve(state, spec.variables, writable=False)
    except PathResolveError as exc:
        raise EnsemblePreflightError(
            message="ensemble variable path resolution failed",
            errors=[exc.as_dict()],
        ) from exc

    preflight_objective_sources(
        state,
        spec.objective,
        catalog_map=catalog_map,
        strict_real_objectives=strict_real_objectives,
        edge_kernel_ids=edge_kernel_ids,
    )
    preflight_live_feed_conflicts(state, spec, catalog_map=catalog_map)

    x0 = resolver.snapshot_x0()
    return spec, resolver, x0


def ensemble_scope_tag_ids(spec: OptimizeEnsembleParameters) -> list[str]:
    """Tags touched by variables or objective sources (for measurables nulling)."""
    tags: set[str] = set()
    for var in spec.variables:
        tags.add(var.tag_id)
        if var.touch_and_go is not None:
            tags.add(var.touch_and_go.gripper_tag)
            home = var.touch_and_go.safe_home_tag or var.touch_and_go.gripper_tag
            tags.add(home)
    for term in spec.objective.terms:
        tags.add(term.source.tag_id)
    if spec.capture is not None:
        for step in spec.capture.before_each_eval:
            tags.add(step.tag_id)
    return sorted(tags)


__all__ = [
    "ensemble_scope_tag_ids",
    "parse_ensemble_parameters",
    "preflight_ensemble",
    "preflight_live_feed_conflicts",
    "preflight_objective_sources",
    "preflight_objective_term",
    "strip_legacy_optimize_fields",
]
