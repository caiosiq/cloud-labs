"""APPLY_TUNABLES_PATCH macro — bulk commit with fixed step ordering.

Ordering (documented; do not reorder without updating this file):

  1. Bench hyperparameters (exposure, future laser fields)
  2. Motor setpoints (``nominal_motor_positions``)
  3. Table placement intent (``nominal_pose``, ``presence``, ``storage``)
  4. Motion / holding primitives last (pick, hover, place) — **not** applied
     from this macro unless explicitly listed in ``motion`` sub-dict.

Recipe / script convenience only; operators use per-field primitives in the UI.
"""
from __future__ import annotations

from typing import Any, Dict

from lab_communicator.base import LabCommunicator

from ..schemas import ApplyTunablesPatchBody


async def run_apply_tunables_patch(
    lab: LabCommunicator, cmd: ApplyTunablesPatchBody
) -> None:
    tid = cmd.target_id
    patch = cmd.parameters.patch or {}

    # --- Step 1: hyperparameters ---
    exp = patch.get("exposure_time_ms")
    if exp is not None:
        await lab.set_exposure_time_ms(tid, float(exp))

    # --- Step 2: motor setpoints ---
    motors = patch.get("nominal_motor_positions")
    if isinstance(motors, dict):
        for mid_raw, angle in motors.items():
            try:
                mid = int(mid_raw)
                await lab.set_motor_setpoint(tid, mid, float(angle))
            except (TypeError, ValueError):
                continue

    # --- Step 3: placement / storage (no motion primitives) ---
    pose = patch.get("nominal_pose")
    if isinstance(pose, dict) and pose:
        with lab._state_lock:
            entry = lab.current_state.get("components", {}).get(tid)
            if isinstance(entry, dict):
                tun = entry.setdefault("tunables", {})
                if isinstance(tun, dict):
                    tun["nominal_pose"] = dict(pose)
        lab._persist_state()

    presence = patch.get("presence")
    if isinstance(presence, str) and presence:
        with lab._state_lock:
            entry = lab.current_state.get("components", {}).get(tid)
            if isinstance(entry, dict):
                tun = entry.setdefault("tunables", {})
                if isinstance(tun, dict):
                    tun["presence"] = presence
        lab._persist_state()

    storage = patch.get("storage")
    if isinstance(storage, dict):
        with lab._state_lock:
            entry = lab.current_state.get("components", {}).get(tid)
            if isinstance(entry, dict):
                tun = entry.setdefault("tunables", {})
                if isinstance(tun, dict):
                    tun["storage"] = dict(storage)
        lab._persist_state()

    # Step 4 (motion) intentionally omitted — use explicit primitives.
