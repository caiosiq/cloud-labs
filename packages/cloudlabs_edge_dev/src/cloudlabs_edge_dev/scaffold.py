"""Create a lab-local ``cloudlabs_edge/`` full function skeleton (Edge Contract v1).

Phase 5 runtime still serves via ``cloudlabs_edge_dev.stub_server`` so
``doctor`` / ``certify`` stay green. Every adapter function below is the
Phase 6 surface to wire to lab-automation (deathray) — signatures only.
"""

from __future__ import annotations

from pathlib import Path

from cloudlabs_edge_dev.agent_skills import (
    REQUIRED_AGENT_SKILL_FILES,
    write_agent_skills,
)

# ---------------------------------------------------------------------------
# Files written by ``cloudlabs-edge init`` (doctor also checks this set).
# ---------------------------------------------------------------------------

REQUIRED_EDGE_FILES: tuple[str, ...] = (
    "main.py",
    "capabilities.json",
    "contract_version.txt",
    "contract.py",
    "latch.py",
    "kernel_host.py",
    "dispatch.py",
    "SKELETON.md",
    "bench/layout.json",
    "data/library.json",
    "data/inventory.json",
    "kernels/manifest.json",
    "optimization/__init__.py",
    "optimization/router_impl.py",
    "optimization/capture_impl.py",
    "adapters/__init__.py",
    "adapters/motion.py",
    "adapters/motors.py",
    "adapters/vision.py",
    "adapters/live_feed.py",
    "adapters/teleop.py",
    "adapters/tunables.py",
    "adapters/optimize.py",
    "adapters/observe.py",
) + REQUIRED_AGENT_SKILL_FILES

MAIN_PY = '''\
"""ASGI entrypoint for this lab's Edge Contract v1 agent.

In Phase 5 this process loads ``capabilities.json`` and ``bench/layout.json``
from disk and serves them through ``cloudlabs_edge_dev.stub_server.create_app``.
That reference implementation completes the live-plane subset
(START/END live feed and teleop, RECORD_MEASURABLES, EVAL_KERNEL) so
``cloudlabs-edge certify`` can pass without hardware, and returns a structured
``NOT_IMPLEMENTED`` refusal for every other declared primitive.

In Phase 6 you keep the same HTTP surface (``GET /capabilities``, ``GET /bench``,
``GET /library``, ``GET /inventory``, ``POST /execute``, stream URLs, teleop
WebSocket) but route ``/execute`` through ``dispatch.dispatch_primitive`` and
fill the adapter modules with lab-automation callables. Do not import
OpticalExperiment or recorder drivers from this file; keep hardware mapping
inside ``adapters/``.
"""

from __future__ import annotations

import json
from pathlib import Path

from cloudlabs_edge_dev.stub_server import create_app
from kernel_host import torch_available

_HERE = Path(__file__).resolve().parent
_CAPS = json.loads((_HERE / "capabilities.json").read_text(encoding="utf-8"))
_BENCH = json.loads((_HERE / "bench" / "layout.json").read_text(encoding="utf-8"))
_BACKEND_ID = str(_CAPS.get("backend_id") or "stub.default")

# Derive TorchScript capability from what this process can actually import, so
# the coordinator only routes EVAL_KERNEL / OPTIMIZE here when PyTorch is
# present. capabilities.json declares the static default (false); this is the
# honest runtime truth. See kernel_host.py.
_CAPS.setdefault("features", {})["torchscript_execution"] = torch_available()

app = create_app(
    backend_id=_BACKEND_ID,
    capabilities=_CAPS,
    bench=_BENCH,
    edge_root=_HERE,
)
'''

# Full UC write surface the lab edge intends to host (macros expand on coordinator).
CAPABILITIES_JSON = """\
{{
  "contract_version": "1.1.0",
  "backend_id": "{backend_id}",
  "features": {{
    "torchscript_execution": false,
    "hardware_reconciliation": false,
    "hardware_triggered_latch": false,
    "runtime_sync": true,
    "lan_direct_streams": true
  }},
  "execution_threads": [
    {{ "id": "arm.0", "kind": "arm" }},
    {{ "id": "sense.0", "kind": "sense" }}
  ],
  "supported_primitives": [
    "START_LIVE_FEED",
    "END_LIVE_FEED",
    "SET_LIVE_EXPOSURE",
    "START_TELEOP",
    "END_TELEOP",
    "TELEOP_JOG",
    "TELEOP_GOTO",
    "RECORD_MEASURABLES",
    "EVAL_KERNEL",
    "MOVE_COMPONENT",
    "MOVE_MOTOR",
    "SET_MOTOR_SETPOINT",
    "MOTOR_SET_ZERO",
    "MOTOR_SEND_HOME",
    "SET_EXPOSURE",
    "SET_LASER_OUTPUT",
    "OPTIMIZE",
    "PICK_COMPONENT",
    "HOVER",
    "PLACE_FROM_HOVER",
    "CONFIRM_HOLDING_TAG",
    "STORE_COMPONENT",
    "PLACE_FROM_STORAGE",
    "AFFIRM_PLACED_AT_CURRENT",
    "REPACK_STORAGE",
    "RECENTER_IN_STORAGE",
    "REMOVE",
    "LOCALIZE_COMPONENTS",
    "RECORD_TUNABLES",
    "SYNC_RUNTIME"
  ],
  "measurables": {{
    "tag_22.camera_image": {{
      "analysis": {{
        "layout": "bgr_hwc_uint8",
        "dtype": "uint8",
        "shape": ["H", "W", 3],
        "domain": "spatial"
      }},
      "wire": {{ "encoding": "jpeg", "profiles": ["default"] }},
      "live_channel": "tag_22.camera_image",
      "capture_latency_ms": 0
    }}
  }},
  "telemetry_channels": {{
    "tag_22.camera_image": {{
      "transport": "jpeg_poll",
      "path": "/stream/tag_22/camera_image.jpg",
      "requires_primitive": "START_LIVE_FEED",
      "binds_measurable": "tag_22.camera_image",
      "default_fps": 10,
      "default_profile": "default"
    }},
    "tag_22.camera_image.mjpeg": {{
      "transport": "mjpeg_http",
      "path": "/stream/tag_22/camera_image.mjpg",
      "requires_primitive": "START_LIVE_FEED",
      "binds_measurable": "tag_22.camera_image",
      "default_fps": 10,
      "default_profile": "default"
    }},
    "teleop": {{
      "transport": "websocket",
      "path": "/ws/teleop",
      "requires_primitive": "START_TELEOP",
      "binds_tunables_live": true
    }}
  }},
  "wire_profiles": {{
    "default": {{ "scale": 1.0, "jpeg_quality": 80, "fps": 10 }}
  }}
}}
"""

LIBRARY_JSON = """\
{
  "schema_version": 1,
  "components": {
    "tag_22": {
      "id": "cam_gripper_1",
      "type": "OPTICAL_CAMERA",
      "tag_id": "tag_22",
      "name": "Gripper Camera 1",
      "size": { "width": 40, "height": 40 },
      "parameters": {},
      "capabilities": {
        "primitives": [
          "RECORD_MEASURABLES",
          "SET_EXPOSURE",
          "SET_LIVE_EXPOSURE",
          "START_LIVE_FEED",
          "END_LIVE_FEED",
          "LOCALIZE_COMPONENTS",
          "RECORD_TUNABLES",
          "SYNC_RUNTIME"
        ],
        "statecontrol": {
          "tunables": {
            "nominal_pose": {
              "widget": "TablePose",
              "recordable": true
            },
            "exposure_time_ms": {
              "widget": "FloatRange",
              "min": 0.1,
              "max": 1000.0,
              "default": 200.0,
              "unit": "ms",
              "recordable": true
            }
          },
          "measurables": {
            "camera_image": {
              "widget": "ImageViewer",
              "dtype": "uint8",
              "layout": "bgr_hwc_uint8",
              "domain": "spatial"
            }
          }
        },
        "telemetry": {}
      }
    }
  }
}
"""

INVENTORY_JSON = """\
{
  "schema_version": 1,
  "entries": {
    "tag_22": {
      "placement": "table",
      "storage_slot": null,
      "localize": true
    }
  }
}
"""

