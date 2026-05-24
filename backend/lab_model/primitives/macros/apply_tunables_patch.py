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

    from lab_model.tunables import exposure_time_ms as exposure_tunable
    from lab_model.tunables import nominal_motor_positions as motor_tunable
    from lab_model.tunables import nominal_pose as pose_tunable

    # --- Step 1: hyperparameters ---
    exp = patch.get("exposure_time_ms")
    if exp is not None:
        await exposure_tunable.apply(lab, tid, float(exp))

    laser_pwr = patch.get("output_power_mw")
    if laser_pwr is not None:
        from lab_model.tunables import output_power_mw as laser_tunable

        await laser_tunable.apply(lab, tid, float(laser_pwr))

    # --- Step 2: motor setpoints ---
    motors = patch.get("nominal_motor_positions")
    if isinstance(motors, dict):
        for mid_raw, angle in motors.items():
            try:
                mid = int(mid_raw)
                await motor_tunable.apply(lab, tid, mid, float(angle))
            except (TypeError, ValueError):
                continue

    # --- Step 3: placement / storage (no motion primitives) ---
    pose = patch.get("nominal_pose")
    if isinstance(pose, dict) and pose:
        await pose_tunable.apply(lab, tid, pose)

    presence = patch.get("presence")
    if isinstance(presence, str) and presence:
        from lab_model.domain.component import tunables_bucket

        with lab._state_lock:
            entry = lab.current_state.get("components", {}).get(tid)
            if isinstance(entry, dict):
                tunables_bucket(entry)["presence"] = presence
        lab._persist_state()

    storage = patch.get("storage")
    if isinstance(storage, dict):
        from lab_model.domain.component import tunables_bucket

        with lab._state_lock:
            entry = lab.current_state.get("components", {}).get(tid)
            if isinstance(entry, dict):
                tunables_bucket(entry)["storage"] = dict(storage)
        lab._persist_state()

    # Step 4 (motion) intentionally omitted — use explicit primitives.
