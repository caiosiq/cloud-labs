"""
Persistent software tracker for per-motor cumulative rotation (same units as MOVE_MOTOR distance).

Path is set once at startup from ``lab_view/motor_rotations.json`` (see
:func:`lab_communicator.shared.lab_view_config.bootstrap_lab_view`).
"""
import json
import os
import threading
from typing import Any, Dict, List

_lock = threading.RLock()

_store_path: str = ""


def reset_motor_rotation_store_for_tests() -> None:
    global _store_path
    _store_path = ""


def configure(path: str) -> None:
    """Bind the single JSON file backing this process (required before any read/write)."""
    global _store_path
    if not path or not isinstance(path, str):
        raise ValueError("motor_rotation_store.configure: path must be a non-empty string")
    _store_path = os.path.abspath(path)


def _path() -> str:
    if not _store_path:
        raise RuntimeError(
            "motor_rotation_store not configured; main must call configure(paths.motor_rotations_json)"
        )
    return _store_path


def _load_path(path: str) -> Dict[str, Any]:
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
    return _load_path(_path())


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
        path = _path()
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
        path = _path()
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


def set_rotations_for_tag(tag_id: str, rotations: Dict[str, Any]) -> None:
    """Replace stored angles for ``tag_id`` with the given motor_id(str)->deg map."""

    with _lock:
        path = _path()
        data = _load_path(path)
        tag = data.setdefault(tag_id, {})
        if not isinstance(tag, dict):
            tag = {}
            data[tag_id] = tag
        for raw_k, raw_v in (rotations or {}).items():
            key = str(raw_k)
            try:
                tag[key] = float(raw_v)
            except (TypeError, ValueError):
                continue
        _save_path(path, data)