BENCH_JSON = """\
{{
  "backend_id": "{backend_id}",
  "layout": {{
    "version": 1,
    "lab_bounds_mm": {{
      "x_min": 0.0,
      "x_max": 450.0,
      "y_min": 0.0,
      "y_max": 300.0
    }},
    "danger_zone": {{ "radius_mm": 40.0, "padding_mm": 5.0 }},
    "storage": {{ "rule": "rect", "grid_nx": 4, "grid_ny": 2 }},
    "breadboard": {{
      "grid_spacing_mm": 25.0,
      "origin_offset_mm": {{ "x": 0.0, "y": 0.0 }}
    }},
    "reconcile_staging_seats": [
      {{ "x": 400.0, "y": 250.0, "rotation": 0.0 }},
      {{ "x": 350.0, "y": 250.0, "rotation": 0.0 }}
    ]
  }},
  "laser_lines": []
}}
"""

CONTRACT_PY = '''\
"""Helpers for shaping Edge Contract ``POST /execute`` JSON responses.

Schema validation for request and response bodies lives in
``cloudlabs-edge-dev``; this module only builds the small envelopes that
adapters and ``dispatch`` should return after a primitive runs. Do not add
new verbs or Twin-only side doors here — every mutation still enters through
a ``PrimitiveId`` string on ``/execute``.
"""

from __future__ import annotations

from typing import Any

CONTRACT_VERSION = "1.1.0"


def completed(
    result: dict[str, Any] | None = None,
    *,
    epoch_ms: int,
    latch_quality: str | None = None,
) -> dict[str, Any]:
    """Build a successful execute response.

    Parameters
    ----------
    result:
        Optional JSON-serializable object describing what the primitive did
        (for example ``{"tag_id": "tag_20", "pose": {...}}``). May be omitted
        when success itself is enough.
    epoch_ms:
        Monotonic bench clock in milliseconds since the Unix epoch (or an
        edge-local monotonic substitute). Required on completed RECORD/EVAL
        and recommended on motion that changes observable state.
    latch_quality:
        Optional string such as ``"hardware_trigger"`` or
        ``"software_approx"`` describing how tightly the epoch matches the
        captured sensors.

    Returns
    -------
    dict
        ``{"status": "completed", "epoch_ms": int, ...}`` with optional
        ``result`` and ``latch_quality`` keys, matching
        ``execute_response.schema.json``.
    """
    out: dict[str, Any] = {"status": "completed", "epoch_ms": int(epoch_ms)}
    if result is not None:
        out["result"] = result
    if latch_quality is not None:
        out["latch_quality"] = latch_quality
    return out


def refused(code: str, message: str) -> dict[str, Any]:
    """Build a structured refusal (policy, lease, or not-yet-implemented).

    Parameters
    ----------
    code:
        Stable machine code (for example ``"TELEOP_NOT_STARTED"`` or
        ``"NOT_IMPLEMENTED"``).
    message:
        Human-readable explanation safe to log and show in Twin/SDK errors.

    Returns
    -------
    dict
        ``{"status": "refused", "error": {"code": ..., "message": ...}}``.
    """
    return {"status": "refused", "error": {"code": code, "message": message}}


def failed(code: str, message: str) -> dict[str, Any]:
    """Build a structured failure after the edge attempted the primitive.

    Use this when hardware or software threw during execution, as opposed to
    a clean policy refusal. Same shape as :func:`refused` but
    ``status="failed"``.
    """
    return {"status": "failed", "error": {"code": code, "message": message}}
'''

LATCH_PY = '''\
"""Observation epochs that bind RECORD_MEASURABLES and EVAL_KERNEL samples.

A latch is the edge-local mechanism that stamps one ``epoch_ms`` (and optional
``latch_quality``) onto a set of sensor readings so Twin and the SDK can tell
whether an image, motor angles, and a kernel score belong to the same capture
instant. Prefer a hardware trigger when the bench has one; otherwise open a
software window and report ``latch_quality="software_approx"`` plus any known
per-device latency from :func:`compensate_latency_ms`.
"""

from __future__ import annotations

import time
from typing import Any


def now_epoch_ms() -> int:
    """Return the current edge clock as integer milliseconds.

    Returns
    -------
    int
        Milliseconds suitable for ``epoch_ms`` fields on execute responses and
        teleop pose samples. Implementations may substitute a monotonic bench
        counter as long as values never go backwards within a process.
    """
    return int(time.time() * 1000)


def begin_latch(*, tag_id: str, reason: str = "record") -> dict[str, Any]:
    """Open a capture epoch for the given component.

    Parameters
    ----------
    tag_id:
        Catalog tag whose sensors are about to be sampled (for example
        ``"tag_22"`` for a camera still).
    reason:
        Short label for logging, typically ``"record"`` or ``"eval_kernel"``.

    Returns
    -------
    dict
        Opaque handle that :func:`end_latch` understands. A typical shape is
        ``{"tag_id": str, "reason": str, "t0_ms": int, "trigger": ...}``, but
        the exact keys are lab-private as long as ``end_latch`` can close it.
    """
    raise NotImplementedError(
        "Phase 6: open HW trigger or software latch "
        f"(tag_id={tag_id!r}, reason={reason!r})"
    )


def end_latch(handle: dict[str, Any]) -> dict[str, Any]:
    """Close a latch opened by :func:`begin_latch` and return epoch metadata.

    Parameters
    ----------
    handle:
        The dict returned by :func:`begin_latch` for this capture.

    Returns
    -------
    dict
        At least ``{"epoch_ms": int, "latch_quality": str}``.
        ``latch_quality`` should be ``"hardware_trigger"`` when a HW line
        fired, or ``"software_approx"`` when the edge only synchronized in
        software. Extra keys (device ids, latencies) are allowed for logs.
    """
    raise NotImplementedError("Phase 6: close latch / stamp epoch_ms + latch_quality")


def compensate_latency_ms(device_id: str) -> int:
    """Return known capture latency for a device when HW trigger is unavailable.

    Parameters
    ----------
    device_id:
        Stable id for the sensor or camera (lab-defined string).

    Returns
    -------
    int
        Non-negative milliseconds to subtract or annotate when stamping
        ``epoch_ms`` for that device. Return ``0`` when latency is unknown.
    """
    raise NotImplementedError(f"Phase 6: latency table for device {device_id!r}")
'''

