"""Approved-library TorchScript loader for closed-loop edge eval.

Sandbox lite:
- Artifacts resolve only under allowlisted roots (``schemas/kernels`` and
  optional ``{lab_view}/kernels``).
- Load via ``torch.jit.load`` only (not pickle ``torch.load``).
- Inference on CPU under ``torch.inference_mode()``.
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

_LOCK = threading.RLock()
_MODULE_CACHE: Dict[str, Any] = {}
_MANIFEST_CACHE: Dict[str, List[Dict[str, Any]]] = {}


def _repo_root() -> Path:
    # backend/lab_model/optimization/kernels/ → repo root
    return Path(__file__).resolve().parents[4]


def _resolve_lab_view_path(lab_view_path: Optional[str] = None) -> Optional[str]:
    if lab_view_path:
        return lab_view_path
    try:
        from lab_communicator.shared.lab_view_config import get_lab_view_paths_optional

        paths = get_lab_view_paths_optional()
        if paths is not None:
            return str(paths.root_dir)
    except Exception:
        return None
    return None


def default_kernels_roots(
    *,
    lab_view_path: Optional[str] = None,
    extra_roots: Optional[Sequence[Path]] = None,
) -> List[Path]:
    """Allowlisted directories that may contain TorchScript artifacts.

    Resolution order (later overrides earlier on id collision):
    schemas/kernels → {lab_view}/kernels → session/job extra roots.
    """
    roots: List[Path] = [_repo_root() / "schemas" / "kernels"]
    lv = _resolve_lab_view_path(lab_view_path)
    if lv:
        roots.append(Path(lv) / "kernels")
    # Active session/job roots (set by session_store)
    try:
        from .session_store import get_extra_kernels_roots

        roots.extend(get_extra_kernels_roots())
    except Exception:
        pass
    if extra_roots:
        roots.extend(Path(r) for r in extra_roots)
    # Deduplicate while preserving order
    seen: set[str] = set()
    out: List[Path] = []
    for root in roots:
        try:
            key = str(root.resolve())
        except OSError:
            key = str(root)
        if key in seen:
            continue
        seen.add(key)
        out.append(root)
    return out


def _manifest_cache_key(*, lab_view_path: Optional[str] = None) -> str:
    parts: List[str] = []
    for root in default_kernels_roots(lab_view_path=lab_view_path):
        try:
            parts.append(str(root.resolve()))
        except OSError:
            parts.append(str(root))
    return "|".join(parts)


def load_manifest_entries(
    *,
    lab_view_path: Optional[str] = None,
    force: bool = False,
) -> List[Dict[str, Any]]:
    """Load and merge kernel manifests from allowlisted roots (later overrides)."""
    cache_key = _manifest_cache_key(lab_view_path=lab_view_path)
    with _LOCK:
        if not force and cache_key in _MANIFEST_CACHE:
            return list(_MANIFEST_CACHE[cache_key])

    by_id: Dict[str, Dict[str, Any]] = {}
    for root in default_kernels_roots(lab_view_path=lab_view_path):
        manifest_path = root / "manifest.json"
        if not manifest_path.is_file():
            continue
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        rows = payload.get("kernels") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            continue
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            kid = str(raw.get("id") or "").strip()
            if not kid:
                continue
            entry = dict(raw)
            entry["id"] = kid
            entry["_root"] = str(root)
            entry["runtime"] = str(entry.get("runtime") or "torchscript")
            by_id[kid] = entry

    merged = sorted(by_id.values(), key=lambda r: str(r["id"]))
    with _LOCK:
        _MANIFEST_CACHE[cache_key] = merged
    return list(merged)


def get_manifest_entry(
    kernel_id: str,
    *,
    lab_view_path: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    kid = kernel_id.strip()
    for entry in load_manifest_entries(lab_view_path=lab_view_path):
        if entry.get("id") == kid:
            return entry
    return None


def resolve_artifact_path(
    entry: Mapping[str, Any],
    *,
    lab_view_path: Optional[str] = None,
) -> Path:
    """Resolve ``artifact`` filename under the entry's root (path-escape safe)."""
    artifact = str(entry.get("artifact") or "").strip()
    if not artifact or os.path.isabs(artifact) or ".." in Path(artifact).parts:
        raise FileNotFoundError(
            f"kernel {entry.get('id')!r}: invalid artifact path {artifact!r}"
        )
    root = Path(str(entry.get("_root") or "")).resolve()
    allowed = {r.resolve() for r in default_kernels_roots(lab_view_path=lab_view_path)}
    if root not in allowed and not any(
        _is_relative_to(root, a) for a in allowed
    ):
        # Entry root must be one of the allowlisted dirs
        if root not in {r.resolve() for r in default_kernels_roots(lab_view_path=lab_view_path) if r.exists()}:
            # Still allow if root equals a configured root even if missing
            configured = {str(r.resolve()) for r in default_kernels_roots(lab_view_path=lab_view_path)}
            if str(root) not in configured:
                raise FileNotFoundError(
                    f"kernel {entry.get('id')!r}: artifact root not allowlisted"
                )
    path = (root / artifact).resolve()
    if not _is_relative_to(path, root):
        raise FileNotFoundError(
            f"kernel {entry.get('id')!r}: artifact escapes allowlisted root"
        )
    if not path.is_file():
        raise FileNotFoundError(
            f"kernel {entry.get('id')!r}: artifact missing at {path}"
        )
    return path


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def torch_available() -> bool:
    try:
        import torch  # noqa: F401

        return True
    except ImportError:
        return False


