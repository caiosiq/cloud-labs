"""Resolve ``LAB_VIEW_PATH`` (per lab deployment) and required JSON bundles.

Everything that used to scatter across ``schemas/``, repo-root ``states/``, and recipe roots now hangs off the
resolved directory configured at process start.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Mapping, Optional, Tuple

_LINE_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

_lab_paths_singleton: Optional["LabViewPaths"] = None
_lab_manifest_singleton: Optional["LabViewManifest"] = None
_project_root_cached: Optional[str] = None


@dataclass(frozen=True)
class LabViewPaths:
    root_dir: str
    layout_json: str
    laser_lines_json: str
    component_library_json: str
    active_catalog_json: str
    motor_rotations_json: str
    lab_state_json: str
    stored_intent_json: str
    session_checkpoint_json: str
    recipes_dir: str
    states_dir: str
    camera_captures_dir: str
    table_cam_preview_json: str
    lab_manifest_json: str


@dataclass(frozen=True)
class LabViewManifest:
    """Deployment identity for this lab view bundle (``lab_manifest.json``)."""

    communicator: str
    lab_mode: str
    lab_automation_path: Optional[str] = None
    session_checkpoint: bool = True
    reconciliation_position_mm: float = 2.0
    reconciliation_yaw_deg: float = 5.0
    reconciliation_stale_warning_hours: float = 168.0
    #: Phase 8 (§16.5) per-component TELEOP safety knobs.
    #:
    #: - ``teleop_require_lab_idle``: when ``True``, ``START_TELEOP``
    #:   refuses unless the lab's ``system_status`` is IDLE. Default
    #:   ``False`` keeps per-component concurrency permissive (operator
    #:   can teleop component A while OPTIMIZE is running on B).
    #: - ``teleop_ttl_ms``: stale-lease TTL. If the lease is not refreshed
    #:   within this window (start, ready, jog, or keepalive), the sweeper
    #:   auto-clears the session. Default 300000ms (5 min). Set to 0 to
    #:   disable the sweeper (only explicit ``END_TELEOP`` ends a session).
    teleop_require_lab_idle: bool = False
    teleop_ttl_ms: int = 300_000

    def as_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "communicator": self.communicator,
            "lab_mode": self.lab_mode,
            "session_checkpoint": self.session_checkpoint,
            "session_reconciliation": {
                "position_mm": self.reconciliation_position_mm,
                "yaw_deg": self.reconciliation_yaw_deg,
                "stale_warning_hours": self.reconciliation_stale_warning_hours,
            },
            "teleop_safety": {
                "require_lab_idle": self.teleop_require_lab_idle,
                "teleop_ttl_ms": self.teleop_ttl_ms,
            },
        }
        if self.lab_automation_path:
            out["lab_automation_path"] = self.lab_automation_path
        return out


@dataclass(frozen=True)
class TableCamPreviewConfig:
    """Live preview poll / recorder JPEG tuning (from ``table_cam_preview.json``)."""

    scale: float = 0.75
    jpeg_quality: int = 72
    target_fps: int = 144
    max_inflight_requests: int = 3
    teleop_scale: float = 0.25
    teleop_jpeg_quality: int = 50
    teleop_poll_fps: int = 20
    teleop_fetch_timeout_s: float = 0.08
    teleop_stream_drain_frames: int = 12

    def as_dict(self) -> Dict[str, Any]:
        return {
            "scale": self.scale,
            "jpeg_quality": self.jpeg_quality,
            "target_fps": self.target_fps,
            "max_inflight_requests": self.max_inflight_requests,
            "teleop_scale": self.teleop_scale,
            "teleop_jpeg_quality": self.teleop_jpeg_quality,
            "teleop_poll_fps": self.teleop_poll_fps,
            "teleop_fetch_timeout_s": self.teleop_fetch_timeout_s,
            "teleop_stream_drain_frames": self.teleop_stream_drain_frames,
        }

    def profile(self, name: str) -> Dict[str, Any]:
        """Resolved tuning for ``default`` or ``teleop`` live-feed profile."""
        if str(name).strip().lower() == "teleop":
            return {
                "scale": self.teleop_scale,
                "jpeg_quality": self.teleop_jpeg_quality,
                "poll_fps": self.teleop_poll_fps,
                "fetch_timeout_s": self.teleop_fetch_timeout_s,
                "stream_drain_frames": self.teleop_stream_drain_frames,
            }
        return {
            "scale": self.scale,
            "jpeg_quality": self.jpeg_quality,
            "poll_fps": min(30, self.target_fps),
            "fetch_timeout_s": 0.45,
            "stream_drain_frames": 6,
        }


_DEFAULT_TABLE_CAM_PREVIEW = TableCamPreviewConfig()


def get_lab_view_paths() -> LabViewPaths:
    if _lab_paths_singleton is None:
        raise RuntimeError("lab_view bootstrap did not run; call bootstrap_lab_view() from main.")
    return _lab_paths_singleton


def get_lab_view_paths_optional() -> Optional[LabViewPaths]:
    return _lab_paths_singleton


def get_lab_manifest() -> LabViewManifest:
    if _lab_manifest_singleton is None:
        raise RuntimeError("lab_view bootstrap did not run; call bootstrap_lab_view() from main.")
    return _lab_manifest_singleton


def get_lab_automation_path() -> Optional[str]:
    """Absolute path to the ``lab_automation`` package directory, if configured."""
    return get_lab_manifest().lab_automation_path


def reset_lab_viewpaths_for_tests() -> None:
    """Test-only — allow loading a fresh ``LAB_VIEW_PATH`` bundle in-process."""
    global _lab_paths_singleton, _lab_manifest_singleton, _project_root_cached
    _lab_paths_singleton = None
    _lab_manifest_singleton = None
    _project_root_cached = None


def _resolve_project_relative(project_root: str, raw: str) -> str:
    p = (raw or "").strip()
    if not p:
        return ""
    if os.path.isabs(p):
        return os.path.abspath(p)
    return os.path.abspath(os.path.join(project_root, p))


def _manifest_bool(doc: Mapping[str, Any], key: str, default: bool) -> bool:
    if key not in doc:
        return default
    v = doc[key]
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("1", "true", "yes", "on"):
            return True
        if s in ("0", "false", "no", "off"):
            return False
    return bool(v)


def _infer_communicator_from_bundle_path(bundle_root: str) -> str:
    norm = bundle_root.replace("\\", "/").lower()
    if "/mock/" in norm or norm.endswith("/mock/lab_view") or "/mock/lab_view" in norm:
        return "mock"
    if "/real/" in norm:
        return "real"
    return "mock"


def _load_lab_manifest(paths: LabViewPaths, project_root: str) -> LabViewManifest:
    from lab_communicator.shared.communicator_factory import known_communicator_ids

    p = paths.lab_manifest_json
    if not os.path.isfile(p):
        inferred = _infer_communicator_from_bundle_path(paths.root_dir)
        doc: Dict[str, Any] = {
            "version": 1,
            "communicator": inferred,
            "description": "Which lab_communicator backend this bundle uses.",
            "session_checkpoint": True,
        }
        if inferred == "real":
            doc["lab_automation_path"] = "../lab_automation"
            doc["session_reconciliation"] = {
                "position_mm": 8,
                "yaw_deg": 10,
                "stale_warning_hours": 168,
            }
        else:
            doc["session_reconciliation"] = {
                "position_mm": 2,
                "yaw_deg": 5,
                "stale_warning_hours": 168,
            }
        atomic_write_json(p, doc)
        print(f"[CONFIG] Created default lab_manifest.json ({p})", flush=True)

    with open(p, "r", encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        raise SystemExit(f"[CONFIG] lab_manifest.json must be a JSON object: {p}")

    comm = str(raw.get("communicator", "")).strip().lower()
    if not comm:
        raise SystemExit(f"[CONFIG] lab_manifest.json missing required field 'communicator': {p}")
    if comm not in known_communicator_ids():
        raise SystemExit(
            f"[CONFIG] lab_manifest.json communicator {comm!r} is not supported "
            f"(use one of: {', '.join(sorted(known_communicator_ids()))})"
        )

    lab_auto: Optional[str] = None
    raw_auto = raw.get("lab_automation_path")
    if raw_auto is not None and str(raw_auto).strip():
        lab_auto = _resolve_project_relative(project_root, str(raw_auto))
        if not os.path.isdir(lab_auto):
            raise SystemExit(
                f"[CONFIG] lab_manifest.json lab_automation_path does not exist: {lab_auto}"
            )

    if comm == "real" and not lab_auto:
        raise SystemExit(
            f"[CONFIG] lab_manifest.json for communicator 'real' requires "
            f"'lab_automation_path' (path to the lab_automation package directory): {p}"
        )

    pos_mm, yaw_deg, stale_h = _session_reconciliation_fields(raw, comm)
    teleop_idle, teleop_ttl = _teleop_safety_fields(raw)
    return LabViewManifest(
        communicator=comm,
        lab_mode=comm.upper(),
        lab_automation_path=lab_auto,
        session_checkpoint=_manifest_bool(raw, "session_checkpoint", True),
        reconciliation_position_mm=pos_mm,
        reconciliation_yaw_deg=yaw_deg,
        reconciliation_stale_warning_hours=stale_h,
        teleop_require_lab_idle=teleop_idle,
        teleop_ttl_ms=teleop_ttl,
    )


def _session_reconciliation_fields(
    raw: Mapping[str, Any], communicator: str
) -> Tuple[float, float, float]:
    """Parse ``session_reconciliation`` block (or legacy flat keys) from lab_manifest."""

    def _float_val(src: Mapping[str, Any], key: str, default: float) -> float:
        try:
            return float(src.get(key, default))
        except (TypeError, ValueError):
            return default

    def_pos = 8.0 if communicator == "real" else 2.0
    def_yaw = 10.0 if communicator == "real" else 5.0
    block = raw.get("session_reconciliation")
    if isinstance(block, dict):
        return (
            _float_val(block, "position_mm", def_pos),
            _float_val(block, "yaw_deg", def_yaw),
            _float_val(block, "stale_warning_hours", 168.0),
        )
    return (
        _float_val(raw, "reconciliation_position_mm", def_pos),
        _float_val(raw, "reconciliation_yaw_deg", def_yaw),
        _float_val(raw, "reconciliation_stale_warning_hours", 168.0),
    )


def _teleop_safety_fields(raw: Mapping[str, Any]) -> Tuple[bool, int]:
    """Parse ``teleop_safety`` block from lab_manifest.

    Phase 8 / §16.5. Block layout::

        "teleop_safety": {
            "require_lab_idle": false,
            "teleop_ttl_ms": 300000
        }

    Both keys are optional with safe defaults. ``teleop_ttl_ms`` of 0
    disables the stale-lease sweeper.
    """
    block = raw.get("teleop_safety")
    if not isinstance(block, dict):
        return (False, 300_000)
    require_idle = block.get("require_lab_idle", False)
    if isinstance(require_idle, str):
        require_idle = require_idle.strip().lower() in ("1", "true", "yes", "on")
    else:
        require_idle = bool(require_idle)
    try:
        ttl_ms = int(block.get("teleop_ttl_ms", 300_000))
    except (TypeError, ValueError):
        ttl_ms = 300_000
    if ttl_ms < 0:
        ttl_ms = 0
    return (require_idle, ttl_ms)


def _apply_manifest_to_process_env(manifest: LabViewManifest) -> None:
    os.environ["LAB_MODE"] = manifest.lab_mode
    if manifest.lab_automation_path:
        os.environ["LAB_AUTOMATION_PATH"] = manifest.lab_automation_path
    else:
        os.environ.pop("LAB_AUTOMATION_PATH", None)


def bootstrap_lab_view(project_root: str) -> LabViewPaths:
    """Load ``LAB_VIEW_PATH``, configure storage geometry, register paths.

    No legacy fallbacks: missing required files abort startup loudly.
    """
    global _lab_paths_singleton, _lab_manifest_singleton, _project_root_cached

    _project_root_cached = os.path.abspath(project_root)

    raw = (os.getenv("LAB_VIEW_PATH") or "").strip()
    if not raw:
        raise SystemExit(
            "[CONFIG] LAB_VIEW_PATH is required in .env — absolute or project-relative path "
            'to your lab bundle directory (must contain lab_manifest.json, layout.json, '
            "laser_lines.json, component_library.json, active_catalog.json)."
        )

    root = _resolve_project_relative(project_root, raw)
    if not os.path.isdir(root):
        raise SystemExit(f"[CONFIG] LAB_VIEW_PATH does not exist or is not a directory: {root}")

    paths = LabViewPaths(
        root_dir=root,
        layout_json=os.path.join(root, "layout.json"),
        laser_lines_json=os.path.join(root, "laser_lines.json"),
        component_library_json=os.path.join(root, "component_library.json"),
        active_catalog_json=os.path.join(root, "active_catalog.json"),
        motor_rotations_json=os.path.join(root, "motor_rotations.json"),
        lab_state_json=os.path.join(root, "lab_state.json"),
        stored_intent_json=os.path.join(root, "stored_intent.json"),
        session_checkpoint_json=os.path.join(root, "session_last_lab_state.json"),
        recipes_dir=os.path.join(root, "recipes"),
        states_dir=os.path.join(root, "states"),
        camera_captures_dir=os.path.join(root, "camera_captures"),
        table_cam_preview_json=os.path.join(root, "table_cam_preview.json"),
        lab_manifest_json=os.path.join(root, "lab_manifest.json"),
    )

    mandatory = (
        paths.layout_json,
        paths.laser_lines_json,
        paths.component_library_json,
        paths.active_catalog_json,
        paths.motor_rotations_json,
    )
    missing = [p for p in mandatory if not os.path.isfile(p)]
    if missing:
        raise SystemExit(
            "[CONFIG] lab_view bundle incomplete; missing:\n  " + "\n  ".join(missing)
        )

    with open(paths.layout_json, "r", encoding="utf-8") as f:
        layout_document = json.load(f)
    if not isinstance(layout_document, dict):
        raise SystemExit(f"[CONFIG] layout.json must contain a JSON object: {paths.layout_json}")

    from lab_model.domain.storage_region import configure_from_layout_document

    configure_from_layout_document(layout_document)

    os.makedirs(paths.recipes_dir, exist_ok=True)
    os.makedirs(paths.states_dir, exist_ok=True)
    os.makedirs(paths.camera_captures_dir, exist_ok=True)

    if not os.path.isfile(paths.stored_intent_json):
        atomic_write_json(
            paths.stored_intent_json,
            {
                "version": 1,
                "updated_at": datetime.now().isoformat(),
                "stored": {},
            },
        )

    if not os.path.isfile(paths.table_cam_preview_json):
        atomic_write_json(
            paths.table_cam_preview_json,
            {
                "version": 1,
                "description": (
                    "Table camera live preview: recorder JPEG scale/quality and UI poll rate."
                ),
                **_DEFAULT_TABLE_CAM_PREVIEW.as_dict(),
            },
        )

    manifest = _load_lab_manifest(paths, project_root)
    _lab_manifest_singleton = manifest
    _apply_manifest_to_process_env(manifest)
    print(
        f"[CONFIG] lab_manifest: communicator={manifest.communicator!r} "
        f"lab_mode={manifest.lab_mode}"
        + (
            f" lab_automation_path={manifest.lab_automation_path}"
            if manifest.lab_automation_path
            else ""
        ),
        flush=True,
    )

    _lab_paths_singleton = paths

    from lab_model import measurables as _measurables  # noqa: F401
    from lab_model import tunables as _tunables  # noqa: F401
    from lab_model.catalog.schema import load_component_library_rows
    from lab_model.platform import validate_communicator_backend, validate_platform_integrity

    try:
        rows = load_component_library_rows(paths.component_library_json)
        validate_platform_integrity(rows)
        validate_communicator_backend(manifest.communicator)
    except Exception as exc:
        raise SystemExit(f"[CONFIG] platform integrity check failed: {exc}") from exc

    return paths


def load_table_cam_preview_config(
    paths: Optional[LabViewPaths] = None,
) -> TableCamPreviewConfig:
    """Read ``table_cam_preview.json`` from the active lab view (defaults if missing/invalid)."""
    p = (paths or get_lab_view_paths()).table_cam_preview_json
    if not os.path.isfile(p):
        return _DEFAULT_TABLE_CAM_PREVIEW
    try:
        with open(p, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"[CONFIG] table_cam_preview.json unreadable ({p}): {e}; using defaults")
        return _DEFAULT_TABLE_CAM_PREVIEW
    if not isinstance(raw, dict):
        print(f"[CONFIG] table_cam_preview.json must be an object: {p}; using defaults")
        return _DEFAULT_TABLE_CAM_PREVIEW
    return _table_cam_preview_from_mapping(raw)


def _table_cam_preview_from_mapping(doc: Mapping[str, Any]) -> TableCamPreviewConfig:
    def _float(key: str, default: float, lo: float, hi: float) -> float:
        try:
            v = float(doc.get(key, default))
        except (TypeError, ValueError):
            v = default
        return max(lo, min(hi, v))

    def _int(key: str, default: int, lo: int, hi: int) -> int:
        try:
            v = int(doc.get(key, default))
        except (TypeError, ValueError):
            v = default
        return max(lo, min(hi, v))

    teleop = doc.get("teleop") if isinstance(doc.get("teleop"), Mapping) else {}

    def _teleop_float(key: str, default: float, lo: float, hi: float) -> float:
        if key in teleop:
            try:
                v = float(teleop.get(key, default))
            except (TypeError, ValueError):
                v = default
            return max(lo, min(hi, v))
        flat = f"teleop_{key}"
        return _float(flat, default, lo, hi)

    def _teleop_int(key: str, default: int, lo: int, hi: int) -> int:
        if key in teleop:
            try:
                v = int(teleop.get(key, default))
            except (TypeError, ValueError):
                v = default
            return max(lo, min(hi, v))
        flat = f"teleop_{key}"
        return _int(flat, default, lo, hi)

    return TableCamPreviewConfig(
        scale=_float("scale", _DEFAULT_TABLE_CAM_PREVIEW.scale, 0.1, 1.0),
        jpeg_quality=_int(
            "jpeg_quality", _DEFAULT_TABLE_CAM_PREVIEW.jpeg_quality, 40, 95
        ),
        target_fps=_int("target_fps", _DEFAULT_TABLE_CAM_PREVIEW.target_fps, 8, 240),
        max_inflight_requests=_int(
            "max_inflight_requests",
            _DEFAULT_TABLE_CAM_PREVIEW.max_inflight_requests,
            1,
            8,
        ),
        teleop_scale=_teleop_float(
            "scale", _DEFAULT_TABLE_CAM_PREVIEW.teleop_scale, 0.05, 1.0
        ),
        teleop_jpeg_quality=_teleop_int(
            "jpeg_quality", _DEFAULT_TABLE_CAM_PREVIEW.teleop_jpeg_quality, 30, 95
        ),
        teleop_poll_fps=_teleop_int(
            "poll_fps", _DEFAULT_TABLE_CAM_PREVIEW.teleop_poll_fps, 8, 60
        ),
        teleop_fetch_timeout_s=_teleop_float(
            "fetch_timeout_s",
            _DEFAULT_TABLE_CAM_PREVIEW.teleop_fetch_timeout_s,
            0.02,
            1.0,
        ),
        teleop_stream_drain_frames=_teleop_int(
            "stream_drain_frames",
            _DEFAULT_TABLE_CAM_PREVIEW.teleop_stream_drain_frames,
            1,
            48,
        ),
    )


def load_layout_document() -> Dict[str, Any]:
    p = get_lab_view_paths().layout_json
    with open(p, "r", encoding="utf-8") as f:
        doc = json.load(f)
    if not isinstance(doc, dict):
        raise ValueError(f"layout.json invalid: {p}")
    return doc


def atomic_write_json(path: str, data: Any) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def read_laser_lines_doc() -> Dict[str, Any]:
    path = get_lab_view_paths().laser_lines_json
    if not os.path.isfile(path):
        raise FileNotFoundError(f"laser_lines.json missing: {path}")
    with open(path, "r", encoding="utf-8") as f:
        doc = json.load(f)
    if not isinstance(doc, dict):
        raise ValueError(f"laser_lines.json must be a JSON object: {path}")
    return doc


def write_laser_lines_doc(doc: Dict[str, Any]) -> None:
    atomic_write_json(get_lab_view_paths().laser_lines_json, doc)


def two_points_to_ab(
    p1: Dict[str, Any], p2: Dict[str, Any]
) -> Optional[Tuple[float, float]]:
    """Return (a, b) for x = a*y + b in lab mm, or vertical (0, x0). None if degenerate."""
    try:
        x1 = float(p1["x"])
        y1 = float(p1["y"])
        x2 = float(p2["x"])
        y2 = float(p2["y"])
    except (TypeError, KeyError, ValueError):
        return None
    if abs(x2 - x1) < 1e-9 and abs(y2 - y1) < 1e-9:
        return None
    if abs(x2 - x1) < 1e-9:
        return 0.0, x1
    if abs(y2 - y1) < 1e-9:
        return None
    a = (x2 - x1) / (y2 - y1)
    b = x1 - a * y1
    return float(a), float(b)


def laser_line_coeffs_from_doc(doc: Dict[str, Any], lab_mode: str) -> Dict[str, Any]:
    """Legacy single-line coefficients for GET /api/laser-line (snap reference)."""
    lines = doc.get("lines") or []
    snap_id = doc.get("snap_line_id")
    by_id = {
        ln["id"]: ln
        for ln in lines
        if isinstance(ln, dict) and isinstance(ln.get("id"), str)
    }
    chosen = None
    if snap_id and snap_id in by_id:
        cand = by_id[snap_id]
        if cand.get("enabled", True):
            chosen = cand
    if chosen is None:
        for ln in lines:
            if not isinstance(ln, dict):
                continue
            if ln.get("enabled", True) and ln.get("p1") and ln.get("p2"):
                chosen = ln
                break
    if chosen is None:
        return {
            "a": 0.0,
            "b": 0.0,
            "source": "lab_view",
            "loaded": False,
            "lab_mode": lab_mode,
        }
    ab = two_points_to_ab(chosen["p1"], chosen["p2"])
    if ab is None:
        return {
            "a": 0.0,
            "b": 0.0,
            "source": "lab_view",
            "loaded": False,
            "lab_mode": lab_mode,
            "snap_line_id": chosen.get("id"),
        }
    a, b = ab
    return {
        "a": a,
        "b": b,
        "source": "lab_view",
        "loaded": True,
        "lab_mode": lab_mode,
        "snap_line_id": chosen.get("id"),
    }


def line_id_pattern() -> re.Pattern[str]:
    return _LINE_ID_RE