KERNEL_HOST_PY = '''\
"""Load and run TorchScript kernels on edge-resident analysis images.

Kernels are inputs to the ``EVAL_KERNEL`` primitive (and to closed-loop
OPTIMIZE), not peer HTTP verbs. They always consume the analysis form of a
measurable -- for cameras, HxWx3 ``uint8`` BGR in process memory -- never a
lossy Twin JPEG stream. Loaded modules are cached here so repeated probes do
not reload weights.

This module ships working, lab-independent provisioning + execution. The only
part you may want to customize is how a specific kernel's raw output maps to
result fields in :func:`eval_on_bgr`; the default handles scalar / feature /
gate outputs.

Provisioning
------------
A local bench keeps ``.pt`` files under ``{CLOUDLABS_EDGE_KERNELS_DIR}`` and
refers to them by id. A *remote* coordinator cannot share that filesystem, so
``EVAL_KERNEL`` args may also carry the artifact itself -- inline
(``artifact_b64``) or by URL (``kernel_uri`` / ``kernel_url``) -- with an
optional ``digest`` (``sha256:<hex>``) for integrity. :func:`provision_kernel`
fetches, verifies, and caches it to ``{kernels_dir}/{kernel_id}.pt`` so the
usual load path then applies. Re-provisioning is skipped when the cached file
already matches the requested digest.

If PyTorch is not installed, :func:`load_kernel` raises a clear error that
dispatch maps to a refusal. Derive
``capabilities.features.torchscript_execution`` from :func:`torch_available`
so the coordinator only routes kernels when this bench can actually run them.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import threading
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

_CACHE: Dict[str, Any] = {}
_lock = threading.Lock()

#: Reject artifacts larger than this (mirrors the coordinator session store).
MAX_ARTIFACT_BYTES = 5 * 1024 * 1024
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def torch_available() -> bool:
    """True when PyTorch can be imported in this process (drives capabilities)."""
    try:
        import torch  # type: ignore  # noqa: F401
    except Exception:  # noqa: BLE001 - any import/runtime error means unusable
        return False
    return True


def _kernels_dir() -> Path:
    raw = os.environ.get("CLOUDLABS_EDGE_KERNELS_DIR")
    if raw:
        return Path(raw)
    return Path(__file__).resolve().parent / "kernels"


def _load_manifest_map() -> Dict[str, Dict[str, Any]]:
    """Map kernel_id → manifest row from ``kernels/manifest.json`` (edge-owned catalog)."""
    manifest_path = _kernels_dir() / "manifest.json"
    if not manifest_path.is_file():
        return {}
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for raw in payload.get("kernels") or []:
        if isinstance(raw, dict) and raw.get("id"):
            out[str(raw["id"])] = dict(raw)
    return out


def _resolve_path(kernel_id: str) -> Path:
    p = Path(kernel_id)
    if p.suffix == ".pt" and p.is_absolute():
        return p
    entry = _load_manifest_map().get(kernel_id)
    if entry is not None:
        artifact = str(entry.get("artifact") or f"{kernel_id}.pt")
        if not os.path.isabs(artifact) and ".." not in Path(artifact).parts:
            return _kernels_dir() / artifact
    return _kernels_dir() / f"{kernel_id}.pt"


def _normalize_digest(digest: Optional[str]) -> Optional[str]:
    """Return a bare lowercase hex sha256, accepting an optional ``sha256:`` prefix."""
    if not digest:
        return None
    raw = str(digest).strip().lower()
    if raw.startswith("sha256:"):
        raw = raw.split(":", 1)[1]
    return raw or None


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fetch_artifact_bytes(args: Dict[str, Any]) -> Optional[bytes]:
    """Pull kernel bytes from ``args`` (inline base64 or a download URL), or None."""
    b64 = args.get("artifact_b64") or args.get("artifact_base64")
    if b64:
        try:
            return base64.b64decode(str(b64), validate=False)
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"invalid artifact_b64: {exc}") from exc

    uri = args.get("kernel_uri") or args.get("kernel_url") or args.get("artifact_uri")
    if uri:
        uri = str(uri)
        scheme = uri.split(":", 1)[0].lower()
        if scheme not in ("http", "https", "file"):
            raise ValueError(f"unsupported kernel_uri scheme: {scheme!r}")
        try:
            with urllib.request.urlopen(uri, timeout=30.0) as resp:  # noqa: S310 - scheme allowlisted
                data = resp.read(MAX_ARTIFACT_BYTES + 1)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"failed to download kernel from {uri!r}: {exc}") from exc
        return data
    return None


def provision_kernel(kernel_id: str, args: Optional[Dict[str, Any]] = None) -> Optional[Path]:
    """Ensure a ``.pt`` for ``kernel_id`` is on disk, fetching it if args supply one.

    Parameters
    ----------
    kernel_id:
        Registry / session id used as the cache filename (``{id}.pt``).
    args:
        Execute args that may carry ``artifact_b64`` or ``kernel_uri`` (+ optional
        ``digest``). When neither is present this is a no-op and returns ``None``
        (the caller falls back to a pre-installed local file).

    Returns
    -------
    Optional[Path]
        The cached artifact path when (re)provisioned, else ``None``.

    Raises
    ------
    ValueError
        On unsafe id, oversize artifact, bad base64, or digest mismatch.
    """
    args = args or {}
    data = None
    digest = _normalize_digest(args.get("digest"))

    # Absolute local .pt ids are pre-installed; never overwrite them.
    p = Path(kernel_id)
    if p.suffix == ".pt" and p.is_absolute():
        return None

    if not _SAFE_ID.match(kernel_id or ""):
        # Only enforce when we actually need to write a cache file.
        if _fetch_artifact_bytes(args) is not None:
            raise ValueError(
                f"unsafe kernel_id for provisioning: {kernel_id!r}"
            )
        return None

    path = _resolve_path(kernel_id)

    # Fast path: cached file already matches the requested digest.
    if path.is_file() and digest is not None:
        if _sha256_hex(path.read_bytes()) == digest:
            return path

    data = _fetch_artifact_bytes(args)
    if data is None:
        return None
    if not data:
        raise ValueError("kernel artifact is empty")
    if len(data) > MAX_ARTIFACT_BYTES:
        raise ValueError(
            f"kernel artifact exceeds max size ({MAX_ARTIFACT_BYTES} bytes)"
        )
    if digest is not None:
        actual = _sha256_hex(data)
        if actual != digest:
            raise ValueError(
                f"kernel digest mismatch: expected {digest}, got {actual}"
            )

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
    with _lock:
        _CACHE.pop(kernel_id, None)  # force reload of freshly written weights
    return path


def load_kernel(kernel_id: str) -> Any:
    """Load (or return a cached) TorchScript module for ``kernel_id``.

    Parameters
    ----------
    kernel_id:
        Registry id such as ``"ensemble.eval.image_features"`` or an absolute
        ``.pt`` path agreed with the coordinator job payload.

    Returns
    -------
    Any
        A ``torch.jit.ScriptModule`` in eval mode, ready for :func:`eval_on_bgr`.
    """
    with _lock:
        if kernel_id in _CACHE:
            return _CACHE[kernel_id]
        try:
            import torch  # type: ignore
        except ImportError as exc:  # pragma: no cover - bench without torch
            raise RuntimeError(
                "torchscript_execution unavailable: PyTorch is not installed"
            ) from exc
        path = _resolve_path(kernel_id)
        if not path.is_file():
            raise FileNotFoundError(f"kernel {kernel_id!r} not found at {path}")
        module = torch.jit.load(str(path), map_location="cpu")
        module.eval()
        _CACHE[kernel_id] = module
        return module


def eval_on_bgr(
    kernel_id: str,
    bgr: Any,
    *,
    args: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run ``kernel_id`` on one BGR frame and return scalar and/or features.

    Parameters
    ----------
    kernel_id:
        Same id passed to :func:`load_kernel`.
    bgr:
        NumPy (or equivalent) array with shape ``(H, W, 3)``, dtype ``uint8``,
        channel order BGR -- the analysis layout declared for ``camera_image``.
    args:
        Optional kernel parameters from the execute body (ROI, thresholds).

    Returns
    -------
    dict
        JSON-friendly results: ``{"scalar": float}`` and/or
        ``{"features": [float, ...]}``, plus ``{"passed": bool}`` when the
        kernel returns a gate (via ``threshold``).
    """
    # Provision the artifact first when the caller shipped one (remote edge path),
    # then load (which raises a clear "torchscript_execution unavailable" error if
    # PyTorch is missing -> mapped to a contract refusal). Import torch only after,
    # when it is guaranteed present, so a torch-less bench refuses rather than 500s.
    provision_kernel(kernel_id, args or {})
    module = load_kernel(kernel_id)

    import torch  # type: ignore

    tensor = torch.from_numpy(_as_uint8_hwc(bgr)).float()
    # Provide NCHW float in [0, 1] as the conventional input; kernels that want
    # HWC can index accordingly. We pass both-friendly NCHW here.
    nchw = tensor.permute(2, 0, 1).unsqueeze(0) / 255.0
    with torch.no_grad():
        try:
            out = module(nchw)
        except Exception:
            out = module(tensor)  # fall back to raw HWC uint8-as-float

    result: Dict[str, Any] = {"kernel_id": kernel_id}
    if isinstance(out, torch.Tensor):
        flat = out.reshape(-1)
        if flat.numel() == 1:
            result["scalar"] = float(flat.item())
        else:
            result["features"] = [float(v) for v in flat.tolist()]
    elif isinstance(out, (tuple, list)):
        result["features"] = [float(v) for v in torch.as_tensor(out).reshape(-1).tolist()]
    else:
        result["scalar"] = float(out)

    threshold = (args or {}).get("threshold")
    if threshold is not None and "scalar" in result:
        result["passed"] = bool(result["scalar"] >= float(threshold))
    return result


def _as_uint8_hwc(bgr: Any) -> Any:
    import numpy as np  # type: ignore

    arr = np.asarray(bgr)
    if arr.dtype != np.uint8:
        arr = np.clip(arr, 0, 255).astype(np.uint8)
    return arr


def unload_kernel(kernel_id: str) -> None:
    """Drop a cached kernel so the next load re-reads weights from disk.

    Parameters
    ----------
    kernel_id:
        Id previously passed to :func:`load_kernel`. Missing ids are a no-op.
    """
    with _lock:
        _CACHE.pop(kernel_id, None)
'''