def load_torchscript_module(
    kernel_id: str,
    *,
    lab_view_path: Optional[str] = None,
    force_reload: bool = False,
) -> Any:
    """Load and cache a TorchScript module by approved ``kernel_id``."""
    entry = get_manifest_entry(kernel_id, lab_view_path=lab_view_path)
    if entry is None:
        raise KeyError(f"unknown TorchScript kernel {kernel_id!r}")
    if str(entry.get("runtime") or "") != "torchscript":
        raise ValueError(f"kernel {kernel_id!r} runtime is not torchscript")
    if not torch_available():
        raise ImportError(
            "PyTorch is required for TorchScript kernels "
            f"({kernel_id!r}); install torch or remove torchscript_scalar terms"
        )

    import torch

    path = resolve_artifact_path(entry, lab_view_path=lab_view_path)
    cache_key = f"{kernel_id}::{path}"
    with _LOCK:
        if not force_reload and cache_key in _MODULE_CACHE:
            return _MODULE_CACHE[cache_key]
        module = torch.jit.load(str(path), map_location="cpu")
        module.eval()
        _MODULE_CACHE[cache_key] = module
        return module


def bgr_uint8_to_nchw_float(bgr: Any) -> Any:
    """Convert OpenCV-style HxWxC uint8 BGR → float32 CHW in [0, 1]."""
    import numpy as np
    import torch

    if bgr is None:
        raise ValueError("image is None")
    arr = np.asarray(bgr)
    if arr.ndim != 3 or arr.shape[2] not in (1, 3, 4):
        raise ValueError(f"expected HxWxC image, got shape {arr.shape}")
    if arr.shape[2] == 4:
        arr = arr[:, :, :3]
    if arr.shape[2] == 1:
        arr = np.repeat(arr, 3, axis=2)
    # BGR → RGB for conventional CHW models
    rgb = arr[:, :, ::-1].copy()
    chw = np.transpose(rgb.astype(np.float32) / 255.0, (2, 0, 1))
    return torch.from_numpy(chw)


