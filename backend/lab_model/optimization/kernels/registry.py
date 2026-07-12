"""Edge kernel catalog — builtins + approved TorchScript manifests (Phase F).

Builtin hooks document ensemble paths. TorchScript models live under
``schemas/kernels/`` (and optional ``{lab_view}/kernels/``). Session packages
may be registered under a lease and referenced by ``session.*`` kernel ids.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set

from .torchscript_runtime import (
    get_manifest_entry,
    list_torchscript_kernel_infos,
    preflight_torchscript_kernel,
)


@dataclass(frozen=True)
class KernelDescriptor:
    """One registered edge kernel (builtin or TorchScript)."""

    id: str
    label: str
    description: str
    backend: str  # mock | real | any
    phase: str = "mock"
    hooks: Sequence[str] = ()
    runtime: Optional[str] = None
    artifact: Optional[str] = None
    artifact_path: Optional[str] = None
    artifact_present: Optional[bool] = None
    inputs: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    outputs: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    output_kind: Optional[str] = None
    feature_names: Sequence[str] = field(default_factory=tuple)
    scope: str = "catalog"
    digest: Optional[str] = None

    def to_api_dict(self) -> Dict[str, Any]:
        row: Dict[str, Any] = {
            "id": self.id,
            "label": self.label,
            "description": self.description,
            "backend": self.backend,
            "phase": self.phase,
            "hooks": list(self.hooks),
            "scope": self.scope,
        }
        if self.runtime:
            row["runtime"] = self.runtime
        if self.artifact is not None:
            row["artifact"] = self.artifact
        if self.artifact_path is not None:
            row["artifact_path"] = self.artifact_path
        if self.artifact_present is not None:
            row["artifact_present"] = self.artifact_present
        if self.inputs:
            row["inputs"] = [dict(x) for x in self.inputs]
        if self.outputs:
            row["outputs"] = [dict(x) for x in self.outputs]
        if self.output_kind:
            row["output_kind"] = self.output_kind
        if self.feature_names:
            row["feature_names"] = list(self.feature_names)
        if self.digest:
            row["digest"] = self.digest
        return row


_BUILTIN_KERNELS: Dict[str, KernelDescriptor] = {
    "ensemble.eval.block_cobyla": KernelDescriptor(
        id="ensemble.eval.block_cobyla",
        label="Ensemble block COBYLA",
        description="Default closed-loop optimizer kernel (session.py + communicator ensemble).",
        backend="any",
        phase="production",
        hooks=("evaluate", "apply", "progress"),
        runtime="builtin",
    ),
    "ensemble.eval.mock_landscape": KernelDescriptor(
        id="ensemble.eval.mock_landscape",
        label="Mock synthetic landscape",
        description=(
            "Mock-only coupled centroid + power landscape; writes measurables on each eval."
        ),
        backend="mock",
        phase="mock",
        hooks=("evaluate", "sync_measurables"),
        runtime="builtin",
    ),
    "ensemble.eval.image_features": KernelDescriptor(
        id="ensemble.eval.image_features",
        label="Live image features → loss",
        description=(
            "Real closed-loop path: capture camera PNG → NumPy/OpenCV centroid & power "
            "→ weighted_sum scalar loss → motor step. Default on real when no mock landscape."
        ),
        backend="real",
        phase="production",
        hooks=("evaluate", "capture", "sync_measurables"),
        runtime="builtin",
    ),
    "measurable.materialize.camera": KernelDescriptor(
        id="measurable.materialize.camera",
        label="Camera measurable materialize",
        description=(
            "On each ensemble eval, persist camera_image PNG metadata into runtime "
            "measurables (MeasurableTensor-compatible) for UI/SDK."
        ),
        backend="any",
        phase="production",
        hooks=("capture", "tensor", "evaluate", "sync_measurables"),
        runtime="builtin",
    ),
    "objective.compile.weighted_sum": KernelDescriptor(
        id="objective.compile.weighted_sum",
        label="Objective graph compiler",
        description="Phase E authoring → ObjectiveSpec + preflight validation.",
        backend="any",
        phase="production",
        hooks=("compile", "preflight"),
        runtime="builtin",
    ),
    "stabilization.settle": KernelDescriptor(
        id="stabilization.settle",
        label="Settle / stabilization wait",
        description="Post-move settle before capture (honors solver.settle_ms on real).",
        backend="real",
        phase="production",
        hooks=("settle_ms", "evaluate"),
        runtime="builtin",
    ),
}


def _merged_catalog(*, lab_view_path: Optional[str] = None) -> Dict[str, KernelDescriptor]:
    catalog = dict(_BUILTIN_KERNELS)
    for info in list_torchscript_kernel_infos(lab_view_path=lab_view_path):
        catalog[info.id] = KernelDescriptor(
            id=info.id,
            label=info.label,
            description=info.description,
            backend=info.backend,
            phase=info.phase,
            hooks=tuple(info.hooks),
            runtime=info.runtime,
            artifact=info.artifact,
            artifact_path=info.artifact_path,
            artifact_present=info.artifact_present,
            inputs=tuple(info.inputs),
            outputs=tuple(info.outputs),
            output_kind=info.output_kind,
            feature_names=tuple(info.feature_names),
            scope=info.scope,
            digest=info.digest,
        )
    return catalog


def list_kernels(
    *,
    backend: Optional[str] = None,
    lab_view_path: Optional[str] = None,
) -> List[KernelDescriptor]:
    """Return registered kernels (builtins + TorchScript manifests)."""
    rows = list(_merged_catalog(lab_view_path=lab_view_path).values())
    if backend:
        tag = backend.strip().lower()
        rows = [k for k in rows if k.backend in {tag, "any"}]
    return sorted(rows, key=lambda k: k.id)


def get_kernel(
    kernel_id: str,
    *,
    lab_view_path: Optional[str] = None,
) -> Optional[KernelDescriptor]:
    return _merged_catalog(lab_view_path=lab_view_path).get(kernel_id.strip())


def validate_kernel_ids(
    kernel_ids: Any,
    *,
    lab_view_path: Optional[str] = None,
    allow_unknown_session: bool = False,
    known_session_ids: Optional[Set[str]] = None,
) -> List[str]:
    """Normalize and validate optional kernels[] on job submit."""
    if kernel_ids is None:
        return []
    if not isinstance(kernel_ids, list):
        raise ValueError("kernels must be a list of kernel id strings")
    catalog = _merged_catalog(lab_view_path=lab_view_path)
    known_session = known_session_ids or set()
    out: List[str] = []
    for raw in kernel_ids:
        kid = str(raw).strip()
        if not kid:
            raise ValueError("kernels entries must be non-empty strings")
        if kid not in catalog:
            if kid.startswith("session.") and (
                allow_unknown_session or kid in known_session
            ):
                if kid not in out:
                    out.append(kid)
                continue
            known = ", ".join(sorted(catalog))
            raise ValueError(f"unknown kernel {kid!r}; known: {known}")
        if kid not in out:
            out.append(kid)
    return out


def apply_kernel_hooks(
    kernel_ids: Sequence[str],
    *,
    hook: str,
    context: Mapping[str, Any],
) -> Dict[str, Any]:
    """Dispatch registered kernel flags into ``context`` (mutable Mapping)."""
    notes: Dict[str, Any] = {"hook": hook, "kernels": list(kernel_ids), "applied": []}
    mutable = context if isinstance(context, dict) else dict(context)
    catalog = _merged_catalog(lab_view_path=mutable.get("lab_view_path"))

    for kid in kernel_ids:
        desc = catalog.get(kid) or _BUILTIN_KERNELS.get(kid)
        if desc is None:
            if get_manifest_entry(kid) is not None or kid.startswith("session."):
                notes["applied"].append(kid)
                mutable["use_torchscript"] = True
                mutable.setdefault("sync_measurables", True)
            continue
        if hook not in desc.hooks and hook != "evaluate":
            continue
        notes["applied"].append(kid)
        if kid == "measurable.materialize.camera":
            mutable["write_camera_image"] = True
            mutable["sync_measurables"] = True
        elif kid == "ensemble.eval.image_features":
            mutable["use_image_features"] = True
            mutable["sync_measurables"] = True
        elif kid == "ensemble.eval.mock_landscape":
            mutable["use_mock_landscape"] = True
            mutable["sync_measurables"] = True
        elif kid == "stabilization.settle":
            mutable["honor_settle_ms"] = True
        elif desc.runtime == "torchscript":
            mutable["use_torchscript"] = True
            mutable.setdefault("sync_measurables", True)

    if hook == "evaluate" and not notes["applied"]:
        backend = str(mutable.get("backend") or "").lower()
        if backend == "mock":
            mutable.setdefault("use_mock_landscape", True)
            mutable.setdefault("sync_measurables", True)
        elif backend == "real":
            mutable.setdefault("use_image_features", True)
            mutable.setdefault("sync_measurables", True)
            mutable.setdefault("write_camera_image", True)

    notes["flags"] = {
        k: mutable.get(k)
        for k in (
            "write_camera_image",
            "sync_measurables",
            "use_image_features",
            "use_mock_landscape",
            "use_torchscript",
            "honor_settle_ms",
        )
        if k in mutable
    }
    notes["context_keys"] = sorted(mutable.keys())
    return notes


def ensure_torchscript_ready(kernel_id: str, *, lab_view_path: Optional[str] = None) -> None:
    """Preflight helper used by ensemble objective validation."""
    preflight_torchscript_kernel(kernel_id, lab_view_path=lab_view_path)


__all__ = [
    "KernelDescriptor",
    "apply_kernel_hooks",
    "ensure_torchscript_ready",
    "get_kernel",
    "list_kernels",
    "validate_kernel_ids",
]
