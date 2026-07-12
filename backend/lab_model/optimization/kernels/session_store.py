"""Session-scoped TorchScript kernel packages (lease → job handoff)."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .torchscript_runtime import (
    clear_torchscript_caches,
    torch_available,
)

_LOCK = threading.RLock()
MAX_ARTIFACT_BYTES = 5 * 1024 * 1024
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")

# Context: extra allowlisted roots for the current request/job.
_EXTRA_ROOTS: List[Path] = []


def session_kernels_allowed(backend_id: str) -> bool:
    """Mock always; other backends require CLOUDLABS_ALLOW_SESSION_KERNELS=1."""
    bid = (backend_id or "").strip().lower()
    if bid.startswith("mock."):
        return True
    flag = os.environ.get("CLOUDLABS_ALLOW_SESSION_KERNELS", "").strip().lower()
    return flag in {"1", "true", "yes", "on"}


def data_root() -> Path:
    """Stable scratch root (not request-scoped lab_view — that breaks job submit).

    Override with ``CLOUDLABS_KERNEL_SESSION_ROOT``.
    """
    override = os.environ.get("CLOUDLABS_KERNEL_SESSION_ROOT", "").strip()
    if override:
        root = Path(override)
    else:
        # backend/lab_model/optimization/kernels → repo root
        root = Path(__file__).resolve().parents[4] / ".cloudlabs_kernel_sessions"
    root.mkdir(parents=True, exist_ok=True)
    return root


def lease_dir(backend_id: str, lease_id: str) -> Path:
    return data_root() / _safe_segment(backend_id) / "leases" / _safe_segment(lease_id)


def job_dir(backend_id: str, job_id: str) -> Path:
    return data_root() / _safe_segment(backend_id) / "jobs" / _safe_segment(job_id)


def job_archive_dir(backend_id: str, job_id: str) -> Path:
    return data_root() / _safe_segment(backend_id) / "archive" / _safe_segment(job_id)


def _safe_segment(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", (value or "").strip()) or "x"
    return cleaned[:120]


def set_extra_kernels_roots(roots: Sequence[Path]) -> None:
    global _EXTRA_ROOTS
    with _LOCK:
        _EXTRA_ROOTS = [Path(r) for r in roots]
        clear_torchscript_caches()


def clear_extra_kernels_roots() -> None:
    set_extra_kernels_roots([])


def get_extra_kernels_roots() -> List[Path]:
    with _LOCK:
        return list(_EXTRA_ROOTS)


def package_roots_for_lease(backend_id: str, lease_id: str) -> List[Path]:
    base = lease_dir(backend_id, lease_id)
    if not base.is_dir():
        return []
    return sorted(
        p for p in base.iterdir() if p.is_dir() and (p / "manifest.json").is_file()
    )


def package_roots_for_job(backend_id: str, job_id: str) -> List[Path]:
    base = job_dir(backend_id, job_id)
    if not base.is_dir():
        return []
    return sorted(
        p for p in base.iterdir() if p.is_dir() and (p / "manifest.json").is_file()
    )


def activate_lease_roots(backend_id: str, lease_id: str) -> None:
    set_extra_kernels_roots(package_roots_for_lease(backend_id, lease_id))


def activate_job_roots(backend_id: str, job_id: str) -> None:
    set_extra_kernels_roots(package_roots_for_job(backend_id, job_id))


def make_kernel_id(backend_id: str, name: str) -> str:
    if not _SAFE_NAME.match(name):
        raise ValueError(
            "kernel name must be alphanumeric/._- and start with alphanumeric"
        )
    short = _safe_segment(backend_id).replace(".", "_")[:24]
    return f"session.{short}.{name}.{uuid.uuid4().hex[:8]}"


def sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def register_package(
    *,
    backend_id: str,
    lease_id: str,
    name: str,
    artifact_bytes: bytes,
    label: Optional[str] = None,
    description: str = "",
    output_kind: str = "scalar",
    feature_names: Optional[Sequence[str]] = None,
    inputs: Optional[Sequence[Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    """Write a session package under the lease scratch and return descriptor."""
    if not session_kernels_allowed(backend_id):
        raise PermissionError(
            f"session kernel registration not allowed on backend {backend_id!r}; "
            "set CLOUDLABS_ALLOW_SESSION_KERNELS=1 for non-mock backends"
        )
    if not artifact_bytes:
        raise ValueError("artifact bytes must be non-empty")
    if len(artifact_bytes) > MAX_ARTIFACT_BYTES:
        raise ValueError(
            f"artifact exceeds max size ({MAX_ARTIFACT_BYTES} bytes)"
        )
    kind = (output_kind or "scalar").strip().lower()
    if kind not in {"scalar", "features"}:
        raise ValueError("output_kind must be 'scalar' or 'features'")

    digest = sha256_bytes(artifact_bytes)
    kernel_id = make_kernel_id(backend_id, name)
    pkg_dir = lease_dir(backend_id, lease_id) / kernel_id
    pkg_dir.mkdir(parents=True, exist_ok=False)
    artifact_name = "model.pt"
    (pkg_dir / artifact_name).write_bytes(artifact_bytes)

    names = [str(n) for n in (feature_names or [])]
    outputs: List[Dict[str, Any]]
    if kind == "features":
        outputs = [
            {
                "name": "features",
                "role": "features",
                "shape": [len(names)] if names else None,
                "feature_names": names,
            }
        ]
    else:
        outputs = [{"name": "score", "role": "scalar"}]

    entry = {
        "id": kernel_id,
        "label": label or name,
        "description": description or f"Session kernel {name}",
        "artifact": artifact_name,
        "runtime": "torchscript",
        "backend": "any",
        "phase": "session",
        "hooks": ["evaluate", "capture", "tensor"],
        "output_kind": kind,
        "feature_names": names,
        "digest": digest,
        "scope": "session",
        "inputs": list(inputs)
        if inputs is not None
        else [
            {
                "name": "image",
                "from": "measurables.camera_image",
                "layout": "bgr_hwc_uint8",
            }
        ],
        "outputs": outputs,
    }
    manifest = {"schema_version": 1, "kernels": [entry]}
    (pkg_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )

    # Smoke-load when torch is available
    if torch_available():
        import torch

        module = torch.jit.load(str(pkg_dir / artifact_name), map_location="cpu")
        module.eval()

    clear_torchscript_caches()
    activate_lease_roots(backend_id, lease_id)
    return dict(entry)


def list_lease_packages(backend_id: str, lease_id: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for root in package_roots_for_lease(backend_id, lease_id):
        try:
            payload = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for raw in payload.get("kernels") or []:
            if isinstance(raw, dict) and raw.get("id"):
                row = dict(raw)
                row["_root"] = str(root)
                row["artifact_path"] = str(root / str(row.get("artifact") or "model.pt"))
                row["artifact_present"] = (root / str(row.get("artifact") or "model.pt")).is_file()
                rows.append(row)
    return rows


def delete_package(backend_id: str, lease_id: str, kernel_id: str) -> bool:
    pkg = lease_dir(backend_id, lease_id) / kernel_id.strip()
    if not pkg.is_dir():
        return False
    shutil.rmtree(pkg, ignore_errors=True)
    clear_torchscript_caches()
    activate_lease_roots(backend_id, lease_id)
    return True


def delete_lease_scratch(backend_id: str, lease_id: str) -> None:
    base = lease_dir(backend_id, lease_id)
    if base.exists():
        shutil.rmtree(base, ignore_errors=True)
    clear_torchscript_caches()
    if not package_roots_for_job(backend_id, lease_id):
        # only clear extra roots if they pointed at this lease
        clear_extra_kernels_roots()


def read_artifact_bytes(backend_id: str, lease_id: str, kernel_id: str) -> bytes:
    pkg = lease_dir(backend_id, lease_id) / kernel_id.strip()
    manifest_path = pkg / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"unknown session kernel {kernel_id!r}")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = (payload.get("kernels") or [None])[0]
    if not isinstance(entry, dict):
        raise FileNotFoundError(f"corrupt session kernel {kernel_id!r}")
    artifact = str(entry.get("artifact") or "model.pt")
    path = pkg / artifact
    if not path.is_file():
        raise FileNotFoundError(f"artifact missing for {kernel_id!r}")
    return path.read_bytes()


def stage_packages_for_job(
    *,
    backend_id: str,
    lease_id: str,
    job_id: str,
    kernel_ids: Sequence[str],
) -> List[Dict[str, Any]]:
    """Copy session packages into job scratch + archive; return audit rows."""
    wanted = {k.strip() for k in kernel_ids if str(k).strip().startswith("session.")}
    if not wanted:
        return []

    available = {row["id"]: row for row in list_lease_packages(backend_id, lease_id)}
    missing = sorted(wanted - set(available))
    if missing:
        raise FileNotFoundError(
            f"session kernels not found on lease {lease_id!r}: {missing}"
        )

    audit: List[Dict[str, Any]] = []
    dest_base = job_dir(backend_id, job_id)
    arch_base = job_archive_dir(backend_id, job_id)
    dest_base.mkdir(parents=True, exist_ok=True)
    arch_base.mkdir(parents=True, exist_ok=True)

    for kid in sorted(wanted):
        src = lease_dir(backend_id, lease_id) / kid
        dst = dest_base / kid
        if dst.exists():
            shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(src, dst)
        # Archive copy for audit
        arch = arch_base / kid
        if arch.exists():
            shutil.rmtree(arch, ignore_errors=True)
        shutil.copytree(src, arch)

        row = dict(available[kid])
        artifact = str(row.get("artifact") or "model.pt")
        data = (dst / artifact).read_bytes()
        digest = str(row.get("digest") or sha256_bytes(data))
        audit.append(
            {
                "kernel_id": kid,
                "digest": digest,
                "output_kind": row.get("output_kind") or "scalar",
                "feature_names": list(row.get("feature_names") or []),
                "label": row.get("label"),
                "artifact_archived": True,
                "archive_path": str(arch / artifact),
            }
        )

    clear_torchscript_caches()
    return audit


def delete_job_scratch(backend_id: str, job_id: str) -> None:
    base = job_dir(backend_id, job_id)
    if base.exists():
        shutil.rmtree(base, ignore_errors=True)
    clear_torchscript_caches()


def stage_packages_from_payload(
    *,
    backend_id: str,
    job_id: str,
    packages: Sequence[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    """Stage packages supplied inline on job submit (``artifact_b64``)."""
    import base64

    audit: List[Dict[str, Any]] = []
    dest_base = job_dir(backend_id, job_id)
    arch_base = job_archive_dir(backend_id, job_id)
    dest_base.mkdir(parents=True, exist_ok=True)
    arch_base.mkdir(parents=True, exist_ok=True)

    for raw in packages:
        if not isinstance(raw, Mapping):
            continue
        kid = str(raw.get("kernel_id") or raw.get("id") or "").strip()
        b64 = raw.get("artifact_b64") or raw.get("artifact_base64")
        if not kid or not b64:
            raise ValueError(
                f"kernel_packages entry missing kernel_id/artifact_b64: {raw!r}"
            )
        data = base64.b64decode(str(b64), validate=False)
        if len(data) > MAX_ARTIFACT_BYTES:
            raise ValueError(f"artifact for {kid!r} exceeds max size")
        digest = str(raw.get("digest") or sha256_bytes(data))
        kind = str(raw.get("output_kind") or "scalar")
        names = [str(n) for n in (raw.get("feature_names") or [])]
        pkg = dest_base / kid
        if pkg.exists():
            shutil.rmtree(pkg, ignore_errors=True)
        pkg.mkdir(parents=True, exist_ok=True)
        (pkg / "model.pt").write_bytes(data)
        entry = {
            "id": kid,
            "label": raw.get("label") or kid,
            "description": raw.get("description") or "",
            "artifact": "model.pt",
            "runtime": "torchscript",
            "backend": "any",
            "phase": "session",
            "hooks": ["evaluate", "capture", "tensor"],
            "output_kind": kind,
            "feature_names": names,
            "digest": digest,
            "scope": "session",
            "inputs": raw.get("inputs")
            or [
                {
                    "name": "image",
                    "from": "measurables.camera_image",
                    "layout": "bgr_hwc_uint8",
                }
            ],
            "outputs": raw.get("outputs")
            or (
                [{"name": "features", "role": "features", "feature_names": names}]
                if kind == "features"
                else [{"name": "score", "role": "scalar"}]
            ),
        }
        (pkg / "manifest.json").write_text(
            json.dumps({"schema_version": 1, "kernels": [entry]}, indent=2),
            encoding="utf-8",
        )
        arch = arch_base / kid
        if arch.exists():
            shutil.rmtree(arch, ignore_errors=True)
        shutil.copytree(pkg, arch)
        audit.append(
            {
                "kernel_id": kid,
                "digest": digest,
                "output_kind": kind,
                "feature_names": names,
                "label": entry.get("label"),
                "artifact_archived": True,
                "archive_path": str(arch / "model.pt"),
            }
        )
    clear_torchscript_caches()
    return audit


def resolve_session_kernel_ids(
    kernel_ids: Sequence[str],
    *,
    known_session: Optional[Mapping[str, Any]] = None,
) -> List[str]:
    """Return session.* ids that are known (in known_session or any)."""
    out: List[str] = []
    for raw in kernel_ids:
        kid = str(raw).strip()
        if kid.startswith("session."):
            if known_session is None or kid in known_session:
                out.append(kid)
    return out


__all__ = [
    "MAX_ARTIFACT_BYTES",
    "activate_job_roots",
    "activate_lease_roots",
    "clear_extra_kernels_roots",
    "data_root",
    "delete_job_scratch",
    "delete_lease_scratch",
    "delete_package",
    "get_extra_kernels_roots",
    "job_archive_dir",
    "job_dir",
    "lease_dir",
    "list_lease_packages",
    "make_kernel_id",
    "package_roots_for_job",
    "package_roots_for_lease",
    "read_artifact_bytes",
    "register_package",
    "session_kernels_allowed",
    "set_extra_kernels_roots",
    "sha256_bytes",
    "stage_packages_for_job",
    "stage_packages_from_payload",
]