def run_torchscript_output(
    kernel_id: str,
    bgr: Any,
    *,
    lab_view_path: Optional[str] = None,
) -> Tuple[str, Any]:
    """Run TorchScript forward; return ``(kind, value)`` where kind is scalar|features."""
    import torch

    entry = get_manifest_entry(kernel_id, lab_view_path=lab_view_path) or {}
    declared = str(entry.get("output_kind") or "").strip().lower()
    module = load_torchscript_module(kernel_id, lab_view_path=lab_view_path)
    tensor = bgr_uint8_to_nchw_float(bgr)
    with torch.inference_mode():
        out = module(tensor)

    # Prefer declared kind; otherwise infer from tensor shape.
    if declared == "features" or (
        not declared and isinstance(out, torch.Tensor) and out.numel() > 1
    ):
        return "features", _as_python_features(out)
    if declared == "scalar" or declared == "" or declared is None:
        # Also accept 1-element feature vectors as scalar when declared scalar
        if isinstance(out, torch.Tensor) and out.numel() > 1 and declared == "scalar":
            raise ValueError(
                f"kernel {kernel_id!r} declared scalar but returned shape {tuple(out.shape)}"
            )
        return "scalar", _as_python_float(out)
    return "features", _as_python_features(out)


def run_torchscript_scalar(
    kernel_id: str,
    bgr: Any,
    *,
    lab_view_path: Optional[str] = None,
) -> float:
    """Capture-frame → TorchScript ``forward`` → Python float scalar."""
    kind, value = run_torchscript_output(
        kernel_id, bgr, lab_view_path=lab_view_path
    )
    if kind != "scalar":
        raise ValueError(
            f"kernel {kernel_id!r} returned features; use run_torchscript_output"
        )
    return float(value)


def run_torchscript_features(
    kernel_id: str,
    bgr: Any,
    *,
    lab_view_path: Optional[str] = None,
) -> List[float]:
    """Capture-frame → TorchScript ``forward`` → 1D float feature vector."""
    kind, value = run_torchscript_output(
        kernel_id, bgr, lab_view_path=lab_view_path
    )
    if kind == "scalar":
        return [float(value)]
    return list(value)


def _as_python_float(out: Any) -> float:
    import torch

    if isinstance(out, torch.Tensor):
        if out.numel() != 1:
            raise ValueError(
                f"TorchScript output must be a scalar tensor; got shape {tuple(out.shape)}"
            )
        return float(out.detach().cpu().reshape(()).item())
    if isinstance(out, (int, float)):
        return float(out)
    if isinstance(out, (list, tuple)) and len(out) == 1:
        return _as_python_float(out[0])
    if isinstance(out, dict):
        for key in ("score", "scalar", "loss", "value"):
            if key in out:
                return _as_python_float(out[key])
    raise ValueError(f"unsupported TorchScript output type: {type(out)!r}")


def _as_python_features(out: Any) -> List[float]:
    import torch

    if isinstance(out, torch.Tensor):
        flat = out.detach().cpu().reshape(-1)
        if flat.numel() < 1:
            raise ValueError("TorchScript features tensor is empty")
        return [float(x) for x in flat.tolist()]
    if isinstance(out, (list, tuple)):
        if not out:
            raise ValueError("TorchScript features list is empty")
        return [float(x) for x in out]
    if isinstance(out, dict):
        for key in ("features", "vector", "values"):
            if key in out:
                return _as_python_features(out[key])
    raise ValueError(f"unsupported TorchScript features type: {type(out)!r}")


def clear_torchscript_caches() -> None:
    with _LOCK:
        _MODULE_CACHE.clear()
        _MANIFEST_CACHE.clear()


