"""In-memory live pose buffer for TeleOp (not persisted in lab_state JSON)."""
from __future__ import annotations

import math
import threading
import time
from typing import Any, Callable, Dict, Optional


def _float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


class TeleopLivePoseStore:
    """Per-tag pose truth updated by a motion loop; read via HTTP poll only."""

    def __init__(
        self,
        *,
        on_motion_idle: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._lock = threading.RLock()
        self._poses: Dict[str, Dict[str, Any]] = {}
        self._targets: Dict[str, Dict[str, float]] = {}
        self._speeds: Dict[str, Dict[str, float]] = {}
        self._on_motion_idle = on_motion_idle
        self._motion_thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def start_session(self, tag_id: str, initial: Dict[str, Any]) -> None:
        with self._lock:
            self._poses[tag_id] = {
                "x": _float(initial.get("x")),
                "y": _float(initial.get("y")),
                "rotation": _float(initial.get("rotation")),
                "z": _float(initial.get("z", 40.0), 40.0),
                "ts_ms": time.time() * 1000.0,
            }
            self._targets.pop(tag_id, None)

    def stop_session(self, tag_id: str) -> None:
        with self._lock:
            self._poses.pop(tag_id, None)
            self._targets.pop(tag_id, None)
            self._speeds.pop(tag_id, None)

    def get_pose(self, tag_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            pose = self._poses.get(tag_id)
            if not pose:
                return None
            out = dict(pose)
            out["executing"] = tag_id in self._targets
            return out

    def is_executing(self, tag_id: str) -> bool:
        with self._lock:
            return tag_id in self._targets

    def set_goto(
        self,
        tag_id: str,
        target: Dict[str, float],
        speed: Dict[str, float],
    ) -> None:
        with self._lock:
            cur = self._poses.setdefault(
                tag_id,
                {"x": 0.0, "y": 0.0, "rotation": 0.0, "z": 40.0, "ts_ms": time.time() * 1000.0},
            )
            merged = dict(cur)
            for k, v in target.items():
                if k in ("x", "y", "rotation", "z"):
                    merged[k] = _float(v, merged.get(k, 0.0))
            self._targets[tag_id] = {k: merged[k] for k in target if k in ("x", "y", "rotation", "z")}
            if not self._targets[tag_id]:
                self._targets[tag_id] = dict(merged)
            self._speeds[tag_id] = {
                "linear_mm_s": max(0.1, _float(speed.get("linear_mm_s"), 25.0)),
                "angular_deg_s": max(0.1, _float(speed.get("angular_deg_s"), 15.0)),
            }
        self._ensure_motion_loop()

    def _ensure_motion_loop(self) -> None:
        if self._motion_thread is not None and self._motion_thread.is_alive():
            return
        self._stop.clear()
        t = threading.Thread(target=self._motion_loop, name="teleop-live-pose", daemon=True)
        self._motion_thread = t
        t.start()

    def _motion_loop(self) -> None:
        tick_s = 0.05
        while not self._stop.is_set():
            idle_tags: list[str] = []
            with self._lock:
                active = list(self._targets.keys())
            for tag_id in active:
                if self._tick_tag(tag_id, tick_s):
                    idle_tags.append(tag_id)
            for tag_id in idle_tags:
                if self._on_motion_idle:
                    try:
                        self._on_motion_idle(tag_id)
                    except Exception as e:  # noqa: BLE001
                        print(f"[teleop-live-pose] idle callback failed for {tag_id}: {e}")
            if not active:
                time.sleep(tick_s)
            else:
                time.sleep(tick_s)

    def _tick_tag(self, tag_id: str, dt: float) -> bool:
        """Advance one tick; return True when target reached."""
        with self._lock:
            cur = self._poses.get(tag_id)
            tgt = self._targets.get(tag_id)
            spd = self._speeds.get(tag_id)
            if not cur or not tgt or not spd:
                return True

            lin = spd["linear_mm_s"] * dt
            ang = spd["angular_deg_s"] * dt
            done = True

            for key, step in (("x", lin), ("y", lin), ("z", lin)):
                if key not in tgt:
                    continue
                delta = tgt[key] - cur[key]
                if abs(delta) <= step:
                    cur[key] = tgt[key]
                else:
                    cur[key] += math.copysign(step, delta)
                    done = False

            if "rotation" in tgt:
                delta = ((tgt["rotation"] - cur["rotation"] + 180) % 360) - 180
                if abs(delta) <= ang:
                    cur["rotation"] = tgt["rotation"]
                else:
                    cur["rotation"] += math.copysign(ang, delta)
                    done = False

            cur["ts_ms"] = time.time() * 1000.0

            if done:
                self._targets.pop(tag_id, None)
                self._speeds.pop(tag_id, None)
            return done