DISPATCH_PY = '''\
"""Route one Edge Contract ``primitive`` string to the matching adapter.

``PRIMITIVE_HANDLERS`` is the authoritative map from the names listed in
``capabilities.json`` / ``PrimitiveId`` to callables that accept the
``args`` object from ``POST /execute``. In Phase 5 this file documents the
surface while ``main`` still uses the reference stub. In Phase 6,
``main`` should call :func:`dispatch_primitive`, wrap the return value with
``contract.completed`` / ``contract.refused``, and never invent alternate
HTTP verbs for the same work.
"""

from __future__ import annotations

from typing import Any, Callable

from adapters import live_feed, motion, motors, observe, optimize, teleop, tunables, vision
from kernel_host import eval_on_bgr
from latch import begin_latch, end_latch, now_epoch_ms

# Each value receives the execute ``args`` dict and returns a lab-defined
# result object (usually a dict) that main will place under ``result``.
PRIMITIVE_HANDLERS: dict[str, Callable[[dict[str, Any]], Any]] = {
    "START_LIVE_FEED": lambda a: live_feed.arm_live_feed(
        str(a.get("channel") or a.get("measurable_id") or ""),
        profile=a.get("profile"),
        exposure_time_ms=(
            float(a["exposure_time_ms"])
            if a.get("exposure_time_ms") is not None
            else None
        ),
    ),
    "END_LIVE_FEED": lambda a: live_feed.disarm_live_feed(
        str(a.get("channel") or a.get("measurable_id") or ""),
    ),
    "SET_LIVE_EXPOSURE": lambda a: live_feed.set_live_exposure(a),
    "START_TELEOP": lambda a: teleop.start_teleop(
        str(a.get("tag_id") or a.get("target_id") or "")
    ),
    "END_TELEOP": lambda a: teleop.end_teleop(
        str(a.get("tag_id") or a.get("target_id") or "")
    ),
    "TELEOP_JOG": lambda a: teleop.teleop_jog(a),
    "TELEOP_GOTO": lambda a: teleop.teleop_goto(a),
    "RECORD_MEASURABLES": lambda a: observe.record_measurables(
        str(a.get("tag_id") or a.get("target_id") or ""), a
    ),
    "EVAL_KERNEL": lambda a: observe.eval_kernel(str(a.get("kernel_id") or ""), a),
    "MOVE_COMPONENT": lambda a: motion.move_component(a),
    "PICK_COMPONENT": lambda a: motion.pick_component(a),
    "HOVER": lambda a: motion.hover_component(a),
    "PLACE_FROM_HOVER": lambda a: motion.place_from_hover(a),
    "CONFIRM_HOLDING_TAG": lambda a: motion.confirm_holding_tag(a),
    "STORE_COMPONENT": lambda a: motion.store_component(a),
    "PLACE_FROM_STORAGE": lambda a: motion.place_from_storage(a),
    "AFFIRM_PLACED_AT_CURRENT": lambda a: motion.affirm_placed_at_current(a),
    "REPACK_STORAGE": lambda a: motion.repack_storage_slot(a),
    "RECENTER_IN_STORAGE": lambda a: motion.recenter_stored_in_inventory(a),
    "REMOVE": lambda a: motion.remove_component(a),
    "MOVE_MOTOR": lambda a: motors.move_motor(a),
    "SET_MOTOR_SETPOINT": lambda a: motors.set_motor_setpoint(a),
    "MOTOR_SET_ZERO": lambda a: motors.motor_set_zero(a),
    "MOTOR_SEND_HOME": lambda a: motors.motor_send_home(a),
    "SET_EXPOSURE": lambda a: tunables.set_exposure_time_ms(a),
    "SET_LASER_OUTPUT": lambda a: tunables.set_output_power_mw(a),
    "OPTIMIZE": lambda a: optimize.optimize_component(a),
    "LOCALIZE_COMPONENTS": lambda a: vision.localize_components(a),
    "RECORD_TUNABLES": lambda a: vision.record_tunables(a),
    "SYNC_RUNTIME": lambda a: vision.sync_runtime(a),
}


def dispatch_primitive(primitive: str, args: dict[str, Any] | None = None) -> Any:
    """Invoke the adapter registered for ``primitive``.

    Parameters
    ----------
    primitive:
        Exact Edge Contract name (for example ``"MOVE_COMPONENT"``), matching
        an entry in ``capabilities.supported_primitives``.
    args:
        The ``args`` object from the execute body. Missing keys are the
        adapter's responsibility to default or refuse.

    Returns
    -------
    Any
        Whatever the adapter returns (typically a ``dict``). Callers should
        wrap successes with ``contract.completed`` and map
        ``NotImplementedError`` / policy errors to ``contract.refused``.

    Raises
    ------
    KeyError
        If ``primitive`` has no row in ``PRIMITIVE_HANDLERS``.
    """
    args = args or {}
    handler = PRIMITIVE_HANDLERS.get(primitive)
    if handler is None:
        raise KeyError(f"no adapter mapping for primitive {primitive!r}")
    return handler(args)


__all__ = [
    "PRIMITIVE_HANDLERS",
    "dispatch_primitive",
    "begin_latch",
    "end_latch",
    "now_epoch_ms",
    "eval_on_bgr",
]
'''

ADAPTERS_INIT = '''\
"""Hardware mapping package for this lab edge.

Modules under ``adapters/`` are the only place that should import
lab-automation (OpticalExperiment, recorder helpers, LiveControlSession,
wifi steppers). ``main.py`` stays a thin HTTP shell: it parses Edge Contract
requests, calls ``dispatch``, and returns JSON. Filling these adapters is
Phase 6 work; until then every function raises ``NotImplementedError`` on
purpose so the skeleton documents the full surface without pretending the
hardware is wired.
"""
'''