@dataclass(frozen=True)
class TorchScriptKernelInfo:
    id: str
    label: str
    description: str
    backend: str
    artifact: str
    artifact_path: Optional[str]
    artifact_present: bool
    runtime: str
    inputs: Sequence[Mapping[str, Any]]
    outputs: Sequence[Mapping[str, Any]]
    hooks: Sequence[str]
    phase: str = "production"
    output_kind: str = "scalar"
    feature_names: Sequence[str] = ()
    scope: str = "catalog"
    digest: Optional[str] = None

    def to_api_dict(self) -> Dict[str, Any]:
        row = {
            "id": self.id,
            "label": self.label,
            "description": self.description,
            "backend": self.backend,
            "phase": self.phase,
            "hooks": list(self.hooks),
            "runtime": self.runtime,
            "artifact": self.artifact,
            "artifact_path": self.artifact_path,
            "artifact_present": self.artifact_present,
            "inputs": [dict(x) for x in self.inputs],
            "outputs": [dict(x) for x in self.outputs],
            "output_kind": self.output_kind,
            "feature_names": list(self.feature_names),
            "scope": self.scope,
        }
        if self.digest:
            row["digest"] = self.digest
        return row


def list_torchscript_kernel_infos(
    *,
    lab_view_path: Optional[str] = None,
    backend: Optional[str] = None,
) -> List[TorchScriptKernelInfo]:
    rows: List[TorchScriptKernelInfo] = []
    for entry in load_manifest_entries(lab_view_path=lab_view_path):
        be = str(entry.get("backend") or "any")
        if backend:
            tag = backend.strip().lower()
            if be not in {tag, "any"}:
                continue
        artifact = str(entry.get("artifact") or "")
        present = False
        path_str: Optional[str] = None
        try:
            path = resolve_artifact_path(entry, lab_view_path=lab_view_path)
            path_str = str(path)
            present = True
        except FileNotFoundError:
            root = entry.get("_root")
            if root and artifact:
                path_str = str(Path(str(root)) / artifact)
        names = entry.get("feature_names") or []
        if not isinstance(names, list):
            names = []
        scope = str(entry.get("scope") or ("session" if str(entry["id"]).startswith("session.") else "catalog"))
        rows.append(
            TorchScriptKernelInfo(
                id=str(entry["id"]),
                label=str(entry.get("label") or entry["id"]),
                description=str(entry.get("description") or ""),
                backend=be,
                artifact=artifact,
                artifact_path=path_str,
                artifact_present=present,
                runtime=str(entry.get("runtime") or "torchscript"),
                inputs=list(entry.get("inputs") or []),
                outputs=list(entry.get("outputs") or []),
                hooks=tuple(entry.get("hooks") or ("evaluate", "capture")),
                phase=str(entry.get("phase") or "production"),
                output_kind=str(entry.get("output_kind") or "scalar"),
                feature_names=tuple(str(n) for n in names),
                scope=scope,
                digest=str(entry["digest"]) if entry.get("digest") else None,
            )
        )
    return rows


def preflight_torchscript_kernel(
    kernel_id: str,
    *,
    lab_view_path: Optional[str] = None,
) -> None:
    """Raise ``ValueError`` / ``ImportError`` / ``FileNotFoundError`` if unusable."""
    entry = get_manifest_entry(kernel_id, lab_view_path=lab_view_path)
    if entry is None:
        raise KeyError(f"unknown TorchScript kernel {kernel_id!r}")
    if not torch_available():
        raise ImportError(
            f"PyTorch required for TorchScript kernel {kernel_id!r}"
        )
    resolve_artifact_path(entry, lab_view_path=lab_view_path)
    # Touch-load to catch corrupt artifacts early
    load_torchscript_module(kernel_id, lab_view_path=lab_view_path)


__all__ = [
    "TorchScriptKernelInfo",
    "bgr_uint8_to_nchw_float",
    "clear_torchscript_caches",
    "default_kernels_roots",
    "get_manifest_entry",
    "list_torchscript_kernel_infos",
    "load_manifest_entries",
    "load_torchscript_module",
    "preflight_torchscript_kernel",
    "resolve_artifact_path",
    "run_torchscript_features",
    "run_torchscript_output",
    "run_torchscript_scalar",
    "torch_available",
]
