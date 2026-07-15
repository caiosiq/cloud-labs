"""Placement-iteration UI updates (ghost / physical phases).

Newton-style placement strategies report incremental progress by
calling a ``progress_callback`` for each sub-move. Cloud-labs translates
those callbacks into snapshot mutations that the UI's canvas reads
back: a *ghost* update repaints the planned target before the move
starts, a *physical* update repaints the observed pose after the move
completes.

The mutation logic is identical between any backend that wants Newton-
style progress -- the only thing that varies between backends is the
XY frame the strategy is reporting in. Real reports robot-frame XY
that we transform back to lab frame; mock (if it ever supports Newton-
style progress) would report lab-frame XY directly. We pass the
transform in as a callable (``xy_robot_to_lab``) so this module never
has to know which backend it's running under.

Used today only by :mod:`lab_communicator.real.optimization` (Newton
place hook). Lives in ``shared/`` because the format of the snapshot
mutation -- ``tunables.reported_pose`` vs ``tunables.nominal_pose``, the
``placement.mode`` value -- is owned by ``lab_model`` and is the same
contract every backend speaks.

Architectural rule (``communicator_refactor.md`` §5.1): pure stdlib +
``lab_model``. No ``lab_automation``, no :mod:`lab_communicator.base`,
no real/mock backends.
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Any, Callable, Dict, Optional, Tuple

from lab_model.domain.component import (
    PRESENCE_BREADBOARD,
    default_measurables,
    default_tunables,
    set_presence_and_storage,
)


XYTransform = Callable[[float, float], Tuple[float, float]]
"""``(x, y) -> (x_out, y_out)`` -- frame conversion for incoming XY pairs.

Real passes ``robot_table_xy_to_lab_xy``; mock would pass the identity
transform. The strategy's ``target_x``/``target_y`` are reported in
whatever frame the backend speaks; this transform turns them into the
lab frame the UI canvas wants.
"""


def ui_pose_for_placement_tick(
    current_state: Dict[str, Any],
    state_lock: threading.Lock,
    tag_id: str,
    target_x: Optional[float],
    target_y: Optional[float],
    *,
    xy_robot_to_lab: XYTransform,
) -> Dict[str, float]:
    """Build the lab-frame pose dict for one Newton sub-move.

    Strategy: take XY from the strategy's reported ``target_x`` /
    ``target_y`` (after running it through ``xy_robot_to_lab``); keep
    rotation from the component's current ``measurables.pose``. We
    deliberately do NOT use the strategy's ``angle`` parameter -- the
    robot's rotvec representation is not the same as the UI's top-down
    rotation (e.g. 270 deg vs ~29 deg), and parsing it would misalign
    the ghost vs solid renderings.

    Roll / pitch / yaw, when present in the existing measurables.pose,
    are passed through so the UI can derive rz from yaw for top-down
    rendering. ``target_x``/``target_y`` may be ``None`` (some strategy
    callbacks fire mid-iteration without a fresh target), in which
    case we keep the existing pose's XY.
    """
    with state_lock:
        comp_entry = (current_state.get("components") or {}).get(tag_id)
        pose = dict(((comp_entry or {}).get("measurables") or {}).get("pose") or {})
    if target_x is not None and target_y is not None:
        nx, ny = xy_robot_to_lab(float(target_x), float(target_y))
    else:
        nx = float(pose.get("x", 0.0))
        ny = float(pose.get("y", 0.0))
    rot = float(pose.get("rotation", 0.0) or 0.0)
    out: Dict[str, float] = {"x": nx, "y": ny, "rotation": rot}
    for key in ("roll", "pitch", "yaw"):
        if key in pose and pose[key] is not None:
            try:
                out[key] = float(pose[key])
            except (TypeError, ValueError):
                pass
    return out


def apply_placement_ui_phase(
    current_state: Dict[str, Any],
    state_lock: threading.Lock,
    tag_id: str,
    phase: str,
    pose: Dict[str, float],
) -> None:
    """Mutate ``current_state`` for one phase of a Newton sub-move.

    - ``phase == "ghost"``: update only ``tunables.nominal_pose`` (the
      planned target before/at start of move). The UI uses this to
      paint the dashed ghost outline.
    - ``phase == "physical"``: update both ``measurables.pose``
      (observed) and ``tunables.nominal_pose`` to match, and force
      ``presence = BREADBOARD`` (a successful sub-place puts the
      component on the table). The UI uses ``measurables.pose`` to
      paint the solid outline.

    Other ``phase`` values are silently ignored -- the strategy may
    fire callbacks the cloud-labs UI doesn't have a render path for
    yet, and we'd rather drop them than crash mid-optimization.
    Backfills missing ``tunables``/``measurables`` blocks with their
    defaults so a partial snapshot doesn't trip the assignment.
    """
    if phase not in ("ghost", "physical"):
        return
    with state_lock:
        comp_entry = (current_state.get("components") or {}).get(tag_id)
        if not comp_entry:
            return
        tun = comp_entry.setdefault("tunables", default_tunables())
        if phase == "ghost":
            tun["nominal_pose"] = dict(pose)
            tun["placement"] = {"mode": "NEWTON"}
        else:
            from lab_model.domain.component import set_reported_pose

            set_reported_pose(comp_entry, pose)
            set_presence_and_storage(
                comp_entry, PRESENCE_BREADBOARD, in_storage=False, slot=None
            )
            tun["nominal_pose"] = dict(pose)
            tun["placement"] = {"mode": "NEWTON"}
        current_state["last_updated"] = datetime.now().isoformat()