ADAPTERS_MOTION = '''\
"""Cartesian motion and inventory primitives for placed or stored components.

These functions implement the motion half of the Edge Contract: moving a
tag on the bench, in-air pick/hover/place, and storage-grid operations.
Wire each body to the existing OpticalExperiment ``*_cloudlab`` helpers (or
a future motion backend) without changing the function names Twin and the
SDK already call through ``POST /execute``.

Unless noted, ``args`` is the execute ``args`` object and every function
returns a JSON-serializable ``dict`` that will become the execute
``result`` (include ``tag_id`` and any updated pose or presence fields the
coordinator should merge into lab state).
"""

from __future__ import annotations

from typing import Any


def move_component(args: dict[str, Any]) -> dict[str, Any]:
    """Move a placed component to an absolute bench pose (MOVE_COMPONENT).

    Parameters
    ----------
    args:
        Must include the target tag as ``tag_id`` or ``target_id``. Pose is
        usually ``target_x``, ``target_y``, and ``rotation`` in lab
        millimetres / degrees (same convention as Twin nominal_pose). Optional
        speed or frame hints may appear as lab-defined keys.

    Returns
    -------
    dict
        At least ``{"tag_id": str}`` and the pose that was commanded or
        achieved, for example ``{"tag_id": "tag_20", "pose": {"x": ..., "y": ...,
        "rotation": ...}}``.
    """
    raise NotImplementedError("Phase 6: OpticalExperiment move / place at pose")


def pick_component(args: dict[str, Any]) -> dict[str, Any]:
    """Lift a placed component into the held/in-air state (PICK_COMPONENT).

    Parameters
    ----------
    args:
        ``tag_id`` / ``target_id`` of the component to pick. Optional approach
        offsets may be supplied by the lab.

    Returns
    -------
    dict
        Confirmation including ``tag_id`` and presence/holding state after the
        pick (for example ``{"tag_id": "...", "holding": true}``).
    """
    raise NotImplementedError("Phase 6: pick_component_cloudlab")


def hover_component(args: dict[str, Any]) -> dict[str, Any]:
    """Move a held component to a hover pose above the bench (HOVER).

    Parameters
    ----------
    args:
        ``tag_id`` / ``target_id`` plus the hover pose fields the lab uses
        (often the same x/y/rotation keys as MOVE_COMPONENT).

    Returns
    -------
    dict
        ``tag_id`` and the hover pose that was reached.
    """
    raise NotImplementedError("Phase 6: hover_component_cloudlab")


def place_from_hover(args: dict[str, Any]) -> dict[str, Any]:
    """Set a held component down from hover onto the bench (PLACE_FROM_HOVER).

    Parameters
    ----------
    args:
        ``tag_id`` / ``target_id`` and the final place pose.

    Returns
    -------
    dict
        ``tag_id``, resulting presence (placed), and final pose.
    """
    raise NotImplementedError("Phase 6: place_from_hover_cloudlab")


def confirm_holding_tag(args: dict[str, Any]) -> dict[str, Any]:
    """Confirm which tag the arm believes it is holding (CONFIRM_HOLDING_TAG).

    Parameters
    ----------
    args:
        Usually ``tag_id`` expected to be in the gripper; labs may also pass
        vision confirmation flags.

    Returns
    -------
    dict
        ``{"tag_id": str, "holding": bool}`` (and optional confidence).
    """
    raise NotImplementedError("Phase 6: confirm holding tag")


def store_component(args: dict[str, Any]) -> dict[str, Any]:
    """Move a component from the bench into a storage slot (STORE_COMPONENT).

    Parameters
    ----------
    args:
        ``tag_id`` / ``target_id``; optional explicit slot coordinates if the
        lab does not assign slots automatically.

    Returns
    -------
    dict
        ``tag_id``, storage slot id/indices, and presence ``"STORED"``.
    """
    raise NotImplementedError("Phase 6: store / inventory path")


def place_from_storage(args: dict[str, Any]) -> dict[str, Any]:
    """Bring a stored component onto the bench (PLACE_FROM_STORAGE).

    Parameters
    ----------
    args:
        ``tag_id`` / ``target_id`` and the destination pose
        (``target_x`` / ``target_y`` / ``rotation``).

    Returns
    -------
    dict
        ``tag_id``, presence ``"PLACED"``, and final pose.
    """
    raise NotImplementedError("Phase 6: place_from_storage")


def affirm_placed_at_current(args: dict[str, Any]) -> dict[str, Any]:
    """Affirm that a component is correctly placed at its current pose.

    Parameters
    ----------
    args:
        ``tag_id`` / ``target_id`` of the component being affirmed.

    Returns
    -------
    dict
        ``tag_id`` and any bookkeeping fields the lab uses for affirmations.
    """
    raise NotImplementedError("Phase 6: affirm placed")


def repack_storage_slot(args: dict[str, Any]) -> dict[str, Any]:
    """Repack or tidy a storage slot (REPACK_STORAGE).

    Parameters
    ----------
    args:
        Identifies the slot and/or ``tag_id`` to repack.

    Returns
    -------
    dict
        Slot identity and outcome status.
    """
    raise NotImplementedError("Phase 6: repack storage slot")


def recenter_stored_in_inventory(args: dict[str, Any]) -> dict[str, Any]:
    """Recenter a stored part inside its inventory cell (RECENTER_IN_STORAGE).

    Parameters
    ----------
    args:
        ``tag_id`` / ``target_id`` of the stored component.

    Returns
    -------
    dict
        ``tag_id`` and updated storage pose or slot metadata.
    """
    raise NotImplementedError("Phase 6: recenter in inventory")


def remove_component(args: dict[str, Any]) -> dict[str, Any]:
    """Remove a component from the runtime bench model (REMOVE).

    Parameters
    ----------
    args:
        ``tag_id`` / ``target_id`` to remove. Physical park behaviour is
        lab-defined; the contract requires a clear success/failure result.

    Returns
    -------
    dict
        ``{"tag_id": str, "removed": true}`` (or equivalent).
    """
    raise NotImplementedError("Phase 6: remove component")
'''

ADAPTERS_MOTORS = '''\
"""Per-motor angle primitives for wifi steppers and similar drivers.

These functions change ``nominal_motor_positions`` (or the lab equivalent)
for a tagged component. Angles are in degrees unless your lab documents
otherwise; keep the same units Twin already shows. Return dicts that echo
``tag_id``, ``motor_id``, and the angle that was commanded or read back.
"""

from __future__ import annotations

from typing import Any


def move_motor(args: dict[str, Any]) -> dict[str, Any]:
    """Command a relative or absolute motor move (MOVE_MOTOR).

    Parameters
    ----------
    args:
        ``tag_id`` / ``target_id``, integer ``motor_id``, and a distance or
        angle field (commonly ``distance`` or ``angle_deg`` — match the
        existing wifi_stepper API).

    Returns
    -------
    dict
        ``{"tag_id": str, "motor_id": int, "angle_deg": float}`` after the move.
    """
    raise NotImplementedError("Phase 6: wifi_stepper move_motor")


def set_motor_setpoint(args: dict[str, Any]) -> dict[str, Any]:
    """Set an absolute motor angle setpoint (SET_MOTOR_SETPOINT).

    Parameters
    ----------
    args:
        ``tag_id`` / ``target_id``, ``motor_id``, and absolute ``angle_deg``.

    Returns
    -------
    dict
        The tag, motor id, and absolute angle now in effect.
    """
    raise NotImplementedError("Phase 6: set motor setpoint")


def motor_set_zero(args: dict[str, Any]) -> dict[str, Any]:
    """Define the current mechanical position as zero (MOTOR_SET_ZERO).

    Parameters
    ----------
    args:
        ``tag_id`` / ``target_id`` and ``motor_id``.

    Returns
    -------
    dict
        Confirmation with ``tag_id``, ``motor_id``, and ``angle_deg`` of ``0``
        (or the lab's zero convention).
    """
    raise NotImplementedError("Phase 6: motor set zero")


def motor_send_home(args: dict[str, Any]) -> dict[str, Any]:
    """Send a motor to its home angle (MOTOR_SEND_HOME).

    On the coordinator this is often a macro that expands to MOVE_MOTOR. The
    edge may still expose a direct implementation for local callers.

    Parameters
    ----------
    args:
        ``tag_id`` / ``target_id`` and ``motor_id``.

    Returns
    -------
    dict
        ``tag_id``, ``motor_id``, and the home angle reached.
    """
    raise NotImplementedError("Phase 6: motor send home (or rely on coordinator macro)")
'''

ADAPTERS_VISION = '''\
"""Still capture in analysis (BGR) and wire (JPEG) forms for one camera tag.

Analysis frames are HxWx3 ``uint8`` BGR arrays used by RECORD_MEASURABLES,
EVAL_KERNEL, and OPTIMIZE. Wire frames are JPEG bytes declared under
``capabilities.measurables.*.wire`` and served on live channels after
START_LIVE_FEED. Implement these against the recorder TCP CAP / GET_JPEG
path (or equivalent) without exposing ad-hoc Twin helpers.
"""

from __future__ import annotations

from typing import Any, Optional


def capture_bgr(tag_id: str, *, exposure_s: Optional[float] = None) -> Any:
    """Capture one analysis frame for ``tag_id``.

    Parameters
    ----------
    tag_id:
        Camera component id (for example ``"tag_22"``).
    exposure_s:
        Optional exposure time in seconds; ``None`` means use the camera's
        current tunable / default.

    Returns
    -------
    Any
        Array-like image with shape ``(H, W, 3)``, dtype ``uint8``, BGR channel
        order. Callers must not assume a file path; keep pixels in memory for
        kernels.
    """
    raise NotImplementedError(f"Phase 6: capture BGR for {tag_id!r}")


def encode_jpeg(bgr: Any, *, quality: int = 80, scale: float = 1.0) -> bytes:
    """Encode an analysis frame into JPEG wire bytes.

    Parameters
    ----------
    bgr:
        HxWx3 ``uint8`` BGR image from :func:`capture_bgr`.
    quality:
        JPEG quality in ``1..100`` (capabilities ``wire_profiles`` default is
        typically 80).
    scale:
        Linear resize factor before encode (``1.0`` keeps native resolution).

    Returns
    -------
    bytes
        A complete JPEG bitstream (starts with ``FF D8``), suitable for
        ``image/jpeg`` HTTP responses.
    """
    raise NotImplementedError("Phase 6: BGR → JPEG wire encode")


def capture_jpeg(tag_id: str, *, profile: str | None = None) -> bytes:
    """Capture and encode one wire JPEG using a named wire profile.

    Parameters
    ----------
    tag_id:
        Camera component id.
    profile:
        Key into ``capabilities.wire_profiles`` (for example ``"default"``).
        When ``None``, use the channel's ``default_profile``.

    Returns
    -------
    bytes
        JPEG bytes as produced by :func:`encode_jpeg` after applying the
        profile's scale and quality.
    """
    raise NotImplementedError(
        f"Phase 6: capture JPEG for {tag_id!r} profile={profile!r}"
    )


def localize_components(args: dict[str, Any]) -> dict[str, Any]:
    """Deprecated alias: RECORD_TUNABLES for ``nominal_pose`` only."""
    return record_tunables(
        {
            "tag_ids": args.get("tag_ids"),
            "tunable_paths": ["nominal_pose"],
            "force_rescan": args.get("force_rescan", True),
        }
    )


def record_tunables(args: dict[str, Any]) -> dict[str, Any]:
    """Overwrite recordable tunables from the world (RECORD_TUNABLES).

    Pose recording must write **nominal_pose**, not a shadow reported_pose.
    """
    raise NotImplementedError("RECORD_TUNABLES — measure/overwrite named tunables")


def sync_runtime(args: dict[str, Any]) -> dict[str, Any]:
    """Boot/readiness macro: RECORD(recordable) + SET(set_at_init).

    Must succeed before the edge advertises READY.
    """
    raise NotImplementedError("SYNC_RUNTIME — required before edge READY")
'''

