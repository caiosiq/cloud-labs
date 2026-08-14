"""TorchScript kernel compiler/loader for edge optimization.

Loads from an edge-local ``kernels/manifest.json`` (+ artifacts). Digest
verification on provision. Does not import ``cloudlabs`` or ``lab_model``.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

_LOCK = threading.RLock()
_MODULE_CACHE: Dict[str, Any] = {}
_MANIFEST_CACHE: Dict[str, List[Dict[str, Any]]] = {}

MAX_ARTIFACT_BYTES = 5 * 1024 * 1024


class KernelError(Exception):
    """Kernel load / eval failure."""


def torch_available() -> bool:
    try:
        import torch  # noqa: F401

        return True
    except ImportError:
        return False


def default_kernels_dir(root: Optional[Path] = None) -> Path:
    env = os.environ.get("CLOUDLABS_EDGE_KERNELS_DIR", "").strip()
    if env:
        return Path(env)
    if root is not None:
        return Path(root)
    return Path.cwd() / "kernels"


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_manifest(kernels_dir: Path, *, force: bool = False) -> List[Dict[str, Any]]:
    key = str(kernels_dir.resolve()) if kernels_dir.exists() else str(kernels_dir)
    with _LOCK:
        if not force and key in _MANIFEST_CACHE:
            return list(_MANIFEST_CACHE[key])
    manifest_path = kernels_dir / "manifest.json"
    rows: List[Dict[str, Any]] = []
    if manifest_path.is_file():
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise KernelError(f"invalid kernels manifest at {manifest_path}: {exc}") from exc
        for raw in payload.get("kernels") or []:
            if not isinstance(raw, dict) or not raw.get("id"):
                continue
            entry = dict(raw)
            entry["_root"] = str(kernels_dir)
            artifact = str(entry.get("artifact") or f"{entry['id']}.pt")
            path = kernels_dir / artifact
            entry["artifact"] = artifact
            entry["artifact_path"] = str(path)
            entry["artifact_present"] = path.is_file()
            rows.append(entry)
    with _LOCK:
        _MANIFEST_CACHE[key] = rows
    return list(rows)


def get_manifest_entry(
    kernel_id: str,
    kernels_dir: Path,
) -> Optional[Dict[str, Any]]:
    kid = kernel_id.strip()
    for entry in load_manifest(kernels_dir):
        if entry.get("id") == kid:
            return entry
    return None


def list_kernels(kernels_dir: Path) -> List[Dict[str, Any]]:
    return load_manifest(kernels_dir)


def kernels_http_catalog(backend_id: str, kernels_dir: Path) -> Dict[str, Any]:
    """Shape for Edge Contract ``GET /kernels``."""
    rows: List[Dict[str, Any]] = []
    for raw in list_kernels(kernels_dir):
        if not isinstance(raw, dict) or not raw.get("id"):
            continue
        kid = str(raw["id"])
        rows.append(
            {
                "id": kid,
                "label": raw.get("label") or kid,
                "description": raw.get("description") or "",
                "runtime": raw.get("runtime") or "torchscript",
                "artifact": raw.get("artifact"),
                "artifact_present": bool(raw.get("artifact_present")),
                "output_kind": raw.get("output_kind"),
                "feature_names": list(raw.get("feature_names") or []),
                "digest": raw.get("digest"),
                "scope": raw.get("scope")
                or ("session" if kid.startswith("session.") else "catalog"),
            }
        )
    return {"backend_id": str(backend_id), "kernels": rows}


def resolve_artifact_path(entry: Mapping[str, Any], kernels_dir: Path) -> Path:
    artifact = str(entry.get("artifact") or "").strip()
    if not artifact or os.path.isabs(artifact) or ".." in Path(artifact).parts:
        raise KernelError(f"kernel {entry.get('id')!r}: invalid artifact {artifact!r}")
    root = Path(str(entry.get("_root") or kernels_dir)).resolve()
    path = (root / artifact).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise KernelError(f"kernel {entry.get('id')!r}: artifact escapes root") from exc
    if not path.is_file():
        raise KernelError(f"kernel {entry.get('id')!r}: artifact missing at {path}")
    return path


def clear_caches() -> None:
    with _LOCK:
        _MODULE_CACHE.clear()
        _MANIFEST_CACHE.clear()


def provision_bytes(
    kernel_id: str,
    data: bytes,
    *,
    kernels_dir: Path,
    digest: Optional[str] = None,
    output_kind: str = "scalar",
    feature_names: Optional[List[str]] = None,
    label: Optional[str] = None,
) -> Path:
    """Write a session/premade artifact under ``kernels_dir`` and refresh cache."""
    if not data:
        raise KernelError("empty artifact")
    if len(data) > MAX_ARTIFACT_BYTES:
        raise KernelError(f"artifact exceeds max size ({MAX_ARTIFACT_BYTES})")
    expected = (digest or "").strip().lower()
    if expected.startswith("sha256:"):
        expected = expected.split(":", 1)[1]
    actual = _sha256_hex(data)
    if expected and actual != expected:
        raise KernelError(f"digest mismatch: expected {expected}, got {actual}")

    kernels_dir.mkdir(parents=True, exist_ok=True)
    # Prefer filename from existing manifest entry when present.
    entry = get_manifest_entry(kernel_id, kernels_dir)
    artifact = str(entry.get("artifact") if entry else f"{kernel_id}.pt")
    path = kernels_dir / artifact
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)

    # Ensure a manifest row exists for session kernels.
    if entry is None:
        manifest_path = kernels_dir / "manifest.json"
        payload: Dict[str, Any]
        if manifest_path.is_file():
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        else:
            payload = {"schema_version": 1, "kernels": []}
        kind = (output_kind or "scalar").strip().lower()
        names = list(feature_names or [])
        row = {
            "id": kernel_id,
            "label": label or kernel_id,
            "artifact": artifact,
            "runtime": "torchscript",
            "output_kind": kind,
            "feature_names": names,
            "digest": f"sha256:{actual}",
            "scope": "session" if kernel_id.startswith("session.") else "catalog",
        }
        kernels = [k for k in (payload.get("kernels") or []) if k.get("id") != kernel_id]
        kernels.append(row)
        payload["kernels"] = kernels
        manifest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    clear_caches()
    return path


def load_module(kernel_id: str, kernels_dir: Path, *, force_reload: bool = False) -> Any:
    if not torch_available():
        raise KernelError(
            f"torchscript_execution unavailable: PyTorch is not installed ({kernel_id!r})"
        )
    import torch

    entry = get_manifest_entry(kernel_id, kernels_dir)
    if entry is None:
        # Fallback: bare {id}.pt without manifest row (legacy).
        path = kernels_dir / f"{kernel_id}.pt"
        if not path.is_file():
            raise KernelError(f"unknown kernel {kernel_id!r}")
    else:
        path = resolve_artifact_path(entry, kernels_dir)

    cache_key = f"{kernel_id}::{path}"
    with _LOCK:
        if not force_reload and cache_key in _MODULE_CACHE:
            return _MODULE_CACHE[cache_key]
        module = torch.jit.load(str(path), map_location="cpu")
        module.eval()
        _MODULE_CACHE[cache_key] = module
        return module


def bgr_to_nchw_float(bgr: Any) -> Any:
    import numpy as np
    import torch

    arr = np.asarray(bgr)
    if arr.ndim != 3 or arr.shape[2] not in (1, 3, 4):
        raise KernelError(f"expected HxWxC image, got shape {arr.shape}")
    if arr.shape[2] == 4:
        arr = arr[:, :, :3]
    if arr.shape[2] == 1:
        arr = np.repeat(arr, 3, axis=2)
    rgb = arr[:, :, ::-1].copy()
    chw = np.transpose(rgb.astype("float32") / 255.0, (2, 0, 1))
    return torch.from_numpy(chw)


def eval_kernel(
    kernel_id: str,
    bgr: Any,
    *,
    kernels_dir: Path,
) -> Tuple[str, Any]:
    """Run TorchScript forward; return ``(kind, value)`` where kind is scalar|features."""
    if not torch_available():
        raise KernelError(
            f"torchscript_execution unavailable: PyTorch is not installed ({kernel_id!r})"
        )
    import torch

    entry = get_manifest_entry(kernel_id, kernels_dir) or {}
    declared = str(entry.get("output_kind") or "").strip().lower()
    module = load_module(kernel_id, kernels_dir)
    tensor = bgr_to_nchw_float(bgr)
    with torch.inference_mode():
        out = module(tensor)

    if declared == "features" or (
        not declared and isinstance(out, torch.Tensor) and out.numel() > 1
    ):
        return "features", _as_features(out)
    if isinstance(out, torch.Tensor) and out.numel() > 1 and declared == "scalar":
        raise KernelError(
            f"kernel {kernel_id!r} declared scalar but returned shape {tuple(out.shape)}"
        )
    return "scalar", _as_scalar(out)


def _as_scalar(out: Any) -> float:
    import torch

    if isinstance(out, torch.Tensor):
        if out.numel() != 1:
            raise KernelError(f"expected scalar tensor, got shape {tuple(out.shape)}")
        return float(out.detach().cpu().reshape(()).item())
    if isinstance(out, (int, float)):
        return float(out)
    raise KernelError(f"unsupported scalar output type: {type(out)!r}")


def _as_features(out: Any) -> List[float]:
    import torch

    if isinstance(out, torch.Tensor):
        flat = out.detach().cpu().reshape(-1)
        if flat.numel() < 1:
            raise KernelError("features tensor is empty")
        return [float(x) for x in flat.tolist()]
    if isinstance(out, (list, tuple)) and out:
        return [float(x) for x in out]
    raise KernelError(f"unsupported features output type: {type(out)!r}")


__all__ = [
    "KernelError",
    "MAX_ARTIFACT_BYTES",
    "clear_caches",
    "default_kernels_dir",
    "eval_kernel",
    "get_manifest_entry",
    "kernels_http_catalog",
    "list_kernels",
    "load_manifest",
    "load_module",
    "provision_bytes",
    "resolve_artifact_path",
    "torch_available",
]
