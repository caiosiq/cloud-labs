"""
Persistent software tracker for per-motor cumulative rotation (same units as MOVE_MOTOR distance).

Motors without encoders: we assume motion only happens via this app's commands so angles stay consistent.

Files (under schemas/):
  - mock_motor_rotations.json — when LAB_MODE is MOCK (default)
  - real_motor_rotations.json — when LAB_MODE is REAL

This keeps mock testing from overwriting real-lab tracking. LAB_MODE is read when each
operation runs so it matches the process configuration (load .env before importing the app).

Legacy: if schemas/motor_rotations.json exists and the mode-specific file is missing, data is
copied from the legacy file on first access.
"""
import json
import os
import threading
from typing import Any, Dict, List

_SCHEMAS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "schemas"))
_LEGACY_PATH = os.path.join(_SCHEMAS_DIR, "motor_rotations.json")

_lock = threading.RLock()


def _mode_path() -> str:
    mode = (os.getenv("LAB_MODE") or "MOCK").upper()
    name = "real_motor_rotations.json" if mode == "REAL" else "mock_motor_rotations.json"
    return os.path.join(_SCHEMAS_DIR, name)


def _migrate_legacy_if_needed(target_path: str) -> None:
    if os.path.exists(target_path):
        return
    if not os.path.exists(_LEGACY_PATH):
        return
    try:
        with open(_LEGACY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        with open(target_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except (json.JSONDecodeError, OSError):
        pass


def _load_path(path: str) -> Dict[str, Any]:
    _migrate_legacy_if_needed(path)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _save_path(path: str, data: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _load() -> Dict[str, Any]:
    return _load_path(_mode_path())


def get_angle(tag_id: str, motor_id: int) -> float:
    with _lock:
        data = _load()
        tag = data.get(tag_id) or {}
        if not isinstance(tag, dict):
            return 0.0
        v = tag.get(str(motor_id), 0.0)
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0


def add_delta(tag_id: str, motor_id: int, delta: float) -> float:
    """Add delta to stored angle; returns new cumulative angle."""
    with _lock:
        path = _mode_path()
        data = _load_path(path)
        tag = data.setdefault(tag_id, {})
        if not isinstance(tag, dict):
            tag = {}
            data[tag_id] = tag
        key = str(motor_id)
        cur = float(tag.get(key, 0.0) or 0.0)
        nxt = cur + float(delta)
        tag[key] = nxt
        _save_path(path, data)
        return nxt


def set_zero(tag_id: str, motor_id: int) -> None:
    """Declare current physical position as angle 0 (no hardware move)."""
    with _lock:
        path = _mode_path()
        data = _load_path(path)
        tag = data.setdefault(tag_id, {})
        if not isinstance(tag, dict):
            tag = {}
            data[tag_id] = tag
        tag[str(motor_id)] = 0.0
        _save_path(path, data)


def get_rotations_for_motor_ids(tag_id: str, motor_ids: List[int]) -> Dict[str, float]:
    """JSON-friendly map motor_id string -> cumulative angle for lab-state."""
    with _lock:
        data = _load()
        tag = data.get(tag_id) or {}
        if not isinstance(tag, dict):
            tag = {}
        out: Dict[str, float] = {}
        for mid in motor_ids:
            key = str(mid)
            v = tag.get(key, 0.0)
            try:
                out[key] = float(v)
            except (TypeError, ValueError):
                out[key] = 0.0
        return out