ADAPTERS_LIVE = '''\
"""Arm and serve Tier B live streams bound to measurable channels.

Live bytes are not a separate product language: Twin may fetch JPEG/MJPEG
only after ``START_LIVE_FEED`` for a channel listed under
``capabilities.telemetry_channels`` with ``requires_primitive`` set. Paths
such as ``/stream/tag_22/camera_image.jpg`` must call :func:`read_live_jpeg`
(or equivalent) and must return HTTP 409 (or contract refuse) when the
channel is not armed.
"""

from __future__ import annotations

from typing import Any, Optional


def arm_live_feed(
    channel: str,
    profile: str | None = None,
    exposure_time_ms: float | None = None,
) -> None:
    """Start producing wire frames for a capability channel.

    Parameters
    ----------
    channel:
        Channel key from ``capabilities.telemetry_channels`` (for example
        ``"tag_22.camera_image"``). May also arrive as ``measurable_id``.
    profile:
        Optional wire profile name from ``capabilities.wire_profiles``.
    exposure_time_ms:
        Optional preview (VEXP) exposure — must not write science CAP tunable.

    Returns
    -------
    None
        Success is silent; raise or let dispatch map errors to refused/failed
        execute responses. After return, :func:`is_live_armed` must be true
        for this channel (and aliases such as ``.mjpeg`` if you treat them
        as the same arm bit).
    """
    raise NotImplementedError(
        f"Phase 6: arm live feed channel={channel!r} profile={profile!r} "
        f"exposure_ms={exposure_time_ms!r}"
    )


def set_live_exposure(args: dict[str, Any]) -> dict[str, Any]:
    """Adjust preview VEXP while armed (SET_LIVE_EXPOSURE). Do not commit_tunable."""
    raise NotImplementedError("SET_LIVE_EXPOSURE — preview exposure only")


def disarm_live_feed(channel: str) -> None:
    """Stop producing wire frames for ``channel`` (END_LIVE_FEED).

    Parameters
    ----------
    channel:
        Same channel key passed to :func:`arm_live_feed`.

    Returns
    -------
    None
        Idempotent: disarming an already-idle channel is success.
    """
    raise NotImplementedError(f"Phase 6: disarm live feed channel={channel!r}")


def is_live_armed(channel: str) -> bool:
    """Return whether ``channel`` is currently allowed to emit wire frames.

    Parameters
    ----------
    channel:
        Telemetry channel key or measurable id alias.

    Returns
    -------
    bool
        ``True`` only while a matching START_LIVE_FEED is in effect.
    """
    raise NotImplementedError(f"Phase 6: query armed state for {channel!r}")


def read_live_jpeg(channel: str) -> bytes:
    """Return one current JPEG frame for an armed channel.

    Parameters
    ----------
    channel:
        Armed telemetry channel key.

    Returns
    -------
    bytes
        JPEG bitstream for immediate HTTP response. Must raise or signal
        failure when :func:`is_live_armed` is false so the HTTP layer can
        return 409 / LIVE_NOT_STARTED.
    """
    raise NotImplementedError(f"Phase 6: read live JPEG for {channel!r}")
'''

ADAPTERS_TELEOP = '''\
"""Per-component teleop lease and flat Tier A WebSocket samples.

Teleop is a leased session on one ``tag_id``: START_TELEOP acquires it,
TELEOP_JOG / TELEOP_GOTO move while held, and END_TELEOP releases it.
WebSocket payloads on the hot path are flat (``cmd``, ``tag_id``, ``axis``,
``val``, …) — nested ``payload`` envelopes must be rejected. Pose samples
published to the client should include ``epoch_ms`` from the edge clock.

This edge owns **hardware** arming and the WS path. Twin UI
``telemetry.teleop.active`` / ``ready`` are committed by the **coordinator**
after a successful remote START/END — do not invent Twin FSM writers here.
A flat ``{tag_id, active: true, ws_path}`` result is the contract shape.
"""

from __future__ import annotations

from typing import Any


def start_teleop(tag_id: str) -> dict[str, Any]:
    """Acquire the teleop lease for ``tag_id`` (START_TELEOP).

    Parameters
    ----------
    tag_id:
        Component that will accept jog/goto frames.

    Returns
    -------
    dict
        ``{"tag_id": str, "active": true, "ws_path": "/ws/teleop"}`` (path must
        match ``capabilities.telemetry_channels.teleop.path``). Refuse when
        the lab is busy, the tag is in storage, or another teleop is active,
        per lab safety policy. Twin UI session flags are committed by the
        coordinator after this returns successfully.
    """
    raise NotImplementedError(f"Phase 6: LiveControlSession start for {tag_id!r}")


def end_teleop(tag_id: str) -> dict[str, Any]:
    """Release the teleop lease for ``tag_id`` (END_TELEOP).

    Parameters
    ----------
    tag_id:
        Component whose session should end.

    Returns
    -------
    dict
        ``{"tag_id": str, "active": false}``. Idempotent when already released.
    """
    raise NotImplementedError(f"Phase 6: LiveControlSession end for {tag_id!r}")


def teleop_jog(args: dict[str, Any]) -> dict[str, Any]:
    """Apply one jog frame while teleop is active (TELEOP_JOG).

    Parameters
    ----------
    args:
        Flat execute/WS args: ``tag_id``, and either ``axis`` + ``val``
        (incremental) or absolute pose / motor fields your LiveControlSession
        already understands. Nested ``payload`` objects are not allowed.

    Returns
    -------
    dict
        Updated pose (and optional motor map), for example
        ``{"tag_id": str, "pose": {"x": float, "y": float, "rotation": float}}``.
    """
    raise NotImplementedError("Phase 6: teleop jog frame")


def teleop_goto(args: dict[str, Any]) -> dict[str, Any]:
    """Move to an absolute teleop target at configured speed (TELEOP_GOTO).

    Parameters
    ----------
    args:
        ``tag_id`` plus absolute ``target_pose`` and/or
        ``target_motor_positions``, and optional ``speed``.

    Returns
    -------
    dict
        Commanded or reached pose/motors for ``tag_id``.
    """
    raise NotImplementedError("Phase 6: teleop goto")


def read_pose_sample(tag_id: str) -> dict[str, Any]:
    """Build one Tier A WebSocket pose sample for ``tag_id``.

    Parameters
    ----------
    tag_id:
        Component currently under teleop (or last sampled).

    Returns
    -------
    dict
        Flat server message such as
        ``{"kind": "pose_sample", "tag_id": str, "x": float, "y": float,
        "rotation": float, "epoch_ms": int}`` matching
        ``teleop_ws_server.schema.json``.
    """
    raise NotImplementedError(f"Phase 6: publish pose sample for {tag_id!r}")
'''

ADAPTERS_TUNABLES = '''\
"""Edge-owned tunable writes that are not pose or motor macros.

Exposure and laser power are examples of fields the edge applies directly to
hardware. The coordinator may also expand APPLY_TUNABLES_PATCH into these
primitives; implementing them here keeps a single southbound path.
"""

from __future__ import annotations

from typing import Any


def set_exposure_time_ms(args: dict[str, Any]) -> dict[str, Any]:
    """Set camera exposure in milliseconds (SET_EXPOSURE).

    Parameters
    ----------
    args:
        ``tag_id`` / ``target_id`` of the camera and ``exposure_time_ms``
        (float or int, milliseconds).

    Returns
    -------
    dict
        ``{"tag_id": str, "exposure_time_ms": float}`` echoing the value in
        effect after the write.
    """
    raise NotImplementedError("Phase 6: camera exposure")


def set_output_power_mw(args: dict[str, Any]) -> dict[str, Any]:
    """Set laser (or source) output power in milliwatts (SET_LASER_OUTPUT).

    Parameters
    ----------
    args:
        ``tag_id`` / ``target_id`` of the source and ``output_power_mw``.

    Returns
    -------
    dict
        ``{"tag_id": str, "output_power_mw": float}`` after the write.
    """
    raise NotImplementedError("Phase 6: laser output power")
'''

ADAPTERS_OPTIMIZE = '''\
"""Closed-loop OPTIMIZE host that runs strategies on the edge.

OPTIMIZE is a single Edge Contract primitive whose ``args`` carry mode,
objective IR, kernels, and limits. The implementation may call observe,
motors, and motion helpers internally, but Twin and the SDK still only see
one ``/execute`` action. Prefer wrapping existing deathray strategies
(Cobyla / Newton / ensemble) rather than inventing a parallel API.
"""

from __future__ import annotations

from typing import Any


def optimize_component(args: dict[str, Any]) -> dict[str, Any]:
    """Run a closed-loop optimization for the target component (OPTIMIZE).

    Parameters
    ----------
    args:
        Execute parameters including ``tag_id`` / ``target_id``, ``mode``
        (for example ``"ensemble"``), compiled ``objective`` / strategy
        fields, optional ``kernels`` / ``kernel_packages``, and lab limits.
        Exact keys follow the coordinator job / command schema already used
        by the SDK ``run_optimize`` path.

    Returns
    -------
    dict
        Outcome summary: success flag, final score, iteration counts, and any
        measurable receipts the lab wants surfaced (JSON-serializable). Long
        runs should still stamp progress via whatever epoch/lab-state updates
        the edge already publishes.
    """
    raise NotImplementedError(
        "Phase 6: CobylaAlignmentStrategy_cloudlab / Newton / ensemble host"
    )
'''

ADAPTERS_OBSERVE = '''\
"""Orchestrate RECORD_MEASURABLES and EVAL_KERNEL on the edge.

Both primitives share the latch + vision path: open a latch, capture the
analysis form of declared measurables (BGR for cameras), optionally run a
kernel, then close the latch and return ``epoch_ms`` / ``latch_quality``.
Do not serve these through Twin-only shortcuts; the only public entry is
``POST /execute`` with the primitive name.
"""

from __future__ import annotations

from typing import Any


def record_measurables(tag_id: str, args: dict[str, Any]) -> dict[str, Any]:
    """Capture fresh measurables for one tag (RECORD_MEASURABLES).

    Parameters
    ----------
    tag_id:
        Component whose catalog-declared measurable fields should be sampled.
    args:
        Full execute ``args`` (may repeat ``tag_id``, exposure hints, field
        filters). Empty ``args`` means "record everything declared for the
        tag".

    Returns
    -------
    dict
        Structure suitable as execute ``result``, typically
        ``{"tag_id": str, "measurables": {field: value, ...}}`` where image
        fields are tensor envelopes or path/LazyRef metadata and scalars are
        numbers. The HTTP layer should attach ``epoch_ms`` and
        ``latch_quality`` via ``contract.completed``.
    """
    raise NotImplementedError(
        f"Phase 6: observe/latch measurables for {tag_id!r} (args={args!r})"
    )


def eval_kernel(kernel_id: str, args: dict[str, Any]) -> dict[str, Any]:
    """Capture, run a TorchScript kernel, and return scores (EVAL_KERNEL).

    Parameters
    ----------
    kernel_id:
        Kernel registry id (also present as ``args["kernel_id"]``).
    args:
        Execute args including ``tag_id`` / ``target_id``, ``field`` (often
        ``"camera_image"``), and optional kernel parameters.

    Returns
    -------
    dict
        Kernel outcome such as ``{"kernel_id": str, "scalar": float}`` and/or
        ``{"features": [float, ...], "passed": bool}``. Pair with latch
        metadata on the execute envelope; do not open a separate
        ``/kernel/probe`` route.
    """
    raise NotImplementedError(
        f"Phase 6: EVAL_KERNEL {kernel_id!r} via /execute only (args={args!r})"
    )
'''

SKELETON_MD = """\
# cloudlabs_edge function skeleton

This tree was generated by `cloudlabs-edge init`. In Phase 5 the process still
serves HTTP through the reference stub so `cloudlabs-edge certify` can pass
without motors. Every Python function under `adapters/`, `latch.py`, and
`kernel_host.py` is nonetheless the complete Phase 6 surface you will wire to
lab-automation (deathray). Read each module docstring for input and output
contracts; this page is only a map of where those contracts live.

## Layout

```text
cloudlabs_edge/
  main.py              # ASGI entry; Phase 5 stub, Phase 6 dispatch
  capabilities.json    # supported_primitives + channels + execution_threads
  contract.py          # completed / refused / failed envelopes
  latch.py             # epoch_ms and latch_quality
  kernel_host.py       # TorchScript on local BGR (provisioning ships working)
  dispatch.py          # primitive name → adapter callable
  bench/layout.json    # static geometry for GET /bench
  data/
    library.json       # full component library (GET /library)
    inventory.json     # tracked tags + placement (GET /inventory)
  adapters/
    motion.py          # MOVE_*, pick/hover/place, storage, REMOVE
    motors.py          # MOVE_MOTOR, setpoints, zero, home
    vision.py          # BGR analysis + JPEG wire + LOCALIZE_COMPONENTS
    live_feed.py       # START/END_LIVE_FEED + read_live_jpeg
    teleop.py          # lease, JOG/GOTO, pose_sample
    tunables.py        # SET_EXPOSURE, SET_LASER_OUTPUT
    optimize.py        # OPTIMIZE
    observe.py         # RECORD_MEASURABLES, EVAL_KERNEL
  .agents/             # tool-agnostic coaching for Phase 6 (humans + AI)
    README.md          # skill index
    skills/*/SKILL.md  # contract, tensors, camera-bringup, latency, kernels, frames, inventory
```

## `execution_threads` (Command Matrix topology)

Advertise **shared** columns only — the edge does not own queues:

```json
"execution_threads": [
  { "id": "arm.0", "kind": "arm" },
  { "id": "sense.0", "kind": "sense" }
]
```

Do **not** list `motor.1`, `motor.2`, … Inventory reuses local `motor_id` per
component (`tag_20` motor `1` ≠ `tag_11` motor `1`). The coordinator creates
`motor.<tag_id>.<motor_id>` columns when motor primitives are enqueued. See
`docs/COMMAND_MATRIX.md` in cloud-labs.

## Phase 6 coaching (`.agents/skills`)

Before filling an adapter, open the matching skill under `.agents/skills/` —
or point any assistant at that file. Skills are not a second API; they remind
you how to honor the contract (one mutation door, canonical measurables,
fail-loud frames, honest inventory, real-lab camera bring-up). Start from
[`.agents/README.md`](.agents/README.md).

## Suggested deathray targets

When you implement a function, start from the existing lab helper rather than
rewriting motion from scratch. OpticalExperiment `*_cloudlab` methods cover
most of `adapters/motion.py`; wifi steppers cover `adapters/motors.py`;
recorder CAP/GET_JPEG cover `adapters/vision.py` and `live_feed.py`;
`LiveControlSession` covers `adapters/teleop.py`; Cobyla/Newton/ensemble
strategies cover `adapters/optimize.py`. Latch and kernel host may be new thin
wrappers even if capture and TorchScript already exist elsewhere in the lab
repo.

## Phase 5 versus Phase 6

While Phase 5 is active, leave the `NotImplementedError` bodies in place and
keep `main.py` on `create_app`. When language review is signed off, implement
the adapters against deathray, then switch `/execute` to
`dispatch.dispatch_primitive` so Twin and the SDK keep calling the same
primitive names with no new side doors.
"""

README = """\
# cloudlabs_edge

Edge Contract v1 agent for backend `{backend_id}`. This folder is a full
function skeleton: it speaks the contract today through the
`cloudlabs_edge_dev` reference stub, and it already contains every adapter
signature Phase 6 must fill from lab-automation. It does not import
OpticalExperiment or recorder drivers yet.

Generated by::

    cloudlabs-edge init ./cloudlabs_edge --backend-id {backend_id}

Read [`SKELETON.md`](./SKELETON.md) for the layout overview, then open each
adapter module for long-form input and output documentation. For Phase 6
coaching (humans or any AI assistant), start at
[`.agents/README.md`](.agents/README.md).

## Install and run

```powershell
pip install -e <path-to-cloud-labs>/packages/cloudlabs_edge_dev
cd cloudlabs_edge
uvicorn main:app --host 0.0.0.0 --port 8100
cloudlabs-edge doctor --path .
cloudlabs-edge certify http://127.0.0.1:8100 --path . --profile stub
```

## Next (Phase 6)

Map each documented function to a deathray callable and implement
`adapters/` and `latch.py`, then point `/execute` at
`dispatch.dispatch_primitive` while keeping Twin and the SDK on the same
primitive names. `kernel_host.py` ships working, lab-independent
provisioning + TorchScript execution (fetch/verify/cache a `.pt`, then run it
on local BGR); customize only how a kernel's raw output maps to result fields.
`main.py` already derives `capabilities.features.torchscript_execution` from
`kernel_host.torch_available()`.
"""

OPTIMIZATION_INIT = '''\
"""Lab seams for edge optimization (capture + actuator router).

The general engine lives in ``cloudlabs_edge_dev.optimization`` (tensors, kernels,
metrics, stepper, session). Fill ``capture_impl`` and ``router_impl`` for this bench.
See ``docs/EDGE_OPTIMIZATION_PIPELINE.md`` Phase 2.
"""
'''

OPTIMIZATION_ROUTER_IMPL = '''\
"""Actuator router for closed-loop OPTIMIZE on this bench.

Wire continuous variables to ``adapters.motors.set_motor_setpoint`` and invasive
pose variables to a block-scoped motion helper. Bookkeeping is per block, not
per eval — see ``docs/EDGE_OPTIMIZATION_PIPELINE.md`` Phase 2.
"""

from __future__ import annotations

from typing import Mapping, Sequence

from cloudlabs_edge_dev.optimization import ActuatorRouter


class EdgeActuatorRouter(ActuatorRouter):
    def enter_continuous_block(self, variable_ids: Sequence[str]) -> None:
        raise NotImplementedError(
            "Phase 2: enter_continuous_block — clear path, no per-eval state churn"
        )

    def assert_optical_path_clear(self) -> None:
        """Phase 6: raise ClearanceError when the arm occludes the beam."""
        return None

    def enter_invasive_block(self, variable_ids: Sequence[str]) -> None:
        raise NotImplementedError(
            "Phase 2: enter_invasive_block — prepare touch-and-go for pose vars"
        )

    def apply_eval(
        self,
        physical_values: Mapping[str, float],
        *,
        block_id: str,
    ) -> None:
        raise NotImplementedError(
            f"Phase 2: apply_eval block={block_id!r} values={dict(physical_values)!r}"
        )

    def exit_block(self, block_id: str) -> None:
        raise NotImplementedError(f"Phase 2: exit_block {block_id!r}")
'''

OPTIMIZATION_CAPTURE_IMPL = '''\
"""Capture source for closed-loop OPTIMIZE on this bench.

Implement latched in-process reads (no JPEG / LazyRef per eval). See
``docs/EDGE_OPTIMIZATION_PIPELINE.md`` Phase 2.
"""

from __future__ import annotations

from cloudlabs_edge_dev.optimization import CaptureSource, EdgeTensor


class EdgeCaptureSource:
    """``CaptureSource`` over ``adapters.observe.capture_tensor`` (Phase 2)."""

    def capture(self, capture_id: str) -> EdgeTensor:
        raise NotImplementedError(
            f"Phase 2: capture({capture_id!r}) — latch + in-process BGR, no JPEG"
        )

    def read_measurable(self, path: str, *, tag_id: str) -> float:
        raise NotImplementedError(
            f"Phase 2: read_measurable({path!r}, tag_id={tag_id!r})"
        )


# Typing alias for Protocol checkers.
_: type[CaptureSource] = EdgeCaptureSource  # type: ignore[misc,assignment]
'''


def _starter_kernels_manifest() -> str:
    fixtures = Path(__file__).resolve().parent / "scaffold_fixtures" / "kernels" / "manifest.json"
    if fixtures.is_file():
        return fixtures.read_text(encoding="utf-8")
    return (
        '{\n  "schema_version": 1,\n  "kernels": []\n}\n'
    )


def init_edge(dest: Path, backend_id: str = "stub.default", force: bool = False) -> Path:
    dest = dest.resolve()
    if dest.exists() and any(dest.iterdir()) and not force:
        raise FileExistsError(
            f"{dest} is not empty; pass --force to overwrite scaffold files"
        )
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "adapters").mkdir(exist_ok=True)
    (dest / "bench").mkdir(exist_ok=True)
    (dest / "data").mkdir(exist_ok=True)
    (dest / "kernels").mkdir(exist_ok=True)
    (dest / "optimization").mkdir(exist_ok=True)

    files = {
        dest / "main.py": MAIN_PY,
        dest / "capabilities.json": CAPABILITIES_JSON.format(backend_id=backend_id),
        dest / "bench" / "layout.json": BENCH_JSON.format(backend_id=backend_id),
        dest / "data" / "library.json": LIBRARY_JSON,
        dest / "data" / "inventory.json": INVENTORY_JSON,
        dest / "contract_version.txt": "1.1.0\n",
        dest / "contract.py": CONTRACT_PY,
        dest / "latch.py": LATCH_PY,
        dest / "kernel_host.py": KERNEL_HOST_PY,
        dest / "dispatch.py": DISPATCH_PY,
        dest / "SKELETON.md": SKELETON_MD,
        dest / "adapters" / "__init__.py": ADAPTERS_INIT,
        dest / "adapters" / "motion.py": ADAPTERS_MOTION,
        dest / "adapters" / "motors.py": ADAPTERS_MOTORS,
        dest / "adapters" / "vision.py": ADAPTERS_VISION,
        dest / "adapters" / "live_feed.py": ADAPTERS_LIVE,
        dest / "adapters" / "teleop.py": ADAPTERS_TELEOP,
        dest / "adapters" / "tunables.py": ADAPTERS_TUNABLES,
        dest / "adapters" / "optimize.py": ADAPTERS_OPTIMIZE,
        dest / "adapters" / "observe.py": ADAPTERS_OBSERVE,
        dest / "optimization" / "__init__.py": OPTIMIZATION_INIT,
        dest / "optimization" / "router_impl.py": OPTIMIZATION_ROUTER_IMPL,
        dest / "optimization" / "capture_impl.py": OPTIMIZATION_CAPTURE_IMPL,
        dest / "kernels" / "manifest.json": _starter_kernels_manifest(),
        dest / "README.md": README.format(backend_id=backend_id),
    }
    for path, content in files.items():
        path.write_text(content, encoding="utf-8", newline="\n")
    # Copy starter TorchScript artifacts from scaffold_fixtures (Phase 5).
    fixtures = Path(__file__).resolve().parent / "scaffold_fixtures" / "kernels"
    if fixtures.is_dir():
        import shutil

        for pt in fixtures.glob("*.pt"):
            shutil.copy2(pt, dest / "kernels" / pt.name)
    write_agent_skills(dest)
    return dest
